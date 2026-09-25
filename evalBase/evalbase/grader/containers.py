"""Container ownership: labels, names, kills and owner-scoped sweeps.

Every container the evaluation starts carries two labels: `LABEL`, which says
the evaluation started it, and `OWNER_KEY=<owner>`, which says *which run*
started it. Every automatic sweep removes only the containers of one owner.
This is not hygiene: with the managed label alone, two attempts running side
by side each removed the other's live containers -- one attempt-start sweep
killed the other attempt's solver sandbox 44 s into its run, and
attempt-boundary sweeps SIGKILLed five of the other attempt's in-flight grading
containers, which the report recorded as `exit: crash`. A container with the
managed label but no owner label predates ownership and belongs to nobody; no
automatic sweep will touch it, only the explicit `sweep --all --yes`.

Every container also carries a unique `--name`. `subprocess.run(...,
timeout=...)` only kills the local `docker run` *client*: the container keeps
running on the daemon, and a candidate that never finishes a case would
otherwise leave a driver pinning a core for as long as the host stays up. The
name is how the timeout path stops the right container; the label is how a
sweep finds anything that still got away.
"""
from __future__ import annotations

import datetime
import os
import re
import signal
import subprocess
import uuid

LABEL = "io.evalbase.managed=1"
OWNER_KEY = "io.evalbase.owner"
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
NAME_PREFIX = "evalbase"


def process_owner() -> str:
    """The owner id for containers started outside an attempt: cli-<pid>.

    Unique per process, so a standalone grader command can clean up after
    itself without being able to reach anything another process is running.
    """
    return f"cli-{os.getpid()}"


def resolve_owner(owner: str | None = None) -> str:
    """Validate an owner id, defaulting to this process's own.

    Defaulting rather than raising is deliberate: a caller that forgets to pass
    an owner gets a private id that matches nothing else, never a wildcard.
    """
    owner = str(owner).strip() if owner is not None and str(owner).strip() else process_owner()
    if not OWNER_RE.match(owner):
        raise ValueError(f"invalid container owner id: {owner!r}")
    return owner


def owner_label(owner: str | None = None) -> str:
    """The `io.evalbase.owner=<owner>` label value for a docker argument."""
    return f"{OWNER_KEY}={resolve_owner(owner)}"


def container_name(purpose: str) -> str:
    """A unique, greppable container name: evalbase-<purpose>-<8 hex>."""
    return f"{NAME_PREFIX}-{purpose}-{uuid.uuid4().hex[:8]}"


def signal_name(returncode: int | None) -> str | None:
    """SIGKILL/SIGSEGV/... for a returncode that reports a signal, else None.

    `docker run` reports a container killed by signal n as 128+n; subprocess
    reports a client killed by signal n as -n. Without this, a container the
    harness itself SIGKILLed (137) and one that segfaulted (139) both reach the
    report as `exit: crash` with nothing to tell them apart.
    """
    if returncode is None:
        return None
    if returncode < 0:
        number = -returncode
    elif 128 < returncode < 128 + signal.NSIG:
        number = returncode - 128
    else:
        return None
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"SIG{number}"


def _docker(args: list, timeout: float = 60.0):
    """Run a short docker command. None if docker is missing or does not answer."""
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def kill_container(name: str) -> bool:
    """Stop one container by name or id; True if `docker kill` reported success.

    `docker kill` is enough for a container started with --rm -- the daemon
    removes it -- but `docker rm -f` follows for the case where it was not.
    """
    if not name:
        return False
    killed = _docker(["kill", str(name)])
    ok = bool(killed is not None and killed.returncode == 0)
    _docker(["rm", "-f", str(name)])
    return ok


