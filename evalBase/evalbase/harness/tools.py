"""The five solver tools and the sandbox they run in.

Parametrised by the instance. The tool *names* are fixed -- shell, oracle, driver, grade_dev,
checkpoint -- and their descriptions come from `TaskSpec.tool_descriptions`.

Boundaries enforced here, not by convention:

* `shell` runs only inside the solver sandbox, which has no network, no host
  mount other than the workspace, no credentials and an unprivileged user.
* `oracle` runs the instance's driver against the *oracle* on a case the
  agent wrote. The case and every asset it names must resolve inside the
  workspace; hidden cases and the reference cache are not reachable by name.
* `driver` runs the instance's driver command inside the sandbox against the
  candidate artifact -- candidate code only.
* `grade_dev` grades the candidate with the real grader against the public
  corpus only; the hidden corpus and its reference cache are never passed in.
* `checkpoint` writes an immutable, hashed snapshot outside the workspace.

`SolverSandbox` is the Docker sandbox every real instance uses.
`LocalSandbox` runs the shell on the host in the workspace directory and
exists for tests and for instances whose driver needs nothing a host lacks
(the toy); it is selected with `--sandbox none` and is never the default.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

from ..grader import containers
from ..interfaces import Instance
from . import workspace as ws

SHELL_TIMEOUT_MAX = 600
SHELL_OUTPUT_LIMIT = 32 * 1024
ORACLE_CASE_BYTES = 8 * 1024 * 1024
ORACLE_MAX_SNAPSHOTS = 64
ORACLE_ASSET_BYTES = 256 * 1024 * 1024
ORACLE_TIMEOUT = 900
DRIVE_TIMEOUT = 900
GRADE_TIMEOUT = 3600
CHECK_DETAIL_CHARS = 160
LABEL = containers.LABEL
TOOL_NAMES = ("shell", "oracle", "driver", "grade_dev", "checkpoint")
# What `shell` tells the model after its container had to be rebuilt. The
# workspace is a host bind mount, so the files survive; nothing inside the
# container does.
SANDBOX_RESTART_NOTE = (
    "The solver container was lost and the harness has restarted it. Your files in /task "
    "are intact -- /task is a bind mount on the host -- but anything that was running inside "
    "the container is gone, along with any state outside /task (installed packages, /tmp, "
    "background processes, shell environment). Re-run this command."
)
# docker exec says one of these when the container it was given is not there.
SANDBOX_GONE_MARKERS = ("no such container", "is not running", "cannot exec in a stopped",
                        "container not running")

WORKSPACE_MOUNT = "/task"
# The evaluator's rebuild container runs as the operator, so the build tree it
# writes under runs/ stays removable by the operator on a rerun. The solver's
# own container is always the image's unprivileged 1000:1000.
BUILD_USER = f"{os.getuid()}:{os.getgid()}"


def docker(*args, timeout=900, check=True, capture=True):
    result = subprocess.run(["docker", *map(str, args)],
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None,
                            text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("docker command failed: " + ((result.stdout or "")[-8000:]))
    return result


def docker_run(purpose, *args, owner=None, timeout=900, check=True, capture=True):
    """`docker run` with a unique --name, the managed label and an owner label.

    A `subprocess` timeout kills the `docker run` client, not the container, so
    a container that outlives its client has to be stopped by name. `owner` is
    the attempt this container belongs to; only that owner's sweep may remove it.
    """
    name = containers.container_name(purpose)
    try:
        return docker("run", "--name", name, "--label", LABEL,
                      "--label", containers.owner_label(owner), *args,
                      timeout=timeout, check=check, capture=capture)
    except BaseException:
        containers.kill_container(name)
        raise


# ------------------------------------------------------------------- schemas

def _tool(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False}}}


def tool_schemas(descriptions: dict) -> list[dict]:
    """The five tools, OpenAI-style, with the instance's descriptions."""
    return [
        _tool("shell", descriptions["shell"],
              {"command": {"type": "string"},
               "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": SHELL_TIMEOUT_MAX}},
              ["command"]),
        _tool("oracle", descriptions["oracle"],
              {"case_path": {"type": "string"}, "outdir": {"type": "string"},
               "assets_dir": {"type": "string"}},
              ["case_path", "outdir"]),
        _tool("driver", descriptions["driver"],
              {"case_path": {"type": "string"}, "outdir": {"type": "string"},
               "assets_dir": {"type": "string"}},
              ["case_path", "outdir"]),
        _tool("grade_dev", descriptions["grade_dev"],
              {"names": {"type": "array", "items": {"type": "string"}}},
              []),
        _tool("checkpoint", descriptions["checkpoint"],
              {"note": {"type": "string"}},
              []),
    ]


