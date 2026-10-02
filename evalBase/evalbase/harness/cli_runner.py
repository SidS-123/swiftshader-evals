"""Native Claude Code / Codex / OpenCode attempts driven through the MCP bridge.

The CLI owns its own agent loop and compaction; the harness owns the
workspace, the tools, the budget, the checkpoints and the grading. The
operator's login stays on the host: the sandbox never sees a credential, and
the CLI never sees the evaluation's repository (it runs in an empty temporary
directory and every built-in tool is disabled).

Two stop policies:

* `budget` (default) -- a final answer ends a CLI *segment*, not the attempt.
  While wall time remains the harness relaunches the CLI with a continuation
  prompt, and records how many final answers happened.
* `submit` -- the model decides when it is done. A final answer is answered
  once with a confirmation prompt; an explicit `SUBMISSION FINAL`, or a second
  exit without a single tool call, ends the attempt with `model_final`. The
  wall budget is still a hard cap (`budget_wall`).

A provider usage/rate limit is neither: the runner recognises it from the
CLI's own stream, waits for it to clear (polling, capped by
`--rate-limit-wait`) and relaunches. That waiting time is recorded in
`rate_limit_wait_seconds` and is never counted as solver time. A rejected
credential (401 / 403) ends the attempt at once rather than being retried.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from ..interfaces import Instance
from . import attempt as att
from . import opencode_boundary as ocb
from . import tools, workspace as ws
from .audit import audit_events, auth_failure_signal, rate_limit_signal
from .codex_boundary import BOUNDARY_VERSION, prepare_catalog
from .privacy import Redactor
from .tools import atomic_json

EXECUTABLE = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}
OPENCODE_SESSION_TITLE = "evalbase attempt"
OPENCODE_FALLBACK_BIN = Path.home() / ".opencode" / "bin" / "opencode"
OPENROUTER_KEY = re.compile(r"^sk-or-v1-[A-Za-z0-9_-]{32,}$")
MIN_SEGMENT_SECONDS = 20.0
MAX_SEGMENTS = 200
# The one string the model types to end a `submit` attempt. It is quoted in the
# task text, in the launch prompt and in the confirmation prompt.
SUBMIT_MARKER = ws.SUBMIT_MARKER
RATE_LIMIT_POLL_SECONDS = 60.0
RATE_LIMIT_BACKOFF_MAX = 900.0
AGENT_MCP = Path(__file__).resolve().parent / "agent_mcp.py"


def _help_text(executable, *argv) -> str:
    """OpenCode prints its help on stderr, not stdout; read both."""
    result = subprocess.run([executable, *argv], capture_output=True, text=True, timeout=60)
    return (result.stdout or "") + (result.stderr or "")


def opencode_credential(executable, model: str) -> str:
    """Name where OpenCode will find the provider credential. Never reads its value."""
    provider, _ = ocb.split_model(model)
    if provider == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
        if not OPENROUTER_KEY.match(os.environ["OPENROUTER_API_KEY"].strip()):
            raise RuntimeError(
                "OPENROUTER_API_KEY on this host is not an OpenRouter key (expected "
                "sk-or-v1- followed by at least 32 key characters). No attempt was "
                "started, and the value was neither logged nor sent anywhere.")
        return "OPENROUTER_API_KEY on the host"
    paths = subprocess.run([executable, "debug", "paths"], capture_output=True, text=True, timeout=60)
    store = None
    for line in (paths.stdout or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == "data":
            store = Path(parts[1].strip()) / "auth.json"
    if store is not None and store.is_file():
        try:
            if provider in (json.loads(store.read_text()) or {}):
                return f"OpenCode auth store ({provider})"
        except (OSError, ValueError, TypeError):
            pass
    hint = ("export OPENROUTER_API_KEY=sk-or-v1-... in the host shell, or run "
            "`opencode providers login` and choose OpenRouter"
            if provider == "openrouter" else
            f"run `opencode providers login` and choose the {provider} provider. Note that "
            "--model is an OpenCode <provider>/<model> id, so an OpenRouter slug needs the "
            "`openrouter/` prefix")
    raise RuntimeError(f"OpenCode has no {provider} credential on this host. To fix: {hint}.")


def preflight(harness: str, model: str | None = None) -> dict:
    name = EXECUTABLE[harness]
    executable = shutil.which(name)
    if not executable and harness == "opencode" and OPENCODE_FALLBACK_BIN.is_file():
        executable = str(OPENCODE_FALLBACK_BIN)
    if not executable:
        raise RuntimeError(f"{name} CLI is not installed on this host")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=60)
    if harness == "opencode":
        if not model:
            raise ValueError("the opencode preflight needs the requested model")
        if "--pure" not in _help_text(executable, "--help"):
            raise RuntimeError("this OpenCode build does not support --pure; upgrade before running attempts")
        run_help = _help_text(executable, "run", "--help")
        for flag in ("--format", "--agent", "--title", "--model", "--dir"):
            if flag not in run_help:
                raise RuntimeError(f"this OpenCode build does not support `run {flag}`; upgrade before running attempts")
        kind = opencode_credential(executable, model)
        provider, _ = ocb.split_model(model)
        catalog = subprocess.run([executable, "models", provider], capture_output=True, text=True, timeout=180)
        listed = (catalog.stdout or "").split()
        if catalog.returncode or not listed:
            raise RuntimeError(
                f"OpenCode cannot list the {provider} provider's models even though a "
                f"credential is configured ({kind}); it printed: "
                + ((catalog.stdout or "") + (catalog.stderr or "")).strip()[:200])
        if model not in listed:
            raise RuntimeError(
                f"{model} is not in OpenCode's {provider} model catalog ({len(listed)} "
                "models). Model ids are passed through verbatim and never substituted.")
        return {"executable": executable, "version": (version.stdout or "").strip(), "authentication": kind}
    auth = subprocess.run([executable, *(["login", "status"] if harness == "codex" else ["auth", "status"])],
                          capture_output=True, text=True, timeout=60)
    blob = (auth.stdout or "") + (auth.stderr or "")
    if harness == "codex":
        logged = auth.returncode == 0 and "Logged in" in blob
        kind = "ChatGPT subscription" if "ChatGPT" in blob else "CLI-managed authentication"
        help_result = subprocess.run([executable, "exec", "--help"], capture_output=True, text=True, timeout=60)
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"):
            if flag not in help_result.stdout:
                raise RuntimeError(f"this Codex build does not support {flag}; upgrade before running attempts")
    else:
        try:
            logged = bool(json.loads(auth.stdout).get("loggedIn"))
        except (ValueError, AttributeError):
            logged = "not logged in" not in blob.lower() and auth.returncode == 0
        kind = "CLI-managed authentication"
        help_result = subprocess.run([executable, "--help"], capture_output=True, text=True, timeout=60)
        for flag in ("--strict-mcp-config", "--setting-sources", "--allowedTools", "--permission-mode"):
            if flag not in help_result.stdout:
                raise RuntimeError(f"this Claude Code build does not support {flag}; upgrade before running attempts")
    if not logged:
        hint = "codex login" if harness == "codex" else "claude auth login"
        raise RuntimeError(f"{name} is not authenticated on this host. Run `{hint}` first.")
    return {"executable": executable, "version": (version.stdout or "").strip(), "authentication": kind}


def launch_command(instance: Instance, options, run_dir, control_dir, executable, *,
                   instructions=None, tool_timeout=3600, codex_catalog=None):
    """Pure construction, for inspection and tests. No token or credential arguments."""
    server = instance.task.mcp_server
    instructions = instructions if instructions is not None else instance.task.instructions
    run_dir, control_dir = Path(run_dir), Path(control_dir)
    bridge = {"command": sys.executable,
              "args": [str(AGENT_MCP), str(run_dir)],
              "env": {}, "cwd": str(control_dir)}
    if options.harness == "codex":
        cmd = [executable, "exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
               "--ephemeral", "--json", "--color", "never", "--sandbox", "read-only",
               "-C", str(control_dir), "-m", options.model]
        config = {
            "approval_policy": "never", "web_search": "disabled", "project_doc_max_bytes": 0,
            "model_catalog_json": str(codex_catalog or run_dir / "codex-models.private.json"),
            "tools.experimental_request_user_input.enabled": False,
            "tools.update_plan.enabled": False,
            "include_collaboration_mode_instructions": False,
            "developer_instructions": (
                f"Use only mcp__{server} tools. The task filesystem is /task inside the shell "
                "tool. No host tool, host path or external resource is permitted."),
            f"mcp_servers.{server}.command": bridge["command"],
            f"mcp_servers.{server}.args": bridge["args"],
            f"mcp_servers.{server}.cwd": bridge["cwd"],
            f"mcp_servers.{server}.required": True,
            f"mcp_servers.{server}.startup_timeout_sec": 120,
            f"mcp_servers.{server}.tool_timeout_sec": tool_timeout,
            f"mcp_servers.{server}.enabled_tools": list(tools.TOOL_NAMES),
            f"mcp_servers.{server}.default_tools_approval_mode": "approve",
            "suppress_unstable_features_warning": True,
        }
        if options.reasoning:
            config["model_reasoning_effort"] = options.reasoning
        disabled = ("shell_tool", "unified_exec", "view_image", "multi_agent", "multi_agent_v2",
                    "apps", "plugins", "image_generation", "browser_use", "browser_use_external",
                    "computer_use", "hooks", "shell_snapshot", "workspace_dependencies",
                    "skill_mcp_dependency_install", "skill_search", "in_app_browser",
                    "in_app_local_automation", "goals", "sleep_tool", "memories")
        config.update({"features." + name: False for name in disabled})
        config["features.skip_host_skill_discovery"] = True
        for key, value in config.items():
            cmd += ["-c", key + "=" + json.dumps(value)]
        return cmd + ["-"]
    if options.harness == "opencode":
        # OpenCode takes no per-tool flags: one configuration document is the
        # whole boundary, written here, checked here, handed over as OPENCODE_CONFIG.
        document = ocb.config_document(model=options.model, server=server, agent=server,
                                       bridge=bridge, instructions=instructions,
                                       tool_timeout=tool_timeout, reasoning=options.reasoning)
        ocb.check_config(document, server=server, agent=server)
        atomic_json(opencode_config_path(run_dir), document, mode=0o600)
        (run_dir / "opencode-config.d").mkdir(parents=True, exist_ok=True)
        return [executable, "run", "--pure", "--print-logs", "--log-level", "ERROR",
                "--format", "json", "--agent", server, "--model", options.model,
                "--title", OPENCODE_SESSION_TITLE, "--dir", str(control_dir)]
    config_path = run_dir / "claude-mcp.private.json"
    atomic_json(config_path, {"mcpServers": {server: bridge}})
    cmd = [executable, "--print", "--verbose", "--output-format", "stream-json",
           "--model", options.model, "--tools", "", "--strict-mcp-config",
           "--mcp-config", str(config_path), "--restricted", "--disable-slash-commands",
           "--setting-sources", "", "--allowedTools", f"mcp__{server}__*",
           "--permission-mode", "dontAsk", "--no-session-persistence",
           "--system-prompt", instructions]
    if options.reasoning:
        cmd += ["--effort", options.reasoning]
    return cmd


def opencode_config_path(run_dir) -> Path:
    return Path(run_dir) / "opencode-config.private.json"


def launch_env(options, control_dir, run_dir) -> dict:
    """The environment one CLI segment is launched with. No secret is added here.

    claude-code and codex authenticate through their own login, so the
    OpenRouter key is removed outright. opencode authenticates *as* the
    operator's OpenRouter key, so it is left in place -- the CLI is a host
    process, and the sandbox never sees the host environment.
    """
    env = dict(os.environ)
    if options.harness != "opencode":
        env.pop("OPENROUTER_API_KEY", None)
        return env
    env.update(ocb.ISOLATION_ENV)
    env["OPENCODE_CONFIG"] = str(opencode_config_path(run_dir))
    env["OPENCODE_CONFIG_DIR"] = str(Path(run_dir) / "opencode-config.d")
    env["PWD"] = str(control_dir)
    for name in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_AUTH_CONTENT", "OPENCODE_PERMISSION"):
        env.pop(name, None)
    return env


class CLIAttempt:
    def __init__(self, instance: Instance, options, run_dir, *, command_override=None, resume=False):
        options.validate()
        if options.harness not in EXECUTABLE:
            raise ValueError("CLIAttempt handles the CLI harnesses only: " + ", ".join(EXECUTABLE))
        self.instance = instance
        self.options = options
        self.run_dir = Path(run_dir).resolve()
        self.command_override = command_override
        self.simulated = command_override is not None
        self.resume = resume
        self.redactor = Redactor(root=instance.root)
        self.base_elapsed = 0.0
        self.base_rate_limit_wait = 0.0
        self.paused_seconds = 0.0
        self.session_paused = 0.0
        self.served_models = []

    # -------------------------------------------------------------- helpers
    def _config(self):
        # `owner` is what the MCP bridge labels the sandbox and every container
        # it starts with; the two processes must agree on it, so it is written
        # down here rather than derived twice.
        return {"instance": str(self.instance.root / "instance.py"),
                "max_seconds": int(self.options.budget_seconds),
                "checkpoint_seconds": int(self.options.checkpoint_seconds),
                "solver_image": self.instance.solver_image, "cpus": self.options.cpus,
                "memory": self.options.memory, "sandbox": self.options.sandbox,
                "owner": self.run_dir.name,
                "corpus_public": str(self.instance.corpus_public), "corpus_assets": str(self.instance.assets),
                "refcache": str(self.instance.refcache), "options": asdict(self.options)}

    def _save(self, manifest):
        atomic_json(self.run_dir / "attempt.json", self.redactor.clean(manifest), mode=0o644)

    def _prompt(self, remaining: float, segment: int, kind: str) -> str:
        """The text handed to one CLI session. `kind` names the situation."""
        instructions = self.instance.task.instructions
        left = f"{int(remaining)} seconds of wall clock remain"
        if kind == "initial":
            if self.options.stop_policy == "submit":
                policy = (
                    f"Stopping policy: submit — you decide when the work is done. Work until "
                    f"you judge it complete, then stop calling tools; you will be asked to "
                    f"confirm once and you end the attempt by replying with the single line "
                    f"{SUBMIT_MARKER}. {left.capitalize()} as a hard cap, and your source is "
                    f"checkpointed automatically throughout.")
            else:
                policy = (
                    f"Stopping policy: budget — a final answer does not end the attempt, so keep "
                    f"improving until the budget ends. {left.capitalize()}.")
            return (instructions + "\n" + policy + " All files live in /task inside the shell "
                    "tool, not in this host directory. Start by reading /task/TASK.md.\n")
        if kind == "confirmation":
            return (instructions + "\nYour last session ended without calling a single tool, "
                    "which this harness reads as an offer to submit what you have. "
                    f"{left.capitalize()} and your /task workspace, build outputs and NOTES.md "
                    "are exactly as you left them.\n\n"
                    "Decide now, and do exactly one of these two things:\n"
                    "1. If anything in /task/SPEC.md is still unimplemented, any public case still "
                    "scores below what you could reach, or any performance ratio is still weak — "
                    "keep working. Start calling tools again in this session; ignore everything "
                    "below.\n"
                    f"2. If you judge the implementation complete and want it graded as it "
                    f"stands, reply with the single line {SUBMIT_MARKER} and nothing else.\n\n"
                    "Check /task/NOTES.md and re-run grade_dev before you decide. A reply that "
                    "neither calls a tool nor states that line ends the attempt anyway.\n")
        if kind == "rate_limit_resume":
            return (instructions + "\nThis attempt was paused because the provider reported a "
                    "usage limit; the pause is over and it did not consume your time budget. "
                    f"{left.capitalize()} and your /task workspace, build outputs and NOTES.md "
                    "are exactly as you left them. Re-read /task/NOTES.md first, then keep "
                    "improving the implementation.\n")
        return (instructions + "\nThis attempt is continuing after an earlier session ended. "
                f"{left.capitalize()} and your /task workspace, build outputs and NOTES.md are "
                "exactly as you left them. Re-read /task/NOTES.md first, then keep improving the "
                "implementation.\n")

    # ------------------------------------------------------------ timekeeping
    def _elapsed(self, started: float) -> float:
        """Solver seconds so far: wall clock minus time spent waiting on a limit."""
        return self.base_elapsed + (time.monotonic() - started) - self.paused_seconds

    def _tool_call_total(self) -> int:
        state_path = self.run_dir / "tools-state.json"
        if not state_path.exists():
            return 0
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            return 0
        return int((state.get("manifest") or {}).get("calls", 0) or 0)

    # ------------------------------------------------------------ rate limits
    def _wait_out_rate_limit(self, hit: dict, manifest: dict, probe: int) -> bool:
        """Sleep until the provider limit should have cleared. False = give up."""
        cap = self.options.rate_limit_wait_seconds
        already = float(manifest["rate_limit_wait_seconds"])
        if already >= cap:
            return False
        resets_at = hit.get("resets_at")
        if isinstance(resets_at, (int, float)) and resets_at > time.time():
            target = float(resets_at) - time.time() + 30.0
        else:
            target = min(RATE_LIMIT_BACKOFF_MAX, RATE_LIMIT_POLL_SECONDS * (2 ** min(probe, 4)))
        target = min(target, cap - already)
        if target <= 0:
            return False
        record = {"segment": len(manifest["segments"]), "signal": hit.get("signal"),
                  "kind": hit.get("kind"), "resets_at": resets_at,
                  "resets_at_iso": att.iso(resets_at) if isinstance(resets_at, (int, float)) else None,
                  "planned_seconds": round(target, 1), "started": att.iso(),
                  "evidence": self.redactor.text(str(hit.get("evidence") or ""))[:400]}
        manifest["rate_limit_waits"].append(record)
        self._save(manifest)
        print(f"rate limit reported by the CLI ({hit.get('signal')}/{hit.get('kind')}); waiting "
              f"{int(target)}s before relaunching. This does not consume the solver budget.",
              file=sys.stderr, flush=True)
        began = time.monotonic()
        deadline = began + target
        while time.monotonic() < deadline:
            time.sleep(min(RATE_LIMIT_POLL_SECONDS, max(0.1, deadline - time.monotonic())))
            waited = time.monotonic() - began
            record["waited_seconds"] = round(waited, 1)
            self.paused_seconds = self.session_paused + waited
            manifest["rate_limit_wait_seconds"] = round(self.base_rate_limit_wait + self.paused_seconds, 1)
            self._save(manifest)
        self.session_paused = self.paused_seconds
        record["ended"] = att.iso()
        return True

    # ------------------------------------------------------------------ run
    def run(self):
        options = self.options
        out = self.run_dir
        instance = self.instance
        info = ({"executable": "simulated-client", "version": "smoke", "authentication": "none"}
                if self.simulated else preflight(options.harness, options.model))
        previous = {}
        if not self.resume:
            out.mkdir(parents=True, exist_ok=False)
            ws.populate(instance, out / "workspace", stop_policy=options.stop_policy,
                        budget_seconds=options.budget_seconds,
                        checkpoint_seconds=options.checkpoint_seconds)
        else:
            previous = json.loads((out / "attempt.json").read_text())
            if previous.get("status") == "complete":
                raise ValueError("completed attempts cannot be resumed as new trials")
            if previous.get("harness") != options.harness or previous.get("model_requested") != options.model:
                raise ValueError("resume must preserve the original harness and model")
            if previous.get("task_version", {}).get("corpus", {}).get("sha256") \
                    != ws.corpus_digest(instance.corpus_public, instance.assets)["sha256"]:
                raise ValueError("the corpus changed; this is a different task version")
            for name in ("submission", "grade"):
                if (out / name).exists():
                    raise ValueError(f"{name}/ already exists: this attempt was already exported")
        atomic_json(out / "agent-config.json", self._config(), mode=0o600)
        manifest = att.base_manifest(instance, options, simulated=self.simulated, name=out.name)
        manifest.update(cli_version=info["version"], authentication=info["authentication"],
                        context_semantics="native CLI agent loop and compaction; no harness substitution",
                        cost_semantics=("CLI/subscription-managed; token usage is reported when the CLI "
                                        "exposes it and never invented"),
                        segments=previous.get("segments", []))
        self.base_elapsed = float(previous.get("solver_seconds", 0.0))
        self.base_rate_limit_wait = float(previous.get("rate_limit_wait_seconds", 0.0))
        self.paused_seconds = 0.0
        self.session_paused = 0.0
        self.served_models = list(previous.get("models_served") or [])
        manifest.update(started=previous.get("started", manifest["started"]),
                        started_unix=previous.get("started_unix", manifest["started_unix"]),
                        resume_count=previous.get("resume_count", 0) + (1 if self.resume else 0),
                        model_final_events=previous.get("model_final_events", 0),
                        rate_limit_wait_seconds=self.base_rate_limit_wait,
                        rate_limit_waits=previous.get("rate_limit_waits", []),
                        submission=previous.get("submission", manifest["submission"]))
        self._save(manifest)

        if options.harness in ("codex", "opencode") and not self.simulated:
            try:
                if options.harness == "codex":
                    catalog = out / "codex-models.private.json"
                    if catalog.exists():
                        catalog.unlink()
                    record = prepare_catalog(info["executable"], options.model, catalog)
                    version = BOUNDARY_VERSION
                else:
                    with tempfile.TemporaryDirectory(prefix="evalbase-cli-") as directory:
                        control = Path(directory)
                        launch_command(instance, options, out, control, info["executable"])
                        record = ocb.prepare(info["executable"], opencode_config_path(out),
                                             cwd=control, env=launch_env(options, control, out),
                                             server=instance.task.mcp_server,
                                             agent=instance.task.mcp_server,
                                             cli_version=info["version"])
                    version = ocb.BOUNDARY_VERSION
                atomic_json(out / "boundary-preflight.json", record, mode=0o600)
                manifest.update(runner_boundary_version=version, tool_boundary_preflight_passed=True)
            except Exception as exc:
                manifest.update(status="infrastructure_error", stop_reason="infrastructure",
                                measurement_valid=False, tool_boundary_preflight_passed=False,
                                error=self.redactor.text(exc))
                self._save(manifest)
                raise

        raw = att.secure(out / "events.private.jsonl")
        errlog = att.secure(out / "stderr.private.log")
        started = time.monotonic()
        interrupted = False
        segment = 0
        consecutive_fast_failures = 0
        manifest["status"] = "running"
        self._save(manifest)
        total_wall_cap = options.budget_seconds + options.rate_limit_wait_seconds
        next_kind = "initial"
        rate_limit_probes = 0
        try:
            while True:
                remaining = options.budget_seconds - self._elapsed(started)
                if remaining <= MIN_SEGMENT_SECONDS:
                    manifest["stop_reason"] = manifest.get("stop_reason") or "budget_wall"
                    break
                if time.monotonic() - started + self.base_elapsed >= total_wall_cap:
                    manifest["stop_reason"] = "budget_wall"
                    manifest["error"] = "the total wall cap (solver budget + rate-limit wait) elapsed"
                    break
                if segment >= MAX_SEGMENTS:
                    manifest["stop_reason"] = "context_failure"
                    break
                segment += 1
                kind, next_kind = next_kind, "continuation"
                record = self._segment(segment, remaining, info, manifest, raw, errlog, started, kind=kind)
                manifest["segments"].append(record)
                self._save(manifest)
                if record.get("stop_requested"):
                    manifest["stop_reason"] = "operator_interrupt"
                    interrupted = True
                    break
                if record.get("rate_limit"):
                    rate_limit_probes += 1
                    if not self._wait_out_rate_limit(record["rate_limit"], manifest, rate_limit_probes):
                        manifest["stop_reason"] = "rate_limited"
                        manifest["error"] = (
                            "the provider usage limit did not clear within --rate-limit-wait "
                            f"({options.rate_limit_wait_hours} h)")
                        break
                    next_kind = "rate_limit_resume"
                    continue
                rate_limit_probes = 0
                if record.get("auth_failure"):
                    manifest["stop_reason"] = "infrastructure"
                    manifest["error"] = (
                        "the provider rejected the credential (HTTP "
                        f"{record['auth_failure'].get('status')}); check the key or the CLI login on the host")
                    break
                if record["exit_code"] == 0:
                    manifest["model_final_events"] += 1
                    consecutive_fast_failures = 0
                    if options.stop_policy == "submit":
                        if kind != "confirmation":
                            manifest["submission"]["confirmations"] += 1
                            next_kind = "confirmation"
                            continue
                        if record["submission_final"] or record["tool_calls"] == 0:
                            manifest["submission"].update(
                                declared_final=bool(record["submission_final"]),
                                declared_at_segment=segment,
                                ended_on=("explicit " + SUBMIT_MARKER if record["submission_final"]
                                          else "a second session with no tool call"))
                            manifest["stop_reason"] = "model_final"
                            break
                        manifest["submission"]["confirmations"] += 1
                        next_kind = "confirmation"
                        continue
                elif record["seconds"] < MIN_SEGMENT_SECONDS:
                    consecutive_fast_failures += 1
                    if consecutive_fast_failures >= 3:
                        manifest["stop_reason"] = "infrastructure"
                        manifest["error"] = "the CLI exited immediately three times in a row"
                        break
                else:
                    consecutive_fast_failures = 0
            if not manifest["stop_reason"]:
                manifest["stop_reason"] = "budget_wall"
        except KeyboardInterrupt:
            interrupted = True
            manifest["stop_reason"] = "operator_interrupt"
        finally:
            # This attempt's containers only: the sandbox and anything its tools
            # started. An attempt running beside this one keeps its own.
            if options.sandbox != "none":
                tools.cleanup_containers(manifest["owner"])
        manifest["solver_seconds"] = round(self._elapsed(started), 3)
        manifest["wall_seconds"] = round(self.base_elapsed + self.base_rate_limit_wait
                                         + time.monotonic() - started, 3)
        manifest["rate_limit_wait_seconds"] = round(self.base_rate_limit_wait + self.paused_seconds, 1)
        manifest["ended"] = att.iso()
        manifest["ended_unix"] = time.time()

        state_path = out / "tools-state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        manifest["tool_calls"] = (state.get("manifest") or {}).get("tool_calls", {})
        manifest["tool_call_total"] = (state.get("manifest") or {}).get("calls", 0)
        manifest["checkpoints"] = state.get("checkpoints", [])
        manifest["infrastructure_incidents"] = (
            list(manifest.get("infrastructure_incidents") or [])
            + list((state.get("manifest") or {}).get("infrastructure_incidents") or []))

        audit = audit_events(raw, options.harness, errlog, server=instance.task.mcp_server)
        atomic_json(out / "audit.json", self.redactor.clean(audit), mode=0o644)
        manifest["audit"] = {k: audit[k] for k in ("valid_tool_boundary", "violations",
                                                   "boundary_evidence", "errors", "events",
                                                   "mcp_tool_calls", "rate_limit_events")}
        manifest["models_served"] = (audit.get("served_models") or self.served_models
                                     or manifest["models_served"])
        totals = audit.get("usage_totals") or {}
        cost = audit.get("cost") or {}
        manifest["usage"] = {"input_tokens": totals.get("input_tokens"),
                             "output_tokens": totals.get("output_tokens"),
                             "cache_creation_input_tokens": totals.get("cache_creation_input_tokens"),
                             "cache_read_input_tokens": totals.get("cache_read_input_tokens"),
                             "cost_usd": cost.get("total_usd"),
                             "cost_reported_by_cli": bool(cost.get("reported")),
                             "cost_basis": cost.get("basis"),
                             "cost_is_billed_charge": bool(cost.get("is_billed_charge")),
                             "cost_note": cost.get("note"),
                             "source": audit["usage_source"], "cli_reported": audit["usage"]}
        for original, destination in ((raw, out / "transcript.jsonl"), (errlog, out / "stderr.log")):
            with original.open(errors="replace") as src, destination.open("w") as dst:
                for line in src:
                    dst.write(self.redactor.text(line))
        if interrupted:
            # An operator pause: the budget used is recorded above. No final checkpoint, export or
            # grade, so `resume` can continue the attempt (it refuses an exported one).
            manifest["status"] = "interrupted"
            self._save(manifest)
            return manifest
        manifest["status"] = "solver_finished"
        self._save(manifest)

        # A final hashed checkpoint of exactly what will be graded.
        try:
            final = ws.checkpoint(instance, out / "workspace", out / "checkpoints" / "final",
                                  index=len(manifest["checkpoints"]) + 1,
                                  note="Final source at the end of the attempt",
                                  elapsed=manifest["solver_seconds"])
            manifest["checkpoints"].append({"index": final["index"], "name": "final",
                                            "source_sha256": final["source_sha256"],
                                            "archive_sha256": final["archive_sha256"],
                                            "elapsed_seconds": final["elapsed_seconds"],
                                            "note": final["note"]})
        except (ValueError, OSError) as exc:
            manifest["final_checkpoint_error"] = self.redactor.text(exc)
        self._save(manifest)

        att.export_and_grade(instance, out, out / "workspace", options, manifest, self.redactor,
                             save=self._save)
        att.apply_validity(manifest, bool(
            audit["valid_tool_boundary"] and manifest["tool_call_total"] > 0
            and manifest["stop_reason"] in ("budget_wall", "model_final")))
        if interrupted:
            manifest["status"] = "interrupted"
        elif not manifest["measurement_valid"]:
            manifest["status"] = "invalid_measurement"
        self._save(manifest)
        return manifest

    # ------------------------------------------------------- opencode extras
    def _opencode_served(self, events_text: str, info: dict, env: dict) -> list:
        """Which model actually answered, from OpenCode's own session export (best effort)."""
        session = None
        for line in (events_text or "").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("sessionID"), str):
                session = event["sessionID"]
                break
        if not session:
            return []
        try:
            result = subprocess.run([info["executable"], "export", session],
                                    capture_output=True, text=True, timeout=120,
                                    cwd=str(self.run_dir), env={**env, "PWD": str(self.run_dir)})
            data = json.loads(result.stdout) if result.returncode == 0 else {}
        except (OSError, ValueError, subprocess.SubprocessError):
            return []
        served = []
        for message in (data.get("messages") or []) if isinstance(data, dict) else []:
            entry = (message or {}).get("info") or {}
            provider, model = entry.get("providerID"), entry.get("modelID")
            if provider and model and f"{provider}/{model}" not in served:
                served.append(f"{provider}/{model}")
        return served

    # -------------------------------------------------------------- segment
    def _segment(self, segment, remaining, info, manifest, raw, errlog, started, *, kind="continuation"):
        options = self.options
        t0 = time.monotonic()
        proc = None
        stop_requested = False
        events_before = raw.stat().st_size if raw.exists() else 0
        stderr_before = errlog.stat().st_size if errlog.exists() else 0
        calls_before = self._tool_call_total()
        with tempfile.TemporaryDirectory(prefix="evalbase-cli-") as directory, \
                raw.open("ab") as log, errlog.open("ab") as err:
            control = Path(directory)
            real = launch_command(self.instance, options, self.run_dir, control, info["executable"],
                                  tool_timeout=int(min(7200, max(600, remaining))))
            command = (self.command_override + real[1:]) if self.command_override else real
            atomic_json(self.run_dir / "launch.private.json",
                        {"argv": command, "cwd": str(control), "segment": segment}, mode=0o600)
            prompt = self._prompt(remaining, segment, kind)
            env = launch_env(options, control, self.run_dir)
            proc = subprocess.Popen(command, cwd=control, stdin=subprocess.PIPE, stdout=log,
                                    stderr=err, start_new_session=True, env=env)
            try:
                proc.stdin.write(prompt.encode())
                proc.stdin.close()
                while proc.poll() is None:
                    if self._elapsed(started) >= options.budget_seconds:
                        manifest["stop_reason"] = "budget_wall"
                        break
                    time.sleep(0.25)
            except KeyboardInterrupt:
                stop_requested = True
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                        proc.wait(timeout=20)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait(timeout=10)
                        except (ProcessLookupError, subprocess.TimeoutExpired):
                            pass
        # The bridge owns the sandbox for its own lifetime only.
        state_path = self.run_dir / "tools-state.json"
        if state_path.exists() and options.sandbox != "none":
            name = (json.loads(state_path.read_text()).get("manifest") or {}).get("sandbox_name")
            if name:
                tools.docker("rm", "-f", name, check=False)
        events_text = _slice(raw, events_before)
        stderr_text = _slice(errlog, stderr_before)
        hit = rate_limit_signal(events_text, stderr_text)
        final_text = _final_text(events_text, options.harness)
        auth_failure = None
        if options.harness == "opencode" and not self.simulated:
            auth_failure = auth_failure_signal(events_text)
            for served in self._opencode_served(events_text, info, env):
                if served not in self.served_models:
                    self.served_models.append(served)
        return {"segment": segment, "exit_code": proc.returncode if proc else None,
                "auth_failure": auth_failure,
                "seconds": round(time.monotonic() - t0, 3), "prompt_kind": kind,
                "stop_requested": stop_requested,
                "tool_calls": self._tool_call_total() - calls_before,
                "submission_final": _declares_final(final_text),
                "final_text": self.redactor.text(final_text)[:2000] or None,
                "rate_limit": ({**hit, "evidence": self.redactor.text(str(hit.get("evidence") or ""))[:400]}
                               if hit else None)}


