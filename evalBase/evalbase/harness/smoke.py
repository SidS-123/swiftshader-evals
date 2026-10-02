"""No-key smoke: a scripted fake model drives the real tools and the real grader.

The script is built
from the instance's `TaskSpec.smoke`: what the fake model writes, which case
it runs through the oracle, which public case it runs the driver on.

The same script is used by all four harnesses. For `openrouter` a fake
provider object replaces the HTTP call; for `claude-code`, `codex` and
`opencode` a stub executable (`stub_cli.py`) speaks the same launch protocol
as the real CLI, spawns the real MCP bridge over stdio and calls the same
tools. Nothing here contacts a provider, and the report is labeled
`simulated_model: true`.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

from ..grader import jsonio
from ..interfaces import Instance
from . import attempt as att
from . import workspace as ws

SMOKE_MODEL = "simulated/no-inference"
SMOKE_BUDGET_SECONDS = 120
SMOKE_MARKER_ENV = "EVALBASE_SMOKE_MARKER"
SMOKE_SANDBOX_ENV = "EVALBASE_SMOKE_SANDBOX"

DOCKER_ISOLATION = r"""set -e
test ! -e /var/run/docker.sock
test ! -e /project && test ! -e /corpus && test ! -e /grader
test ! -e /task/dev/hidden && test ! -e /task/corpus
test -z "$OPENROUTER_API_KEY" && test -z "$ANTHROPIC_API_KEY" && test -z "$OPENAI_API_KEY"
test "$(id -u)" -ne 0
python3 -c 'import socket; s=socket.socket(); s.settimeout(2); assert s.connect_ex(("1.1.1.1",443)) != 0'
"""


def _write_files(sources: dict[str, str]) -> str:
    lines = ["set -e"]
    for rel, text in sources.items():
        parent = str(Path(rel).parent)
        if parent not in (".", ""):
            lines.append(f"mkdir -p {shlex.quote(parent)}")
        lines.append(f"cat > {shlex.quote(rel)} <<'EVALBASE_EOF'\n{text}\nEVALBASE_EOF")
    return "\n".join(lines) + "\n"


def build_script(instance: Instance, sandbox: str = "docker") -> list:
    """(tool, arguments, assertion) triples the fake model plays, in order."""
    smoke = instance.task.smoke
    if smoke is None:
        raise ValueError(f"instance {instance.name} declares no smoke script (TaskSpec.smoke)")
    task = instance.task
    isolation = (DOCKER_ISOLATION if sandbox == "docker" else "set -e\n")
    if sandbox == "docker" and task.isolation_check:
        # the instance's own image checks (TaskSpec.isolation_check), run as a script so its
        # exit status decides; documented on the field, previously never run
        isolation += ("cat > /tmp/evalbase_isolation.sh <<'EVALBASE_EOF'\n" + task.isolation_check
                      + "\nEVALBASE_EOF\nbash /tmp/evalbase_isolation.sh\n")
    isolation += _write_files(smoke.sources) + "printf 'isolation-ok\\n'\n"
    build = f"{task.build_command} 2>&1 | tail -3; test -f {shlex.quote('/task/' + task.artifact)} && echo artifact-present"
    write_case = ("mkdir -p mine && cat > mine/tiny.json <<'EVALBASE_EOF'\n"
                  + json.dumps(smoke.case, indent=1) + "\nEVALBASE_EOF\nls -l mine/tiny.json\n")
    notes = ("cat > NOTES.md <<'EVALBASE_EOF'\n# Notes (no-key smoke)\n\nA deliberately incomplete "
             "candidate; this run exercises the tool path, not the task.\nEVALBASE_EOF\necho notes-written\n")
    channel = instance.scorer.primary_channel
    driver_ok = (lambda r: r["exit_code"] != 0) if smoke.driver_fails else (lambda r: r["exit_code"] == 0)
    return [
        ("shell", {"command": isolation, "timeout_seconds": 120},
         lambda r: r["exit_code"] == 0 and "isolation-ok" in r["output"]),
        ("shell", {"command": build, "timeout_seconds": 300},
         lambda r: r["exit_code"] == 0 and "artifact-present" in r["output"]),
        ("shell", {"command": write_case, "timeout_seconds": 60}, lambda r: r["exit_code"] == 0),
        ("oracle", {"case_path": "mine/tiny.json", "outdir": "out/oracle"},
         lambda r: r["exit"] == "ok" and any(channel in str(e.get("files", {})) or True
                                             for e in r.get("events", [])) and r["files"]),
        ("driver", {"case_path": f"dev/cases/{smoke.public_case}.json", "outdir": "out/mine"}, driver_ok),
        ("grade_dev", {"names": [smoke.public_case]},
         lambda r: r["aggregate"]["overall"] <= smoke.max_overall),
        ("shell", {"command": notes, "timeout_seconds": 60}, lambda r: r["exit_code"] == 0),
        ("checkpoint", {"note": "no-key smoke checkpoint"}, lambda r: bool(r["source_sha256"])),
    ]


def check(script, index: int, result) -> None:
    name, _, assertion = script[index]
    if isinstance(result, dict) and "tool_error" in result:
        raise AssertionError(f"smoke tool {name} failed: {result['tool_error']}")
    if not assertion(result):
        raise AssertionError(f"smoke tool {name} returned an unexpected result: "
                             f"{jsonio.dumps(result)[:800]}")


# ----------------------------------------------------------------- provider

class SimulatedProvider:
    """A fake OpenRouter endpoint: no key, no network, provider-shaped replies."""

    def __init__(self, runner_ref, script):
        self.index = 0
        self.runner_ref = runner_ref
        self.script = script

    def __call__(self, payload, key, timeout):
        if key:
            raise AssertionError("the simulated provider must never receive an API key")
        results = [json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool"]
        if results:
            check(self.script, self.index - 1, results[-1])
        if self.index >= len(self.script):
            # Script finished: report a final answer and let the wall budget end
            # the attempt, exactly as the budget stop policy prescribes.
            runner = self.runner_ref[0]
            runner.base_elapsed += runner.options.budget_seconds
            return self._response({"role": "assistant", "content":
                                   "Smoke script complete; the candidate is intentionally incomplete."})
        name, arguments, _ = self.script[self.index]
        self.index += 1
        return self._response({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"smoke-{self.index}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments)}}]})

    @staticmethod
    def _response(message):
        return {"model": SMOKE_MODEL, "provider": "local-test-double",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0},
                "choices": [{"finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                             "message": message}]}


# -------------------------------------------------------------------- entry

def smoke(instance: Instance, harness: str, out: str | Path, *, budget_seconds: int = SMOKE_BUDGET_SECONDS,
          keep: bool = False, sandbox: str = "docker") -> dict:
    out = Path(out)
    if out.exists():
        if not keep:
            ws.remove_tree(out, image=instance.solver_image)
        else:
            raise ValueError(f"{out} already exists")
    script = build_script(instance, sandbox)
    options = att.Options(harness=harness, model=SMOKE_MODEL,
                          budget_hours=budget_seconds / 3600.0,
                          checkpoint_minutes=budget_seconds / 120.0, sandbox=sandbox)
    t0 = time.time()
    if harness == "openrouter":
        from .openrouter import Runner
        holder: list = []
        runner = Runner(instance, options, out, request_fn=SimulatedProvider(holder, script))
        holder.append(runner)
        manifest = runner.run()
    else:
        from .cli_runner import CLIAttempt
        os.environ[SMOKE_MARKER_ENV] = str(out / "smoke-segment.marker")
        os.environ[SMOKE_SANDBOX_ENV] = sandbox
        os.environ["EVALBASE_INSTANCE"] = str(instance.root / "instance.py")
        command = [sys.executable, str(Path(__file__).resolve().parent / "stub_cli.py"), "--harness", harness]
        manifest = CLIAttempt(instance, options, out, command_override=command).run()
        failures = json.loads((out / "smoke-failures.json").read_text()) \
            if (out / "smoke-failures.json").is_file() else ["the stub CLI never ran the script"]
        assert not failures, f"scripted tool assertions failed: {failures}"
    manifest["smoke_seconds"] = round(time.time() - t0, 1)

    assert manifest["simulated_model"] is True, "the smoke report must be labeled simulated_model"
    assert manifest["grade"] is not None, "the exported artifact was not graded"
    assert manifest["final_source_sha256"], "no immutable source export"
    assert manifest["checkpoints"], "no checkpoint was recorded"
    assert manifest["grade"]["overall"] <= instance.task.smoke.max_overall, \
        f"the smoke candidate must score near zero, got {manifest['grade']['overall']}"
    first = next(iter(instance.task.smoke.sources))
    source = out / "submission" / first
    assert source.is_file() and b"no-key smoke agent" in source.read_bytes(), \
        "the exported source is not the source the fake model wrote"
    if harness == "openrouter":
        assert manifest["usage"]["cost_usd"] == 0.0 and manifest["requests"] >= len(script)
        assert manifest["stop_reason"] == "budget_wall"
    else:
        assert manifest["audit"]["valid_tool_boundary"], f"audit rejected the run: {manifest['audit']}"
        assert manifest["tool_call_total"] >= len(script)
    print(json.dumps({"harness": harness, "simulated_model": True,
                      "stop_reason": manifest["stop_reason"],
                      "tool_calls": manifest.get("tool_calls"),
                      "checkpoints": len(manifest["checkpoints"]),
                      "final_source_sha256": manifest["final_source_sha256"][:16] + "...",
                      "graded_corpus": manifest.get("graded_corpus"),
                      "grade": manifest["grade"], "seconds": manifest["smoke_seconds"]}, indent=1))
    print(f"NO-KEY SMOKE PASSED ({harness}): real tools, real oracle, real grader; "
          f"the incomplete candidate scored {manifest['grade']['overall']:.4f}.", flush=True)
    return manifest