GENERIC_DESCRIPTIONS = {
    "shell": "Run a bash command in /task inside the sandbox. No network. Output is truncated to "
             "32 KiB; the timeout cap is 600 s.",
    "oracle": "Run a case you wrote against the reference and copy its outputs back into /task.",
    "driver": "Run the driver against YOUR candidate artifact and write its outputs to outdir.",
    "grade_dev": "Score your current build on the public cases with the real metric.",
    "checkpoint": "Snapshot your /task source and NOTES.md now.",
}
TOOLS = tool_schemas(GENERIC_DESCRIPTIONS)


def checks_summary(checks) -> dict:
    """Counts plus the failures, in place of every check with its full detail.

    What the model has to act on is which checks failed and enough of each
    detail to find the cause; the passing ones only say the same thing many
    times. The grader's own report keeps the full list either way -- this
    shapes the `grade_dev` document, which is read into a context window.
    """
    from ..grader import jsonio
    checks = checks or []
    failed = []
    for check in checks:
        if check.get("ok"):
            continue
        detail = check.get("detail")
        if not isinstance(detail, str):
            detail = jsonio.dumps(detail)
        failed.append({"name": check.get("check"), "detail": detail[:CHECK_DETAIL_CHARS]})
    return {"checks_total": len(checks),
            "checks_passed": sum(1 for c in checks if c.get("ok")),
            "checks_failed": failed}


def atomic_json(path, data, mode=0o600):
    """Write one report atomically, through the canonical encoder."""
    from ..grader import jsonio
    jsonio.atomic_write_json(path, data, mode=mode)


# ------------------------------------------------------------------- sandbox

