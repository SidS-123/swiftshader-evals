"""Shared attempt bookkeeping: manifests, budgets, export, final grading,
checkpoint selection and the progression curve.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NamedTuple

from ..grader import containers
from ..interfaces import Instance
from . import tools, workspace as ws
from .privacy import POLICY

HARNESS_VERSION = "evalbase-harness/1.0"
HARNESSES = ("claude-code", "codex", "opencode", "openrouter")
REASONING = ("low", "medium", "high", "xhigh")
STOP_POLICIES = ("budget", "submit")
SANDBOXES = ("docker", "none")
STOP_REASONS = ("budget_wall", "budget_cost", "budget_tokens", "model_final",
                "operator_interrupt", "context_failure", "infrastructure", "rate_limited")
PRIVATE_FILES = ("events.private.jsonl", "stderr.private.log", "state.private.json",
                 "claude-mcp.private.json", "codex-models.private.json",
                 "opencode-config.private.json", "launch.private.json",
                 "tools-state.json")


@dataclass
class Options:
    harness: str
    model: str
    budget_hours: float = 12.0
    checkpoint_minutes: float = 15.0
    max_cost_usd: float = 0.0
    max_tokens: int = 0
    reasoning: str | None = None
    # budget: a final answer ends a CLI segment, never the attempt.
    # submit: a confirmed final answer ends the attempt; the wall budget is a cap.
    stop_policy: str = "budget"
    # CLI harnesses only: how long to wait out a provider usage/rate limit.
    # This time is recorded separately and never counts as solver time.
    rate_limit_wait_hours: float = 6.0
    corpus_hidden: str | None = None
    refcache_hidden: str | None = None
    seed: int = 0
    cpus: str = "4"
    memory: str = "8g"
    # docker (the measurement configuration) or none (test-only, no isolation)
    sandbox: str = "docker"
    # OpenRouter-only knobs; recorded for every harness so resume is exact.
    context_chars: int = 400000
    max_output_tokens: int = 32000
    max_requests: int = 100000

    def validate(self):
        if self.harness not in HARNESSES:
            raise ValueError(f"--harness must be one of {HARNESSES}")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("an exact --model id is required; there is no default")
        for name in ("budget_hours", "checkpoint_minutes"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"positive --{name.replace('_', '-')} required")
        if self.reasoning is not None and self.reasoning not in REASONING:
            raise ValueError(f"--reasoning must be one of {REASONING}")
        if self.stop_policy not in STOP_POLICIES:
            raise ValueError(f"--stop-policy must be one of {STOP_POLICIES}")
        if self.stop_policy != "budget" and self.harness == "openrouter":
            raise ValueError("--stop-policy submit is implemented for the CLI harnesses only")
        if self.sandbox not in SANDBOXES:
            raise ValueError(f"--sandbox must be one of {SANDBOXES}")
        if not isinstance(self.rate_limit_wait_hours, (int, float)) \
                or not math.isfinite(self.rate_limit_wait_hours) or self.rate_limit_wait_hours < 0:
            raise ValueError("--rate-limit-wait must be a non-negative number of hours")
        for name in ("max_tokens", "max_requests", "max_output_tokens", "context_chars"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"non-negative integer required: {name}")
        if not isinstance(self.max_cost_usd, (int, float)) or self.max_cost_usd < 0:
            raise ValueError("--max-cost-usd must be non-negative")
        if self.harness != "openrouter" and (self.max_cost_usd or self.max_tokens):
            raise ValueError("cost and token caps apply only to the openrouter harness; "
                             "CLI harnesses bill the operator's own subscription")
        return self

    @property
    def budget_seconds(self) -> float:
        return float(self.budget_hours) * 3600.0

    @property
    def checkpoint_seconds(self) -> float:
        return float(self.checkpoint_minutes) * 60.0

    @property
    def rate_limit_wait_seconds(self) -> float:
        return float(self.rate_limit_wait_hours) * 3600.0


def iso(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t if t is not None else time.time()))


class GradingTarget(NamedTuple):
    """A grading corpus and the refcache that was built from that same corpus."""
    corpus: Path
    refcache: Path
    label: str


def require_refcache(corpus: Path, refcache: Path) -> None:
    """Fail before the rebuild if the refcache was not built from this corpus."""
    replays = sorted(Path(corpus).glob("*.json"))
    if not replays:
        raise ValueError(f"no cases to grade in {corpus}")
    ledger = Path(refcache) / replays[0].stem / "refcache.json"
    if not ledger.is_file():
        raise ValueError(
            f"refcache {refcache} has no entry for case {replays[0].stem} "
            f"(looked for {ledger}); the corpus and the refcache must be a matching "
            f"pair. Build it with `python3 -m evalbase.grader.cli --corpus {corpus} "
            f"--cache {refcache} refcache`.")


def grading_target(instance: Instance, options: Options) -> GradingTarget:
    """Pick the grading corpus and the refcache that belongs to it.

    The hidden corpus is graded against the hidden refcache, the public corpus
    against the public refcache; the two are never mixed.
    `--corpus-hidden`/`--refcache-hidden` override the pair.
    """
    if options.corpus_hidden:
        corpus, label = Path(options.corpus_hidden), "hidden"
        if not any(corpus.glob("*.json")):
            raise ValueError(f"--corpus-hidden has no cases: {corpus}")
    else:
        corpus, label = Path(instance.corpus_hidden), "hidden"
        if not any(corpus.glob("*.json")):
            corpus, label = Path(instance.corpus_public), "public (no hidden corpus on this host)"
    refcache = (Path(options.refcache_hidden or instance.refcache_hidden) if label == "hidden"
                else Path(instance.refcache))
    require_refcache(corpus, refcache)
    return GradingTarget(corpus, refcache, label)


def sweep_containers(stage: str, owner: str) -> list[dict]:
    """Kill any container *this attempt* left behind, and say which.

    `owner` is not optional: this sweep used to select on the managed label
    alone, which made it a weapon against a parallel attempt.
    """
    killed = containers.kill_stale_containers(owner)
    if killed:
        print(f"[sweep:{stage}] killed {len(killed)} leftover container(s) of {owner}", flush=True)
    return killed


def incident_kinds(manifest: dict) -> list[str]:
    seen = []
    for entry in manifest.get("infrastructure_incidents") or ():
        kind = (entry or {}).get("kind") or "infrastructure_incident"
        if kind not in seen:
            seen.append(kind)
    return seen


def apply_validity(manifest: dict, harness_ok: bool) -> bool:
    """Set `measurement_valid` and the reasons it is not.

    An attempt whose solver container was lost and rebuilt under it did not
    measure what it claims to measure; such a run is reported as
    `measurement_valid: false` with `sandbox_lost` among its reasons, whatever
    else about it looks clean.
    """
    reasons = list(manifest.get("measurement_invalid_reasons") or [])
    if not harness_ok and "harness_criteria" not in reasons:
        reasons.append("harness_criteria")
    for kind in incident_kinds(manifest):
        if kind not in reasons:
            reasons.append(kind)
    manifest["measurement_invalid_reasons"] = reasons
    manifest["measurement_valid"] = not reasons
    return manifest["measurement_valid"]


def base_manifest(instance: Instance, options: Options, *, simulated: bool, name: str) -> dict:
    sweep_containers("attempt-start", name)
    return {
        "attempt": name,
        "owner": name,
        "instance": instance.name,
        "harness": options.harness,
        "harness_version": HARNESS_VERSION,
        "stop_policy": options.stop_policy,
        "sandbox": options.sandbox,
        "simulated_model": simulated,
        "model_requested": options.model,
        "models_served": [],
        "reasoning_requested": options.reasoning,
        "reasoning_served": None,
        "seeds": {"attempt_seed": options.seed,
                  "corpus_seeds": "fixed in the frozen corpus; the harness adds no sampling seed",
                  "model_sampling": "provider default; the harness does not set temperature"},
        "options": asdict(options),
        "budget": {"hours": options.budget_hours, "seconds": options.budget_seconds,
                   "checkpoint_seconds": options.checkpoint_seconds,
                   "max_cost_usd": options.max_cost_usd or None,
                   "max_tokens": options.max_tokens or None,
                   "rate_limit_wait_seconds": options.rate_limit_wait_seconds,
                   "total_wall_cap_seconds": options.budget_seconds + options.rate_limit_wait_seconds},
        "task_version": ws.version_record(instance),
        "started": iso(), "started_unix": time.time(),
        "ended": None, "ended_unix": None,
        "solver_seconds": 0.0, "grading_seconds": 0.0,
        "stop_reason": None,
        "model_final_events": 0,
        "rate_limit_wait_seconds": 0.0,
        "rate_limit_waits": [],
        "submission": {"confirmations": 0, "declared_final": False, "declared_at_segment": None},
        "usage": {"input_tokens": None, "output_tokens": None, "cost_usd": None, "source": None},
        "compaction_events": [],
        "checkpoints": [],
        "tool_calls": {},
        "final_source_sha256": None,
        "grade_report_path": None,
        "grade": None,
        "status": "starting",
        "infrastructure_incidents": [],
        "measurement_valid": None,
        "measurement_invalid_reasons": [],
        "redaction_policy": POLICY,
        "private_files": list(PRIVATE_FILES),
    }


def secure(path: Path) -> Path:
    path = Path(path)
    path.touch()
    path.chmod(0o600)
    return path


def export_and_grade(instance: Instance, run_dir: Path, workspace: Path, options: Options, manifest: dict,
                     redactor, *, grade: bool = True, save=lambda manifest: None) -> dict:
    """Freeze the source, rebuild it cleanly and score it with the real grader.

    `save` persists `manifest` at each status change so a manifest read from
    disk mid-grade never lags the in-memory one.
    """
    run_dir, workspace = Path(run_dir), Path(workspace)
    owner = manifest.get("owner") or manifest.get("attempt") or run_dir.name
    submission = run_dir / "submission"
    started = time.monotonic()
    if not submission.exists():
        try:
            manifest["final_source_sha256"] = ws.source_snapshot(instance, workspace, submission)
        except (ValueError, OSError) as exc:
            manifest.update(status="complete", grade={"overall": 0.0, "full_success": False,
                                                      "build_failure": True},
                            source_export_error=redactor.text(exc))
            save(manifest)
            return manifest
    else:
        manifest["final_source_sha256"] = ws.verify_snapshot(submission)
    if not grade:
        manifest["status"] = "solver_finished"
        save(manifest)
        return manifest
    target = grading_target(instance, options)
    manifest["graded_corpus"] = target.label
    manifest["graded_refcache"] = str(target.refcache)
    manifest["status"] = "grading"
    save(manifest)
    out = run_dir / "grade"
    sweep_containers("before-grade", owner)
    try:
        report = tools.build_and_grade(instance, submission, out, corpus=target.corpus,
                                       cache=target.refcache, label=manifest["attempt"],
                                       owner=owner, sandbox=options.sandbox)
    except Exception:
        save(manifest)
        raise
    finally:
        sweep_containers("after-grade", owner)
    manifest["grade_report_path"] = str((out / "report.json").relative_to(run_dir))
    manifest["grade"] = {"overall": report["aggregate"]["overall"],
                         "full_success": report["aggregate"].get("full_success", False),
                         "categories": {k: v["score"] for k, v in report["aggregate"].get("categories", {}).items()},
                         "build_failure": report.get("build_failure", False),
                         "cases": {c["name"]: c["score"] for c in report.get("cases", [])}}
    manifest["grading_seconds"] = round(manifest.get("grading_seconds", 0.0) + time.monotonic() - started, 3)
    manifest["status"] = "complete"
    save(manifest)
    sweep_containers("attempt-end", owner)
    return manifest


# ------------------------------------------------- checkpoint selection

# Checkpoints carry no trigger field of their own; the note the harness writes
# is the record of who asked for one.
AUTO_NOTES = ("Automatic checkpoint", "Before context compaction", "Attempt start or resume")
FINAL_NOTES = ("Final source", "Final source at the end of the attempt")


def checkpoint_trigger(name: str, note: str) -> str:
    """auto (harness timer), final (the graded source) or tool (the model asked)."""
    note = (note or "").strip()
    if name == "final" or note in FINAL_NOTES:
        return "final"
    if note in AUTO_NOTES:
        return "auto"
    return "tool"


def read_checkpoints(run_dir: Path) -> list[dict]:
    """Every checkpoint on disk, in order, with its trigger and wall time."""
    run_dir = Path(run_dir)
    start = None
    manifest = run_dir / "attempt.json"
    if manifest.is_file():
        try:
            start = json.loads(manifest.read_text()).get("started_unix")
        except (ValueError, OSError):
            start = None
    rows = []
    for directory in sorted((run_dir / "checkpoints").glob("*")):
        record = directory / "checkpoint.json"
        if not record.is_file():
            continue
        try:
            entry = json.loads(record.read_text())
        except ValueError:
            continue
        elapsed = entry.get("elapsed_seconds")
        if elapsed is None and entry.get("created") and start:
            elapsed = entry["created"] - start
        note = entry.get("note") or ""
        rows.append({"index": entry.get("index"), "id": directory.name, "path": directory,
                     "trigger": checkpoint_trigger(directory.name, note),
                     "source_sha256": entry.get("source_sha256"),
                     "elapsed_seconds": None if elapsed is None else round(float(elapsed), 3),
                     "created": entry.get("created"), "note": note[:400]})
    return rows


def parse_checkpoint_selection(spec) -> set | None:
    """Normalise a `--only` selection: indices (3), ids (000012) or `final`."""
    if spec is None:
        return None
    tokens = spec.split(",") if isinstance(spec, str) else list(spec)
    wanted: set = set()
    for raw in tokens:
        token = str(raw).strip()
        if not token:
            continue
        wanted.add(token)
        try:
            wanted.add(int(token))
        except ValueError:
            pass
    if not wanted:
        raise ValueError("--only selected nothing: pass checkpoint indices (3,7), "
                         "checkpoint ids (000012) or final")
    return wanted


def select_checkpoints(rows, *, every: float | None = None, auto_only: bool = False,
                       dedupe: bool = False, max_points: int | None = None,
                       indices=None) -> tuple[list[dict], list[dict]]:
    """Choose which checkpoints to grade. Returns (selected, skipped).

    Filters compose, in this order: `indices` (--only), `auto_only`, `every`
    (keep the last checkpoint of each wall-time bucket of that many minutes),
    then `max_points` (thin evenly). The first checkpoint and the final one are
    anchors every mode keeps. `dedupe` is applied last and drops nothing -- a
    checkpoint whose source sha256 equals the previously selected one stays in
    the curve carrying `reused_from`, and is not rebuilt.
    """
    rows = list(rows)
    if not rows:
        return [], []
    keep = [True] * len(rows)
    reason: list[str | None] = [None] * len(rows)
    final_index = next((i for i, r in enumerate(rows) if r["trigger"] == "final"), len(rows) - 1)
    anchors = {0, len(rows) - 1, final_index}

    def drop(i: int, why: str):
        keep[i], reason[i] = False, why

    if indices:
        wanted = parse_checkpoint_selection(indices)
        anchors = set()
        for i, row in enumerate(rows):
            if row["index"] not in wanted and row["id"] not in wanted:
                drop(i, "not in --only")
        if not any(keep):
            raise ValueError(
                "--only matched no checkpoint of the "
                f"{len(rows)} on disk (ids {rows[0]['id']}..{rows[-1]['id']}); "
                "pass an index, a checkpoint id or final")
    if auto_only:
        for i, row in enumerate(rows):
            if keep[i] and i not in anchors and row["trigger"] != "auto":
                drop(i, f"trigger is {row['trigger']}, not auto (--auto-only)")
    if every:
        width = float(every) * 60.0
        if width <= 0:
            raise ValueError("--every must be a positive number of minutes")
        last_of_bucket = {}
        for i, row in enumerate(rows):
            if keep[i]:
                last_of_bucket[int((row["elapsed_seconds"] or 0.0) // width)] = i
        chosen = set(last_of_bucket.values())
        for i in range(len(rows)):
            if keep[i] and i not in chosen and i not in anchors:
                drop(i, f"not the last checkpoint in its {float(every):g}-minute bucket")
    if max_points and sum(keep) > max_points:
        kept = [i for i in range(len(rows)) if keep[i]]
        n = max(int(max_points), 1)
        pick = ({kept[round(j * (len(kept) - 1) / (n - 1))] for j in range(n)} if n > 1
                else {kept[-1]})
        pick |= {i for i in anchors if keep[i]}
        floor = max(n, len({i for i in anchors if keep[i]}))
        while len(pick) > floor:
            droppable = [i for i in sorted(pick) if i not in anchors]
            if not droppable:
                break
            pick.discard(droppable[len(droppable) // 2])
        for i in kept:
            if i not in pick:
                drop(i, f"thinned to --max {n}")

    selected, skipped, previous = [], [], None
    for i, row in enumerate(rows):
        row = dict(row)
        if not keep[i]:
            row["reason"] = reason[i]
            skipped.append(row)
            continue
        row["reused_from"] = None
        if (dedupe and previous is not None and row["source_sha256"]
                and row["source_sha256"] == previous["source_sha256"]):
            row["reused_from"] = previous["id"]
        else:
            previous = row
        selected.append(row)
    return selected, skipped


def curve_row(row: dict, extra: dict | None = None) -> dict:
    """One point of the progression curve: who, when, from what source."""
    point = {"index": row["index"], "id": row["id"],
             "elapsed_seconds": row["elapsed_seconds"], "trigger": row["trigger"],
             "source_sha256": row["source_sha256"]}
    point.update(extra or {})
    return point


def grade_checkpoints(instance: Instance, run_dir: Path, options: Options | None = None, *, corpus=None,
                      cache=None, indices=None, every: float | None = None,
                      auto_only: bool = False, dedupe: bool = False,
                      max_points: int | None = None, dry_run: bool = False) -> list[dict]:
    """Rebuild and grade the selected checkpoints: an attempt's progression curve.

    With no selection options every checkpoint is graded. `dry_run` resolves
    the selection and returns the curve rows without a grade; it reads the
    attempt directory and writes nothing.
    """
    run_dir = Path(run_dir)
    owner = run_dir.name
    rows = read_checkpoints(run_dir)
    selected, skipped = select_checkpoints(rows, every=every, auto_only=auto_only,
                                           dedupe=dedupe, max_points=max_points, indices=indices)
    order = [row["id"] for row in rows]
    if dry_run:
        points = {r["id"]: curve_row(r, {"grade_reused": r["reused_from"] is not None,
                                         "reused_from": r["reused_from"],
                                         "would_grade": r["reused_from"] is None})
                  for r in selected}
        points |= {r["id"]: curve_row(r, {"skipped": r["reason"]}) for r in skipped}
        return [points[name] for name in order]
    if options is None:
        config_path = run_dir / "agent-config.json"
        if config_path.is_file():
            stored = json.loads(config_path.read_text()).get("options") or {}
            options = Options(**{k: v for k, v in stored.items() if k in Options.__dataclass_fields__})
    sandbox = options.sandbox if options else "docker"
    if corpus:
        target = GradingTarget(Path(corpus), Path(cache or instance.refcache), "explicit")
        require_refcache(target.corpus, target.refcache)
    else:
        if options is None:
            raise ValueError(f"no agent-config.json in {run_dir}")
        target = grading_target(instance, options)
    results, graded = {}, {}
    for row in selected:
        index, directory = row["index"], row["path"]
        if row["reused_from"] is not None and row["reused_from"] in graded:
            results[row["id"]] = curve_row(row, graded[row["reused_from"]]
                                           | {"grade_reused": True, "reused_from": row["reused_from"]})
            continue
        source = directory / "source"
        if not source.is_dir():
            results[row["id"]] = curve_row(row, {"error": "checkpoint has no exported source"})
            continue
        out = directory / "grade"
        if (out / "report.json").is_file():
            report = json.loads((out / "report.json").read_text())
        else:
            try:
                report = tools.build_and_grade(instance, source, out, corpus=target.corpus,
                                               cache=target.refcache,
                                               label=f"checkpoint-{index:06d}", owner=owner,
                                               sandbox=sandbox)
            finally:
                sweep_containers(f"after-checkpoint-{index:06d}", owner)
        grade = {"overall": report["aggregate"]["overall"],
                 "full_success": report["aggregate"].get("full_success", False),
                 "build_failure": report.get("build_failure", False),
                 "report": str((out / "report.json").relative_to(run_dir))}
        graded[row["id"]] = grade
        results[row["id"]] = curve_row(row, grade | {"grade_reused": False, "reused_from": None})
    results |= {row["id"]: curve_row(row, {"skipped": row["reason"]}) for row in skipped}
    curve = [results[name] for name in order]
    (run_dir / "checkpoint-curve.json").write_text(json.dumps(curve, indent=1) + "\n")
    return curve