def list_managed_containers(label: str = LABEL, owner: str | None = None) -> list[dict]:
    """Containers carrying `label`, running or not. Never lists anything else.

    With `owner`, docker ANDs a second `label=<OWNER_KEY>=<owner>` filter, so a
    container started by another attempt -- or by anything from before owners
    existed, which carries no owner label at all -- is not returned and cannot
    be killed by a sweep that owns something else.
    """
    args = ["ps", "-a", "--filter", f"label={label}"]
    if owner is not None:
        args += ["--filter", f"label={owner_label(owner)}"]
    args += ["--format",
             "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.CreatedAt}}\t"
             + "{{.Label \"" + OWNER_KEY + "\"}}"]
    p = _docker(args)
    rows = []
    for line in ((p.stdout if p is not None else "") or "").splitlines():
        parts = (line.split("\t") + ["", "", "", "", "", ""])[:6]
        if not parts[0].strip():
            continue
        rows.append(dict(zip(("id", "name", "image", "status", "created_at", "owner"),
                             (x.strip() for x in parts))))
    return rows


def _age_seconds(created_at: str) -> float:
    """Age of a `docker ps` {{.CreatedAt}} stamp; inf when it cannot be read.

    Unreadable means "old enough to kill": the sweep must not spare a container
    because its timestamp had an unexpected shape.
    """
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*([+-]\d{2}:?\d{2}|Z)?",
                 created_at or "")
    if not m:
        return float("inf")
    tz = (m.group(3) or "+0000").replace(":", "").replace("Z", "+0000")
    try:
        t = datetime.datetime.strptime(f"{m.group(1)} {m.group(2)} {tz}", "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return float("inf")
    return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()


def kill_stale_containers(owner: str | None = None, label: str = LABEL,
                          older_than_s: float = 0, all_owners: bool = False) -> list[dict]:
    """Kill the containers `owner` started -- and only those -- and say so.

    This is the sweep every automatic caller uses, and it is scoped by owner on
    purpose. `all_owners=True` restores the label-wide behaviour and exists for
    exactly one caller, the explicit `sweep --all --yes`, which prints what it
    will kill and asks first. `older_than_s` spares containers younger than
    that. Returns one dict per container it tried to kill.
    """
    if not all_owners:
        owner = resolve_owner(owner)
    killed = []
    for c in list_managed_containers(label, None if all_owners else owner):
        if older_than_s and _age_seconds(c["created_at"]) < older_than_s:
            continue
        entry = {**c, "killed": kill_container(c["id"])}
        killed.append(entry)
        print(f"[sweep] killed {entry['name'] or entry['id']} "
              f"({entry['image']}, {entry['status']}, owner={entry.get('owner') or 'none'})",
              flush=True)
    return killed


def docker_base(cpus: str | None, mem: str, network: bool = False,
                name: str | None = None, owner: str | None = None,
                pids: str = "512") -> list[str]:
    """The `docker run` prefix every driver container shares: labels, limits, no network.

    `EVALBASE_PLATFORM` threads through to `--platform`, so a host can grade
    under emulation against a cross-built image without anything else changing.
    """
    cmd = ["docker", "run", "--rm", "--init", "--memory", mem, "--pids-limit", pids,
           "--label", LABEL, "--label", owner_label(owner)]
    if name:
        cmd += ["--name", name]
    platform = os.environ.get("EVALBASE_PLATFORM")
    if platform:
        cmd += ["--platform", platform]
    if not network:
        cmd += ["--network", "none"]
    if cpus:
        cmd += ["--cpus", cpus]
    return cmd


def run_named(cmd: list[str], name: str, timeout_s: float) -> tuple[int, str, bool]:
    """Run a `docker run --name <name> ...` argv; kill the container on timeout or interrupt.

    Returns (returncode, stderr, timed_out). The timeout path kills the
    container by name before returning, so a case that never finishes cannot
    keep a core busy after the grader has moved on.
    """
    timed_out = False
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        rc, err = p.returncode, p.stderr or ""
    except subprocess.TimeoutExpired as e:
        timed_out, rc = True, -1
        err = e.stderr or ""
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        kill_container(name)
    except BaseException:
        kill_container(name)
        raise
    return rc, err, timed_out