class SolverSandbox:
    """A long-lived, network-less container with the workspace bind-mounted read-write.

    The container can disappear under the model: another process's sweep, an
    operator's `docker rm -f`, an OOM kill. Every exec therefore checks that
    the container is alive, rebuilds it from the workspace when it is not, and
    reports the loss as an infrastructure incident so the attempt cannot be
    published as a valid measurement.
    """

    def __init__(self, workspace, *, image, cpus="4", memory="8g", pids="1024", name=None,
                 owner=None, on_incident=None, prefix="evalbase-solver"):
        self.workspace = Path(workspace).resolve()
        self.image = image
        self.name = name or f"{prefix}-" + uuid.uuid4().hex[:12]
        self.owner = containers.resolve_owner(owner)
        self.on_incident = on_incident
        self.incidents = []
        self.restarts = 0
        source = str(self.workspace)
        if "," in source:
            raise ValueError("workspace path may not contain commas")
        # Recorded verbatim so the mount set and the uid of the container the
        # model runs in can be audited from the attempt's own files.
        self.argv = ["docker", "run", "-d", "--name", self.name, "--label", LABEL,
                     "--label", containers.owner_label(self.owner),
                     "--network", "none", "--cap-drop", "ALL",
                     "--security-opt", "no-new-privileges",
                     "--pids-limit", str(pids), "--memory", memory, "--memory-swap", memory,
                     "--cpus", str(cpus), "--user", "1000:1000",
                     "--ulimit", "nofile=4096:4096",
                     "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g",
                     "--tmpfs", "/var/tmp:rw,nosuid,nodev,size=512m",
                     "--mount", f"type=bind,src={source},dst={WORKSPACE_MOUNT}",
                     "-w", WORKSPACE_MOUNT, "-e", "HOME=/task", self.image,
                     "sleep", "infinity"]
        docker(*self.argv[1:])
        self.closed = False

    def alive(self) -> bool:
        """True only if the container exists and is running right now."""
        probe = docker("inspect", "-f", "{{.State.Running}}", self.name, timeout=60, check=False)
        return (probe.stdout or "").strip().splitlines()[-1:] == ["true"]

    def _incident(self, observed: str, restarted: bool, error=None) -> dict:
        entry = {"kind": "sandbox_lost", "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "container": self.name, "owner": self.owner,
                 "observed": str(observed)[:2000], "restarted": bool(restarted),
                 "restart_error": None if error is None else str(error)[:2000],
                 "restarts": self.restarts}
        self.incidents.append(entry)
        if self.on_incident:
            try:
                self.on_incident(entry)
            except Exception:                      # recording must not kill the attempt
                pass
        return entry

    def recreate(self) -> None:
        """Rebuild the container from the same workspace and the same argv."""
        docker("rm", "-f", self.name, check=False)
        docker(*self.argv[1:])
        self.closed = False
        self.restarts += 1

    def _lost(self, observed: str) -> dict:
        try:
            self.recreate()
        except Exception as exc:
            return self._incident(observed, restarted=False, error=exc)
        return self._incident(observed, restarted=True)

    def _restart_result(self, observed: str, incident: dict, exit_code: int = 125) -> dict:
        note = SANDBOX_RESTART_NOTE if incident["restarted"] else (
            "The solver container was lost and the harness could not restart it: "
            + str(incident["restart_error"] or "unknown error"))
        text = (observed.rstrip() + "\n\n" + note) if observed.strip() else note
        return {"exit_code": exit_code, "output": text[-SHELL_OUTPUT_LIMIT:],
                "truncated": False, "output_bytes": len(text), "timed_out": False,
                "sandbox_restarted": bool(incident["restarted"]),
                "sandbox_incident": incident["time"]}

    def shell(self, command, timeout=SHELL_TIMEOUT_MAX, cwd=WORKSPACE_MOUNT):
        if not isinstance(command, str) or not command.strip() or len(command) > 200000:
            raise ValueError("invalid shell command")
        if type(timeout) is not int or not 1 <= timeout <= SHELL_TIMEOUT_MAX:
            raise ValueError(f"timeout_seconds must be 1..{SHELL_TIMEOUT_MAX}")
        if not self.closed and not self.alive():
            incident = self._lost(f"docker inspect reports container {self.name} is not running")
            return self._restart_result("", incident)
        result = self._exec(command, timeout, cwd)
        if (result["exit_code"] != 0 and not self.closed and _looks_lost(result["output"])
                and not self.alive()):
            # The liveness probe passed and the exec still could not find the
            # container: it was removed inside this call's own window. The
            # second probe keeps a model's *own* output ("postgres is not
            # running") from being read as a lost container.
            incident = self._lost(result["output"][-2000:])
            return self._restart_result(result["output"], incident, exit_code=result["exit_code"])
        return result

    def _exec(self, command, timeout, cwd):
        proc = subprocess.Popen(
            ["docker", "exec", "-w", cwd, self.name, "timeout", "--signal=TERM", "--kill-after=5",
             str(timeout), "bash", "-lc", command],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
        return _pump(proc, timeout)

    def paused(self):
        sandbox = self

        class _Pause:
            def __enter__(self):
                docker("pause", sandbox.name, check=False)

            def __exit__(self, *exc):
                docker("unpause", sandbox.name, check=False)
                return False
        return _Pause()

    def close(self):
        if not self.closed:
            docker("rm", "-f", self.name, check=False)
            self.closed = True


class LocalSandbox:
    """Runs the shell on the host in the workspace directory. Test-only.

    Selected with `--sandbox none`. There is no isolation: no network block,
    no uid change, no resource limit, and `/task` is the host path of the
    workspace. It exists so that the whole tool path -- paths, caps, dispatch,
    MCP framing, grading -- can be exercised without Docker, and for instances
    like the toy whose driver is plain Python. Never use it for a measurement.
    """

    def __init__(self, workspace, **_):
        self.workspace = Path(workspace).resolve()
        self.name = "local-sandbox"
        self.image = "none"
        self.owner = None
        self.argv = ["local", str(self.workspace)]
        self.incidents = []
        self.restarts = 0
        self.closed = False

    def alive(self) -> bool:
        return not self.closed

    def shell(self, command, timeout=SHELL_TIMEOUT_MAX, cwd=None):
        if not isinstance(command, str) or not command.strip() or len(command) > 200000:
            raise ValueError("invalid shell command")
        if type(timeout) is not int or not 1 <= timeout <= SHELL_TIMEOUT_MAX:
            raise ValueError(f"timeout_seconds must be 1..{SHELL_TIMEOUT_MAX}")
        # `/task` is mapped to the workspace so the model's paths and the
        # instance's driver command work unchanged.
        command = command.replace(WORKSPACE_MOUNT + "/", str(self.workspace) + "/") \
                         .replace(" " + WORKSPACE_MOUNT, " " + str(self.workspace))
        proc = subprocess.Popen(
            ["timeout", "--signal=TERM", "--kill-after=5", str(timeout), "bash", "-lc", command],
            cwd=str(self.workspace), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, bufsize=0,
            env={**os.environ, "HOME": str(self.workspace), "PYTHONDONTWRITEBYTECODE": "1"})
        return _pump(proc, timeout)

    def paused(self):
        class _P:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, *exc):
                return False
        return _P()

    def close(self):
        self.closed = True


