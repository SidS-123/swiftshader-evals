"""Workspace population, immutable source export, checkpoints and versions.

Parametrised by the instance's `TaskSpec`.

Everything here runs on the host. The workspace (`/task` in the sandbox) is
populated from public material only: the starter, the task text, the frozen
spec tree if the instance has one, the public cases with their assets, the
reference outputs of those cases, and the case format. The hidden corpus, the
reference cache of hidden cases, the grader source and the oracle never enter
a workspace.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path

from ..interfaces import Instance
from ..grader import containers

EXCLUDED_TOP = {"build", "dev", "spec", ".git", ".cache", "__pycache__", "TASK.md", "SPEC.md"}
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 2048
MAX_ENTRIES = 20000
MAX_DEPTH = 32
SAFE_NAME = re.compile(r"[A-Za-z0-9_.+-][A-Za-z0-9_./+-]*")
NO_DOCKER_ENV = "EVALBASE_NO_DOCKER"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def digest_tree(root: Path, base: Path, patterns: tuple[str, ...] = ("*",)) -> dict[str, str]:
    out: dict[str, str] = {}
    root = Path(root)
    if not root.exists():
        return out
    if root.is_file():
        out[str(root.relative_to(base) if root.is_relative_to(base) else root)] = sha256_file(root)
        return out
    for pattern in patterns:
        for p in sorted(root.rglob(pattern)):
            if p.is_file() and "__pycache__" not in p.parts:
                out[str(p.relative_to(base) if p.is_relative_to(base) else p)] = sha256_file(p)
    return out


def _image_id(tag: str) -> str | None:
    if os.environ.get(NO_DOCKER_ENV):
        return None
    try:
        r = subprocess.run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    ident = r.stdout.strip()
    return ident if r.returncode == 0 and ident.startswith("sha256:") else None


def _git_hash(root: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def referenced_assets(corpus: Path) -> set[str]:
    names: set[str] = set()
    for replay in sorted(Path(corpus).glob("*.json")):
        try:
            data = json.loads(replay.read_text())
        except (OSError, ValueError):
            continue
        for op in data.get("ops", []) or []:
            asset = op.get("asset") if isinstance(op, dict) else None
            if isinstance(asset, dict) and isinstance(asset.get("file"), str):
                names.add(Path(asset["file"]).name)
    return names


def corpus_digest(corpus: Path, assets: Path) -> dict:
    """Identity of the public corpus the solver sees (names + content hashes)."""
    corpus, assets = Path(corpus), Path(assets)
    files = {p.name: sha256_file(p) for p in sorted(corpus.glob("*.json"))}
    used = sorted(referenced_assets(corpus))
    asset_hashes = {name: (sha256_file(assets / name) if (assets / name).is_file() else None) for name in used}
    blob = json.dumps({"replays": files, "assets": asset_hashes}, sort_keys=True).encode()
    return {"replays": files, "assets": asset_hashes, "sha256": hashlib.sha256(blob).hexdigest()}


def version_record(instance: Instance, corpus: Path | None = None, assets: Path | None = None) -> dict:
    """Everything that would make two attempts incomparable if it changed."""
    corpus = Path(corpus or instance.corpus_public)
    assets = Path(assets or instance.assets)
    here = Path(__file__).resolve().parents[1]
    trees = {"task": ([instance.task.task_dir], ("*",)),
             "evalbase": ([here], ("*.py",))}
    if instance.task.spec_dir:
        trees["spec"] = ([instance.task.spec_dir], ("*",))
    trees.update(instance.hashed_trees)
    files = {}
    for label, (paths, patterns) in trees.items():
        merged = {}
        for path in paths:
            merged.update(digest_tree(Path(path), instance.root, patterns))
        files[label] = merged

    def agg(d: dict[str, str]) -> str:
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()

    record = {"instance": instance.name,
              "git_commit": _git_hash(instance.root),
              "evalbase_git_commit": _git_hash(here.parent),
              "corpus": corpus_digest(corpus, assets),
              "reference_image": {"tag": instance.reference_image, "id": _image_id(instance.reference_image)},
              "solver_image": {"tag": instance.solver_image, "id": _image_id(instance.solver_image)},
              "files": files}
    for label, d in files.items():
        record[f"{label}_sha256"] = agg(d)
    return record


# ------------------------------------------------------------------ populate

# TASK.md carries this marker on a line of its own; `populate` replaces it with
# the stopping policy this attempt is actually run under, so the task text the
# model reads never contradicts the harness.
STOP_POLICY_MARKER = "<!-- harness: stopping policy -->"
SUBMIT_MARKER = "SUBMISSION FINAL"


def _hours(seconds: float | None) -> str:
    if not seconds:
        return "the budget given to this attempt"
    hours = seconds / 3600.0
    return f"{hours:.0f} hours" if hours >= 1 and abs(hours - round(hours)) < 0.05 else (
        f"{hours:.2f} hours" if hours >= 1 else f"{seconds / 60.0:.0f} minutes")


def stopping_paragraph(stop_policy: str = "budget", budget_seconds: float | None = None,
                       checkpoint_seconds: float | None = None) -> str:
    """The policy paragraph written into the workspace copy of TASK.md."""
    every = _hours(checkpoint_seconds) if checkpoint_seconds else "15 minutes"
    cap = _hours(budget_seconds)
    if stop_policy == "submit":
        return (
            f"Stopping policy for this attempt: **submit**. You decide when the work is done. "
            f"Work until you judge it complete, then stop calling tools and say so; the harness "
            f"asks you to confirm exactly once, and you end the attempt by replying with the "
            f"single line `{SUBMIT_MARKER}`. If instead there is more worth doing, ignore the "
            f"confirmation and keep working — nothing forces you to stop. A hard cap of {cap} of "
            f"wall clock ends the attempt wherever it stands, and your source and NOTES.md are "
            f"checkpointed every {every} regardless of what you are doing, so a checkpoint is "
            f"never a reason to stop and an unfinished implementation is still graded.")
    return (
        f"Stopping policy for this attempt: **budget**. The attempt runs for {cap} of wall clock "
        f"and a final answer does not end it: if you stop calling tools the harness restarts you "
        f"with your workspace, build outputs and NOTES.md exactly as you left them, so keep "
        f"improving until the budget ends. Your source and NOTES.md are checkpointed every "
        f"{every} regardless of what you are doing.")


def _chmod_tree(root: Path, dir_mode: int, file_mode: int) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            path.chmod(dir_mode if path.is_dir() else file_mode)
        except OSError:
            pass


def container_remove_command(path: Path, image: str, owner: str | None = None) -> list[str]:
    """`docker run` argv that deletes one directory a container owns.

    Only the *parent* directory is mounted and only the named child is removed,
    so a mistake cannot reach anything else. The container carries an owner
    label like every other container the evaluation starts.
    """
    path = Path(path).resolve()
    parent, name = path.parent, path.name
    if path == parent or not name or name in (".", ".."):
        raise ValueError(f"refusing to remove {path} from a container")
    if "," in str(parent):
        raise ValueError("path may not contain commas")
    return ["docker", "run", "--rm", "--init", "--label", containers.LABEL,
            "--label", containers.owner_label(owner),
            "--network", "none", "--user", "0:0",
            "--mount", f"type=bind,src={parent},dst=/parent",
            "--entrypoint", "/bin/sh", image,
            "-c", 'rm -rf -- "/parent/$1"', "sh", name]


def remove_tree(path: str | Path, *, allow_container: bool = True, image: str | None = None) -> None:
    """Delete a workspace, including the read-only spec/ and dev/ trees.

    A tree the solver container wrote belongs to the container's uid, which is
    not this host's user on every machine; when the plain removal fails for
    that reason the deletion is finished from a throwaway root container that
    mounts only the parent directory.
    """
    path = Path(path)
    if not path.exists():
        return
    for entry in sorted(path.rglob("*"), reverse=True):
        if entry.is_dir() and not entry.is_symlink():
            try:
                entry.chmod(0o755)
            except OSError:
                pass
    try:
        path.chmod(0o755)
    except OSError:
        pass
    try:
        shutil.rmtree(path)
        return
    except (PermissionError, OSError) as exc:
        if not allow_container or os.environ.get(NO_DOCKER_ENV):
            raise
        reason = exc
    result = subprocess.run(container_remove_command(path, image or "alpine:3"),
                            capture_output=True, text=True, timeout=300)
    if result.returncode or path.exists():
        raise PermissionError(
            f"{path} could not be removed on the host ({reason}) and the container fallback "
            f"failed: {(result.stdout or '') + (result.stderr or '')}".strip())


def populate(instance: Instance, destination: str | Path, *, corpus: Path | None = None,
             assets: Path | None = None, refcache: Path | None = None, require_reference: bool = True,
             stop_policy: str = "budget", budget_seconds: float | None = None,
             checkpoint_seconds: float | None = None) -> Path:
    """Create a fresh workspace tree. The destination must not already exist."""
    task = instance.task
    destination = Path(destination)
    if destination.exists():
        raise ValueError(f"workspace destination already exists: {destination}")
    corpus = Path(corpus or instance.corpus_public)
    assets = Path(assets or instance.assets)
    refcache = Path(refcache or instance.refcache)
    starter = task.task_dir / "starter"
    if starter.is_dir():
        shutil.copytree(starter, destination)
    else:
        destination.mkdir(parents=True)
    for name in ("TASK.md", "SPEC.md"):
        source = task.task_dir / name
        if source.is_file():
            shutil.copyfile(source, destination / name)
    task_text = (destination / "TASK.md").read_text() if (destination / "TASK.md").is_file() else ""
    if STOP_POLICY_MARKER not in task_text:
        raise ValueError(f"{task.task_dir / 'TASK.md'} has lost its {STOP_POLICY_MARKER!r} line")
    (destination / "TASK.md").write_text(task_text.replace(
        STOP_POLICY_MARKER, stopping_paragraph(stop_policy, budget_seconds, checkpoint_seconds)))
    if task.spec_dir and task.spec_dir.is_dir():
        shutil.copytree(task.spec_dir, destination / "spec")

    dev = destination / "dev"
    (dev / "cases").mkdir(parents=True)
    if task.format_doc and task.format_doc.is_file():
        shutil.copyfile(task.format_doc, dev / "CASE_FORMAT.md")
    for rel, source in (task.dev_extra or {}).items():
        target = dev / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    replays = sorted(corpus.glob("*.json"))
    if not replays:
        raise ValueError(f"no public cases in {corpus}")
    for replay in replays:
        shutil.copyfile(replay, dev / "cases" / replay.name)
    used = referenced_assets(corpus)
    if used:
        (dev / "assets").mkdir()
        for name in sorted(used):
            source = assets / name
            if not source.is_file():
                raise ValueError(f"public case references a missing asset: {name}")
            shutil.copyfile(source, dev / "assets" / name)

    channel = instance.scorer.primary_channel
    for replay in replays:
        name = replay.stem
        n1 = refcache / name / "n1"
        ledger = n1 / "ledger.json"
        if not ledger.is_file():
            if require_reference:
                raise ValueError(f"no reference output for {name}: build it with "
                                 f"`python3 -m evalbase.grader.cli refcache` (looked in {n1})")
            continue
        out = dev / "reference" / name
        out.mkdir(parents=True)
        events = json.loads(ledger.read_text()).get("events", [])
        for event in events:
            if event.get("op") != "snapshot":
                continue
            rel = (event.get("files") or {}).get(channel)
            if not rel:
                continue
            source = n1 / rel
            if not source.is_file():
                raise ValueError(f"reference output missing: {source}")
            shutil.copyfile(source, out / Path(rel).name)
            _write_preview(instance, n1, event, out / (Path(rel).stem + ".png"))

    _chmod_tree(destination, 0o777, 0o666)
    for locked in (destination / "spec", dev):
        if locked.exists():
            _chmod_tree(locked, 0o555, 0o444)
    destination.chmod(0o777)
    return destination


def _write_preview(instance: Instance, outdir: Path, event: dict, png: Path) -> None:
    """Preview PNG for a reference output, through the instance's scorer (if it has pictures)."""
    try:
        output = instance.scorer.load_output(str(outdir), event)
        rgb = instance.scorer.preview_rgb8(output) if output is not None else None
    except Exception:
        rgb = None
    if rgb is None:
        return
    from ..grader.png import write_png
    write_png(str(png), rgb)