# --------------------------------------------------------------- segment reads

def _slice(path: Path, offset: int) -> str:
    """The bytes one segment appended to a shared log, as text."""
    if not Path(path).exists():
        return ""
    with Path(path).open("rb") as handle:
        handle.seek(offset)
        return handle.read().decode("utf-8", errors="replace")


def _final_text(events_text: str, harness: str) -> str:
    """The last thing the model said in a segment, from the CLI's own stream."""
    text = ""
    for line in events_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if harness == "opencode":
            if event.get("type") == "text":
                part = event.get("part")
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text = part["text"]
            continue
        if harness == "codex":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" \
                    and isinstance(item.get("text"), str):
                text = item["text"]
            continue
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            text = event["result"]
        elif event.get("type") == "assistant":
            message = event.get("message")
            blocks = (message or {}).get("content") if isinstance(message, dict) else None
            for block in blocks if isinstance(blocks, list) else []:
                if isinstance(block, dict) and block.get("type") == "text" \
                        and isinstance(block.get("text"), str):
                    text = block["text"]
    return text


def _declares_final(text: str) -> bool:
    """True when the model used the exact submission marker (not merely quoted it)."""
    for line in (text or "").splitlines():
        stripped = line.strip().strip("*`_#>-. ")
        if stripped.upper() == SUBMIT_MARKER:
            return True
    return False