def _pump(proc, timeout) -> dict:
    kept = bytearray()
    total = 0
    deadline = time.monotonic() + timeout + 15
    try:
        while True:
            if time.monotonic() > deadline:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                break
            if select.select([proc.stdout], [], [], 0.1)[0]:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                total += len(chunk)
                if len(kept) < SHELL_OUTPUT_LIMIT:
                    kept.extend(chunk[:SHELL_OUTPUT_LIMIT - len(kept)])
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        proc.stdout.close()
    return {"exit_code": proc.returncode, "output": kept.decode(errors="replace"),
            "truncated": total > SHELL_OUTPUT_LIMIT, "output_bytes": total,
            "timed_out": proc.returncode == 124}


def make_sandbox(kind: str, workspace, *, image, cpus="4", memory="8g", owner=None, on_incident=None):
    if kind == "none":
        return LocalSandbox(workspace)
    if kind == "docker":
        return SolverSandbox(workspace, image=image, cpus=cpus, memory=memory, owner=owner,
                             on_incident=on_incident)
    raise ValueError(f"unknown sandbox kind {kind!r} (docker or none)")


def _looks_lost(output: str) -> bool:
    """True when docker's own error says the container is gone or stopped."""
    text = (output or "").lower()
    return any(marker in text for marker in SANDBOX_GONE_MARKERS)


def cleanup_containers(owner=None):
    """Remove the containers `owner` started. Never anything else."""
    owner = containers.resolve_owner(owner)
    ids = docker("ps", "-aq", "--filter", "label=" + LABEL,
                 "--filter", "label=" + containers.owner_label(owner)).stdout.split()
    for name in ids:
        docker("rm", "-f", name, check=False)
    return {"owner": owner, "containers_removed": len(ids)}


# ------------------------------------------------------------- path handling