# ------------------------------------------------------------------- export

def _visit(task, fd: int, relative: Path, selected: list[tuple[Path, bytes]], counters: dict) -> None:
    if len(relative.parts) > MAX_DEPTH:
        raise ValueError("source directory nesting exceeds 32 levels")
    for name in sorted(os.listdir(fd)):
        counters["entries"] += 1
        if counters["entries"] > MAX_ENTRIES:
            raise ValueError("too many entries in the source tree")
        rel = relative / name if str(relative) != "." else Path(name)
        if len(rel.parts) == 1 and name in EXCLUDED_TOP:
            continue
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"symlinks are not permitted in exported source: {rel}")
        if stat.S_ISDIR(info.st_mode):
            if name in ("__pycache__", ".git", "build"):
                continue
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                _visit(task, child, rel, selected, counters)
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"special files are not permitted in exported source: {rel}")
        if name not in task.source_names and Path(name).suffix.lower() not in task.source_suffixes:
            continue
        if not SAFE_NAME.fullmatch(str(rel)) or any(p.startswith("-") for p in rel.parts):
            raise ValueError(f"use simple source filenames (letters, digits, _ - . /): {rel}")
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            if not stat.S_ISREG(os.fstat(child).st_mode):
                raise ValueError("source changed to a special file during export")
            with os.fdopen(child, "rb", closefd=False) as f:
                data = f.read(MAX_FILE_BYTES + 1)
        finally:
            os.close(child)
        counters["total"] += len(data)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"source file exceeds 8 MiB: {rel}")
        if counters["total"] > MAX_TOTAL_BYTES or len(selected) >= MAX_FILES:
            raise ValueError("exported source exceeds the size or file-count limit")
        selected.append((rel, data))