def _safe_relative(raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("a path is required")
    if "\x00" in raw or len(raw) > 4096:
        raise ValueError("invalid path")
    text = raw.strip()
    if text.startswith(WORKSPACE_MOUNT + "/"):
        text = text[len(WORKSPACE_MOUNT) + 1:]
    elif text == WORKSPACE_MOUNT:
        text = "."
    elif text.startswith("/"):
        raise ValueError(f"paths must be inside {WORKSPACE_MOUNT}: {raw}")
    rel = Path(text)
    if any(part == ".." for part in rel.parts):
        raise ValueError(f"'..' is not allowed in tool paths: {raw}")
    return rel


def resolve_in_workspace(workspace: Path, raw: str, *, must_exist=True, kind="file") -> Path:
    """Map a model-supplied /task path to a host path, rejecting every escape."""
    workspace = Path(workspace).resolve()
    rel = _safe_relative(raw)
    target = workspace
    for part in rel.parts:
        if part == ".":
            continue
        target = target / part
        if target.is_symlink():
            raise ValueError(f"symlinks are not allowed in tool paths: {raw}")
    if must_exist:
        if kind == "file" and not target.is_file():
            raise ValueError(f"no such file in the workspace: {raw}")
        if kind == "dir" and not target.is_dir():
            raise ValueError(f"no such directory in the workspace: {raw}")
    if not target.resolve().is_relative_to(workspace):
        raise ValueError(f"path escapes the workspace: {raw}")
    return target


def _writable_outdir(workspace: Path, raw: str) -> Path:
    target = resolve_in_workspace(workspace, raw, must_exist=False, kind="dir")
    rel = target.relative_to(Path(workspace).resolve())
    if rel.parts and rel.parts[0] in ("spec", "dev"):
        raise ValueError("outdir must not be inside the supplied spec/ or dev/ trees")
    if target.exists() and not target.is_dir():
        raise ValueError("outdir exists and is not a directory")
    target.mkdir(parents=True, exist_ok=True)
    try:
        target.chmod(0o777)
    except OSError:
        pass
    return target


# ------------------------------------------------------------ case checks

def inspect_case(instance: Instance, path: Path) -> dict:
    """Parse and bound a model-authored case through the instance's driver."""
    size = path.stat().st_size
    if size > ORACLE_CASE_BYTES:
        raise ValueError(f"case exceeds {ORACLE_CASE_BYTES // (1024 * 1024)} MiB")
    info = instance.driver.inspect_case(path)
    if info.get("snapshots", 0) > ORACLE_MAX_SNAPSHOTS:
        raise ValueError(f"case produces {info['snapshots']} snapshots; the oracle allows {ORACLE_MAX_SNAPSHOTS}")
    info.setdefault("bytes", size)
    return info


def _ledger_summary(outdir: Path, limit: int = 120) -> dict:
    path = outdir / "ledger.json"
    if not path.is_file():
        return {"ledger": None}
    try:
        ledger = json.loads(path.read_text())
    except ValueError:
        return {"ledger": "corrupt"}
    events = ledger.get("events", [])
    out = {"exit": ledger.get("exit"), "event_count": len(events),
           "events": events[:limit], "events_truncated": len(events) > limit}
    for key in ("errors", "device_errors", "status_messages", "peak_rss_bytes"):
        if key in ledger:
            out[key] = ledger[key][:20] if isinstance(ledger[key], list) else ledger[key]
    return out


# ---------------------------------------------------------------- tool logic

class ToolSession:
    """Shared tool implementation for the MCP bridge and the OpenRouter loop.

    Subclasses provide: instance, run_dir, workspace, options, state, sandbox,
    redactor, elapsed(), save(), log(), and `owner` -- the attempt id every
    container started on this attempt's behalf is labelled with.
    """

    instance: Instance
    owner = None
    corpus_public = None
    corpus_assets = None
    refcache = None

    def _corpus(self):
        return Path(self.corpus_public or self.instance.corpus_public)

    def _assets(self):
        return Path(self.corpus_assets or self.instance.assets)

    def _refcache(self):
        return Path(self.refcache or self.instance.refcache)

    def record_incident(self, entry: dict) -> dict:
        """Write one infrastructure incident into the attempt's own manifest."""
        entry = dict(entry, elapsed_seconds=round(self.elapsed(), 3))
        manifest = self.state.setdefault("manifest", {}) if isinstance(self.state, dict) else {}
        manifest.setdefault("infrastructure_incidents", []).append(entry)
        try:
            self.save()
            self.log("infrastructure_incident", **entry)
        except Exception:
            pass
        return entry

    # ---- checkpoint
    def checkpoint(self, note: str = "Automatic checkpoint") -> dict:
        self.state["checkpoint_index"] += 1
        index = self.state["checkpoint_index"]
        dest = self.run_dir / "checkpoints" / f"{index:06d}"
        error = None
        try:
            with self.sandbox.paused():
                manifest = ws.checkpoint(self.instance, self.workspace, dest, index=index, note=note,
                                         elapsed=self.elapsed())
        except (ValueError, OSError) as exc:
            manifest = {"index": index, "source_sha256": None, "note": note[:16000]}
            error = self.redactor.text(exc)
            dest.mkdir(parents=True, exist_ok=True)
            atomic_json(dest / "checkpoint.json", manifest | {"export_error": error}, mode=0o644)
        self.last_checkpoint = self.elapsed()
        entry = {"index": index, "source_sha256": manifest.get("source_sha256"),
                 "archive_sha256": manifest.get("archive_sha256"), "note": note[:400],
                 "elapsed_seconds": round(self.elapsed(), 3), "export_error": error}
        self.state.setdefault("checkpoints", []).append(entry)
        self.save()
        self.log("checkpoint", **entry)
        return {"checkpoint": index, "source_sha256": manifest.get("source_sha256"),
                "files": len(manifest.get("files", {}) or {}), "export_error": error}

    # ---- oracle
    def oracle(self, case_path: str, outdir: str, assets_dir: str | None = None) -> dict:
        case = resolve_in_workspace(self.workspace, case_path)
        info = inspect_case(self.instance, case)
        out = _writable_outdir(self.workspace, outdir)
        assets = self._assets_dir(case, assets_dir)
        self.state["oracle_index"] = self.state.get("oracle_index", 0) + 1
        index = self.state["oracle_index"]
        staging = self.run_dir / "oracle" / f"{index:06d}"
        staging.mkdir(parents=True, exist_ok=True)
        stage_assets = staging / "assets"
        stage_assets.mkdir(exist_ok=True)
        total = 0
        for name in info["assets"]:
            source = resolve_in_workspace(assets, name) if assets else None
            if source is None:
                raise ValueError(f"case names asset {name} but no assets directory was found")
            total += source.stat().st_size
            if total > ORACLE_ASSET_BYTES:
                raise ValueError("referenced assets exceed the oracle size limit")
            shutil.copyfile(source, stage_assets / Path(name).name)
        shutil.copyfile(case, staging / "case.json")
        result = staging / "out"
        result.mkdir(exist_ok=True)
        result.chmod(0o777)
        t0 = time.time()
        # candidate=None: the oracle. The driver labels any container with this attempt.
        res = self.instance.driver.run(str(staging / "case.json"), str(result), str(stage_assets),
                                       candidate=None, timeout_s=ORACLE_TIMEOUT, owner=self.owner)
        wall = time.time() - t0
        copied = self._copy_into_workspace(result, out)
        summary = {"case": info.get("name"), "snapshots_requested": info["snapshots"],
                   "outdir": self._task_path(out), "files": copied,
                   "wall_seconds": round(wall, 3), "returncode": res.returncode,
                   "stderr_tail": self.redactor.text(res.stderr_tail or "")[-4000:],
                   **_ledger_summary(result)}
        self.log("oracle", case=self._task_path(case), **{k: summary[k] for k in
                 ("snapshots_requested", "wall_seconds", "returncode")})
        return summary

    # ---- driver
    def driver(self, case_path: str, outdir: str, assets_dir: str | None = None) -> dict:
        case = resolve_in_workspace(self.workspace, case_path)
        info = inspect_case(self.instance, case)
        out = _writable_outdir(self.workspace, outdir)
        assets = self._assets_dir(case, assets_dir)
        artifact = self.workspace / self.instance.task.artifact
        if not artifact.is_file():
            raise ValueError(f"/task/{self.instance.task.artifact} does not exist; build first "
                             f"(`{self.instance.task.build_command}`)")
        command = self.instance.task.driver_command.format(
            case=self._q(self._task_path(case)), outdir=self._q(self._task_path(out)),
            assets=self._q(self._task_path(assets)) if assets else "'.'")
        command = f"rm -f {self._q(self._task_path(out))}/ledger.json 2>/dev/null; " + command
        t0 = time.time()
        shell = self.sandbox.shell(command, timeout=min(SHELL_TIMEOUT_MAX, DRIVE_TIMEOUT))
        summary = {"case": info.get("name"), "snapshots_requested": info["snapshots"],
                   "outdir": self._task_path(out), "exit_code": shell["exit_code"],
                   "wall_seconds": round(time.time() - t0, 3),
                   "output": shell["output"][-8000:], **_ledger_summary(out)}
        self.log("driver", case=self._task_path(case), exit_code=shell["exit_code"])
        return summary

    # ---- grade_dev
    def grade_dev(self, names=None) -> dict:
        from ..grader import jsonio, runner as grader_runner
        corpus = self._corpus()
        available = sorted(p.stem for p in corpus.glob("*.json"))
        if names in (None, [], ()):
            selected = available
        else:
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise ValueError("names must be an array of public case names")
            unknown = [n for n in names if n not in available]
            if unknown:
                raise ValueError(f"unknown public case(s) {unknown}; available: {available}")
            selected = [n for n in available if n in set(names)]
        artifact = self.workspace / self.instance.task.artifact
        if not artifact.is_file():
            raise ValueError(f"/task/{self.instance.task.artifact} does not exist; build first "
                             f"(`{self.instance.task.build_command}`)")
        self.state["grade_index"] = self.state.get("grade_index", 0) + 1
        index = self.state["grade_index"]
        base = self.run_dir / "grade_dev" / f"{index:06d}"
        lib_dir = base / "candidate"
        lib_dir.mkdir(parents=True, exist_ok=True)
        with self.sandbox.paused():
            if artifact.is_symlink():
                raise ValueError(f"{self.instance.task.artifact} must be a regular file, not a symlink")
            copy_artifact(self.instance, self.workspace, lib_dir)
        deadline = time.monotonic() + GRADE_TIMEOUT
        cases = []
        for name in selected:
            if time.monotonic() > deadline:
                cases.append({"name": name, "skipped": "grade_dev time limit reached"})
                continue
            grade = grader_runner.grade_replay(self.instance, str(corpus / f"{name}.json"),
                                               str(self._refcache()), str(self._assets()),
                                               str(lib_dir), str(base / "cases"), owner=self.owner)
            detail = grade.detail
            cases.append({
                "name": grade.name, "category": grade.category, "family": grade.family,
                "score": round(grade.score, 4), "exit": detail.get("exit"),
                "threshold_T": detail.get("threshold"),
                "snapshot_defects_D": detail.get("snapshot_defects"),
                "snapshot_scores": detail.get("snapshot_scores"),
                "first_diverge_snapshot": detail.get("first_diverge_snapshot"),
                **checks_summary(detail.get("checks")),
                "ratio": detail.get("ratio"),
                "replay_score": detail.get("replay_score"),
                "stderr_tail": self.redactor.text(detail.get("stderr_tail", ""))[-2000:],
                "candidate_wall_seconds": detail.get("candidate_wall_seconds"),
                "reference_wall_seconds": detail.get("reference_wall_seconds"),
            })
        graded = [g for g in cases if "score" in g]
        aggregate = grader_runner.aggregate(self.instance.metric, [
            grader_runner.ReplayGrade(g["name"], g["category"], g["family"], g["score"], {
                "ratio": g.get("ratio", float("inf"))}) for g in graded])
        report = {"corpus": "public", "graded": [g["name"] for g in graded],
                  "aggregate": {"overall": round(aggregate["overall"], 4),
                                "categories": {k: {"score": round(v["score"], 4), "n": v["n"],
                                                   "weight": v["weight"]}
                                               for k, v in aggregate["categories"].items()}},
                  "cases": cases,
                  "note": "public-corpus scores only; the hidden corpus is larger and different"}
        # Sanitize once, here -- after aggregate() has read the real inf -- so the
        # file, the return value and the MCP message are the same valid document.
        report = jsonio.jsonable(report)
        atomic_json(base / "report.json", report, mode=0o644)
        self.log("grade_dev", names=selected, overall=report["aggregate"]["overall"])
        return report

    # ---- helpers
    def _q(self, text: str) -> str:
        return "'" + str(text).replace("'", "'\\''") + "'"

    def _task_path(self, host_path: Path) -> str:
        rel = Path(host_path).resolve().relative_to(Path(self.workspace).resolve())
        return WORKSPACE_MOUNT if str(rel) == "." else f"{WORKSPACE_MOUNT}/{rel.as_posix()}"

    def _assets_dir(self, case: Path, assets_dir: str | None) -> Path | None:
        if assets_dir:
            return resolve_in_workspace(self.workspace, assets_dir, kind="dir")
        default = Path(self.workspace) / "dev" / "assets"
        if case.parent == Path(self.workspace) / "dev" / "cases" and default.is_dir():
            return default
        return case.parent

    def _copy_into_workspace(self, source: Path, destination: Path) -> list[str]:
        written = []
        with self.sandbox.paused():
            if destination.is_symlink():
                raise ValueError("outdir must not be a symlink")
            for item in sorted(Path(source).iterdir()):
                if not item.is_file() or item.is_symlink():
                    continue
                target = destination / item.name
                if target.is_symlink():
                    raise ValueError(f"refusing to overwrite a symlink: {item.name}")
                shutil.copyfile(item, target)
                try:
                    target.chmod(0o666)
                except OSError:
                    pass
                written.append(item.name)
        return written

    # ---- dispatch
    def execute(self, call: dict):
        function = call["function"]
        try:
            params = json.loads(function["arguments"]) if isinstance(function["arguments"], str) else function["arguments"]
        except ValueError:
            raise ValueError("tool arguments must be valid JSON") from None
        if not isinstance(params, dict):
            raise ValueError("tool arguments must be an object")
        name = function["name"]
        if name == "shell":
            timeout = params.get("timeout_seconds", 120)
            if type(timeout) is not int:
                raise ValueError("timeout_seconds must be an integer")
            return self.sandbox.shell(params["command"], timeout)
        if name == "oracle":
            return self.oracle(params["case_path"], params["outdir"], params.get("assets_dir"))
        if name == "driver":
            return self.driver(params["case_path"], params["outdir"], params.get("assets_dir"))
        if name == "grade_dev":
            return self.grade_dev(params.get("names"))
        if name == "checkpoint":
            note = params.get("note", "")
            if not isinstance(note, str):
                raise ValueError("note must be text")
            return self.checkpoint(note or "Model checkpoint")
        raise ValueError("unknown tool: " + str(name))


def validate_arguments(name: str, arguments) -> None:
    schema = next((t["function"]["parameters"] for t in TOOLS if t["function"]["name"] == name), None)
    if schema is None or not isinstance(arguments, dict):
        raise ValueError("unknown tool or invalid arguments")
    if set(arguments) - set(schema["properties"]):
        raise ValueError("unexpected tool argument")
    if not set(schema["required"]) <= set(arguments):
        raise ValueError("missing required tool argument")


# ------------------------------------------------- rebuild + trusted grading

def copy_artifact(instance: Instance, build_root: Path, lib_dir: Path) -> Path:
    """Copy the candidate artifact (and anything beside it the driver needs) into lib_dir.

    The driver takes a *directory*; the artifact keeps its base name inside it.
    Files listed in the artifact's directory that the driver may import
    (helper modules beside a script) are copied too, but never the workspace's
    supplied trees.
    """
    artifact = Path(build_root) / instance.task.artifact
    lib_dir = Path(lib_dir)
    lib_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(artifact, lib_dir / artifact.name)
    if artifact.parent.resolve() == Path(build_root).resolve():
        # a script-shaped artifact at the workspace root: bring its sibling
        # modules along, and nothing from the supplied trees
        for sibling in sorted(artifact.parent.iterdir()):
            if (sibling.is_file() and not sibling.is_symlink() and sibling != artifact
                    and sibling.suffix.lower() in instance.task.source_suffixes
                    and sibling.name not in ("TASK.md", "SPEC.md")):
                shutil.copyfile(sibling, lib_dir / sibling.name)
    return lib_dir


def build_export(instance: Instance, source: Path, out: Path, *, sandbox: str = "docker", cpus="4",
                 memory="8g", timeout=1800, owner=None) -> dict:
    """Rebuild an exported snapshot in a clean sandbox with the evaluator's own spec/.

    The build container runs as the *host* user, not the image's uid 1000: the
    build tree is evaluator scaffolding under runs/, and a rerun must be able
    to delete what the previous run left behind.
    """
    task = instance.task
    source, out = Path(source).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    build_root = out / "task"
    if build_root.exists():
        ws.remove_tree(build_root, image=instance.solver_image)
    shutil.copytree(source, build_root)
    shutil.rmtree(build_root / "spec", ignore_errors=True)
    if task.spec_dir and task.spec_dir.is_dir():
        shutil.copytree(task.spec_dir, build_root / "spec")   # the evaluator's own copy
    (build_root / "source-manifest.json").unlink(missing_ok=True)
    ws._chmod_tree(build_root, 0o777, 0o666)
    command = task.build_command + " 2>&1 | tail -n 400"
    if sandbox == "none":
        local = LocalSandbox(build_root)
        result = local.shell(command, timeout=min(SHELL_TIMEOUT_MAX, int(timeout)))
        returncode, stdout = result["exit_code"], result["output"]
    else:
        log = docker_run("build", "--rm", "--init", "--network", "none",
                         "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                         "--pids-limit", "1024", "--memory", memory, "--memory-swap", memory,
                         "--cpus", cpus, "--user", BUILD_USER,
                         "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g",
                         "--mount", f"type=bind,src={build_root},dst={WORKSPACE_MOUNT}", "-w", WORKSPACE_MOUNT,
                         "-e", "HOME=/task", instance.solver_image,
                         "bash", "-lc", command,
                         owner=owner, timeout=timeout, check=False)
        returncode, stdout = log.returncode, log.stdout or ""
    (out / "build.log").write_text(stdout)
    artifact = build_root / task.artifact
    record = {"build_exit_code": returncode, "artifact_present": artifact.is_file(),
              "log_tail": stdout[-8000:]}
    if artifact.is_file():
        lib_dir = copy_artifact(instance, build_root, out / "candidate")
        record["lib_dir"] = str(lib_dir)
        record["artifact_sha256"] = ws.sha256_file(lib_dir / artifact.name)
    record["build_success"] = bool(record["artifact_present"]) and returncode == 0
    atomic_json(out / "build.json", record, mode=0o644)
    return record


def grade_library(instance: Instance, lib_dir, out, *, corpus, assets=None, cache=None,
                  label="attempt", owner=None) -> dict:
    """Score a built candidate with the real grader against a corpus directory."""
    from ..grader import jsonio, runner as grader_runner
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    corpus = Path(corpus)
    assets = Path(assets or instance.assets)
    cache = Path(cache or instance.refcache)
    grades = []
    for replay in sorted(corpus.glob("*.json")):
        grades.append(grader_runner.grade_replay(instance, str(replay), str(cache), str(assets),
                                                 str(lib_dir) if lib_dir else None,
                                                 str(out / "cases"), owner=owner))
    aggregate = grader_runner.aggregate(instance.metric, grades)
    report = {"label": label, "corpus": str(corpus), "cache": str(cache),
              "image": instance.reference_image, "metric_version": instance.metric.version,
              "instance": instance.name, "aggregate": aggregate,
              "cases": [{"name": g.name, "category": g.category, "family": g.family,
                         "score": g.score, "detail": g.detail} for g in grades]}
    jsonio.write_json(out / "report.json", report)
    return report


def build_and_grade(instance: Instance, source, out, *, corpus, assets=None, cache=None,
                    label="attempt", owner=None, sandbox: str = "docker") -> dict:
    """Rebuild an export in a clean sandbox, then grade it with the real grader."""
    from ..grader import jsonio
    out = Path(out)
    build = build_export(instance, source, out / "build", owner=owner, sandbox=sandbox)
    if not build["build_success"]:
        report = {"label": label, "build": build, "aggregate": {"overall": 0.0, "categories": {},
                  "full_success": False}, "cases": [], "build_failure": True}
        jsonio.write_json(out / "report.json", report)
        return report
    report = grade_library(instance, build["lib_dir"], out, corpus=corpus, assets=assets, cache=cache,
                           label=label, owner=owner)
    report["build"] = build
    report["build_failure"] = False
    jsonio.write_json(out / "report.json", report)
    return report