def source_snapshot(instance: Instance, workspace: str | Path, destination: str | Path) -> str:
    """Freeze the candidate's source as read-only bytes and return its digest."""
    task = instance.task
    workspace = Path(workspace).resolve()
    destination = Path(destination)
    if destination.exists():
        raise ValueError("snapshot destination already exists")
    if destination.resolve().is_relative_to(workspace):
        raise ValueError("snapshot destination must be outside the workspace")
    selected: list[tuple[Path, bytes]] = []
    counters = {"entries": 0, "total": 0}
    rootfd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _visit(task, rootfd, Path("."), selected, counters)
    finally:
        os.close(rootfd)
    names = {str(rel) for rel, _ in selected}
    if task.required_names and not any(n in names for n in task.required_names):
        raise ValueError(f"none of {sorted(task.required_names)} is in the workspace: the evaluator "
                         f"rebuilds with `{task.build_command}`")
    sources = sum(Path(n).suffix.lower() in task.unit_suffixes for n in names)
    destination.mkdir(parents=True)
    hashes = {}
    for rel, data in sorted(selected):
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o444)
        hashes[str(rel)] = hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    manifest = {"files": hashes, "sha256": digest, "bytes": counters["total"],
                "compilation_units": sources, "created": time.time()}
    (destination / "source-manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return digest


def verify_snapshot(destination: str | Path) -> str:
    """Re-hash an exported snapshot; raises if the bytes no longer match."""
    destination = Path(destination)
    manifest = json.loads((destination / "source-manifest.json").read_text())
    for rel, want in manifest["files"].items():
        got = sha256_file(destination / rel)
        if got != want:
            raise ValueError(f"exported source changed on disk: {rel}")
    digest = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()
    if digest != manifest["sha256"]:
        raise ValueError("source manifest digest does not match its file list")
    return digest


# --------------------------------------------------------------- checkpoints

def checkpoint(instance: Instance, workspace: str | Path, destination: str | Path, *, index: int,
               note: str = "", elapsed: float | None = None) -> dict:
    """Snapshot the workspace source + NOTES.md into <destination>/source.tar.gz + manifest."""
    workspace, destination = Path(workspace), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / "source"
    if staging.exists():
        shutil.rmtree(staging)
    digest = source_snapshot(instance, workspace, staging)
    archive = destination / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=str(Path("source") / path.relative_to(staging)))
    notes = staging / "NOTES.md"
    manifest = {
        "index": index,
        "note": note[:16000],
        "source_sha256": digest,
        "archive_sha256": sha256_file(archive),
        "archive": archive.name,
        "files": json.loads((staging / "source-manifest.json").read_text())["files"],
        "notes_present": notes.is_file(),
        "notes_sha256": sha256_file(notes) if notes.is_file() else None,
        "elapsed_seconds": None if elapsed is None else round(elapsed, 3),
        "created": time.time(),
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (destination / "checkpoint.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest
