"""Run cases through the instance's driver and grade the outputs.

The container half is `containers.py`. Everything here runs on the host; the driver decides where the
candidate runs. The reference cache and the report layout are the same for
every instance:

    <cache>/<case>/refcache.json         thresholds, timings, identity stamps
    <cache>/<case>/n1/, n2/, <perturbation>/   the oracle's runs + ledgers
    <run>/cases/<case>/                  the candidate's outputs + ledger
    <run>/report.json                    aggregate + per-case detail

A refcache entry is a recording of one specific oracle's output for one
specific case file, not "the correct answer". Every entry records
`replay_sha256` (of the case file's bytes) and the asset names it uses; an
entry built from a different file is refused at grading time rather than
scored against.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
from dataclasses import dataclass, field

import numpy as np

from ..interfaces import CaseMeta, Instance, MetricSpec, read_case_meta
from . import containers, jsonio, metrics

# Re-exported so callers (and tests) that patch ownership helpers on the runner
# module keep working.
LABEL = containers.LABEL
OWNER_KEY = containers.OWNER_KEY
process_owner = containers.process_owner
resolve_owner = containers.resolve_owner
owner_label = containers.owner_label
container_name = containers.container_name
signal_name = containers.signal_name
kill_container = containers.kill_container
kill_stale_containers = containers.kill_stale_containers
list_managed_containers = containers.list_managed_containers
_age_seconds = containers._age_seconds


# ------------------------------------------------------------------ driving

def drive(instance: Instance, replay_path: str, outdir: str, assets_dir: str,
          lib_dir: str | None = None, sample_mult: int = 1, timeout_s: float = 600.0,
          cpus: str | None = None, mem: str = "8g", perturb: str = "",
          extra_mounts=(), extra_env: dict | None = None, owner: str | None = None):
    """Run one case through the instance's driver. lib_dir=None selects the oracle.

    A single dispatch point so that tests can replace it, and so a control's
    extra mounts and environment reach the driver from exactly one place.
    """
    os.makedirs(outdir, exist_ok=True)
    return instance.driver.run(replay_path, outdir, assets_dir, candidate=lib_dir,
                               sample_mult=sample_mult,
                               perturbation=perturb if lib_dir is None else "",
                               timeout_s=timeout_s, cpus=cpus, memory=mem,
                               extra_mounts=extra_mounts, extra_env=extra_env, owner=owner)


def snapshots(ledger: dict) -> list[dict]:
    return [e for e in ledger.get("events", []) if e.get("op") == "snapshot"]


def run_events(ledger: dict) -> list[dict]:
    return [e for e in ledger.get("events", []) if e.get("op") == "run"]


def load_snapshot(instance: Instance, outdir: str, event: dict, channel: str | None = None):
    """One snapshot's primary output, through the instance's scorer; None if absent."""
    if not event:
        return None
    try:
        return instance.scorer.load_output(outdir, event, channel)
    except Exception:
        return None


def _distance(instance: Instance):
    return instance.scorer.distance


# ------------------------------------------------------------------ refcache

def replay_identity(replay_path: str) -> dict:
    """Identity of a case file: sha256 of its bytes and the asset names it uses.

    Asset names are content hashes when the corpus writer produced them, so
    listing them is enough; the binaries are not re-hashed.
    """
    with open(replay_path, "rb") as f:
        raw = f.read()
    ident = {"replay_sha256": hashlib.sha256(raw).hexdigest(), "assets": []}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return ident
    if isinstance(doc, dict):
        ident["assets"] = sorted((doc.get("assets") or {}).keys())
        for key in ("generator", "generator_version", "generator_stamp"):
            if key in doc:
                ident["generator_stamp"] = doc[key]
                break
    return ident


def replay_snapshots(replay_path: str) -> int:
    """Snapshots a case produces: one per snapshot op. Matches refcache "snapshots"."""
    with open(replay_path) as f:
        doc = json.load(f)
    return sum(1 for op in doc.get("ops", []) if op.get("op") == "snapshot")


def build_refcache(instance: Instance, replay_path: str, cache_dir: str, assets_dir: str,
                   cpus: str | None = None, owner: str | None = None) -> dict:
    """Run the oracle at N and 2N plus every `metric.perturbations` mode.

    Every perturbation is run and its mean defect against the N-snapshot
    reference recorded, but only `metric.tolerance_perturbations` feed the
    threshold. The others are kept as fair-candidate numbers.
    """
    spec = instance.metric
    distance = _distance(instance)
    name = os.path.splitext(os.path.basename(replay_path))[0]
    d1 = os.path.join(cache_dir, name, "n1")
    d2 = os.path.join(cache_dir, name, "n2")
    r1 = drive(instance, replay_path, d1, assets_dir, None, 1, cpus=cpus, owner=owner)
    if r1.exit != "ok":
        raise RuntimeError(f"reference failed on {name}: {r1.exit} {r1.stderr_tail}")
    r2 = drive(instance, replay_path, d2, assets_dir, None, 2, cpus=cpus, owner=owner)
    if r2.exit != "ok":
        raise RuntimeError(f"reference 2N failed on {name}: {r2.exit}")
    s1, s2 = snapshots(r1.ledger), snapshots(r2.ledger)
    f1 = [load_snapshot(instance, d1, e) for e in s1]
    f2 = [load_snapshot(instance, d2, e) for e in s2]
    if any(f is None for f in f1):
        raise RuntimeError(f"reference produced missing snapshots on {name}")
    by_mode = {}
    for mode in spec.perturbations:
        dp = os.path.join(cache_dir, name, mode)
        rp = drive(instance, replay_path, dp, assets_dir, None, 1, cpus=cpus, perturb=mode, owner=owner)
        if rp.exit != "ok":
            raise RuntimeError(f"reference {mode} failed on {name}: {rp.exit}")
        by_mode[mode] = [load_snapshot(instance, dp, e) for e in snapshots(rp.ledger)]
    perturbed = [by_mode[m] for m in spec.tolerance_perturbations if m in by_mode]
    f2_ok = f2 if len(f2) == len(f1) and all(f is not None for f in f2) else None
    thr = metrics.replay_threshold(distance, f1, f2_ok, perturbed, spec)
    info = {
        "replay": name,
        "metric_version": spec.version,
        "perturbations": list(spec.perturbations),
        "tolerance_perturbations": list(spec.tolerance_perturbations),
        "snapshots": len(f1),
        "motion_p90": thr.motion_p90,
        "noise": thr.noise,
        "sensitivity": thr.sensitivity,
        "threshold": thr.threshold,
        "perturbation_defects": {m: metrics.mean_snapshot_defect(distance, f1, by_mode[m])
                                 for m in spec.perturbations},
        "reference_run_seconds": instance.scorer.timings(r1.ledger),
        "reference_wall_seconds": r1.wall_seconds,
        "host": {"machine": platform.machine(), "system": platform.system(), "cpus": os.cpu_count()},
        "image": instance.reference_image,
    }
    info.update(replay_identity(replay_path))
    # strict: an entry whose threshold is not a number must stop the build, not
    # be cached as null for every later run to score against.
    jsonio.write_json(os.path.join(cache_dir, name, "refcache.json"), info, strict=True)
    return info


def cache_snapshots(instance: Instance, cdir: str, mode: str) -> list | None:
    """One refcache run's snapshots in snapshot order, or None if absent."""
    ledger_path = os.path.join(cdir, mode, "ledger.json")
    if not os.path.exists(ledger_path):
        return None
    with open(ledger_path) as f:
        ledger = json.load(f)
    return [load_snapshot(instance, os.path.join(cdir, mode), e) for e in snapshots(ledger)]


def recalibrate_entry(instance: Instance, cdir: str) -> dict:
    """Recompute one entry's threshold from its outputs, no driver run.

    When a metric version changes which runs set T (not which are
    run), the new threshold is recoverable from the snapshots already in the
    cache. Returns the refcache.json fields that change.
    """
    spec = instance.metric
    distance = _distance(instance)
    f1 = cache_snapshots(instance, cdir, "n1")
    if not f1 or any(f is None for f in f1):
        raise RuntimeError(f"{cdir}: no usable n1 output to recalibrate from")
    f2 = cache_snapshots(instance, cdir, "n2")
    if f2 is not None and (len(f2) != len(f1) or any(f is None for f in f2)):
        f2 = None
    by_mode = {m: cache_snapshots(instance, cdir, m) for m in spec.perturbations}
    perturbed = [by_mode[m] for m in spec.tolerance_perturbations if by_mode.get(m)]
    thr = metrics.replay_threshold(distance, f1, f2, perturbed, spec)
    return {"metric_version": spec.version,
            "perturbations": list(spec.perturbations),
            "tolerance_perturbations": list(spec.tolerance_perturbations),
            "motion_p90": thr.motion_p90, "noise": thr.noise,
            "sensitivity": thr.sensitivity, "threshold": thr.threshold,
            "perturbation_defects": {m: metrics.mean_snapshot_defect(distance, f1, by_mode.get(m))
                                     for m in spec.perturbations}}


def entry_threshold(instance: Instance, cdir: str, rc: dict) -> tuple[float, str | None]:
    """The case's threshold under the metric of record, and why if it moved.

    A cache entry stamped with this metric version is used as it stands. An
    older one is recalibrated from its own cached outputs (no driver run, no
    write): scoring a candidate against an older tolerance would be scoring it
    against a tolerance this metric does not grant.
    """
    version = instance.metric.version
    if rc.get("metric_version") == version:
        return float(rc["threshold"]), None
    was = rc.get("metric_version") or "unstamped"
    try:
        fields = recalibrate_entry(instance, cdir)
    except (RuntimeError, OSError, ValueError) as exc:
        return float(rc["threshold"]), (
            f"refcache entry was calibrated by metric v{was} and could not be "
            f"recalibrated ({exc}); its stored threshold was used as it stands")
    return fields["threshold"], (
        f"refcache entry was calibrated by metric v{was}; T recomputed offline from its "
        f"cached outputs under v{version} ({rc.get('threshold')} -> {fields['threshold']})")


# ------------------------------------------------------------------ grading

@dataclass
class ReplayGrade:
    name: str
    category: str
    family: str
    score: float
    detail: dict = field(default_factory=dict)


def performance_ratio(instance: Instance, case: CaseMeta, rc: dict, cand_ledger: dict,
                      exited_ok: bool, replay_score: float) -> float:
    """t_candidate / t_reference for the timed run, or inf when unmeasurable.

    A performance case must also match the reference's output (score >= 0.5),
    or the ratio is inf: a fast wrong candidate earns nothing.
    """
    ref_t = rc.get("reference_run_seconds") or []
    cand_t = instance.scorer.timings(cand_ledger)
    idx = case.meta.get("timed_run_index", len(ref_t) - 1)
    if (exited_ok and 0 <= idx < len(cand_t) and idx < len(ref_t) and replay_score >= 0.5
            and ref_t[idx] > 0):
        return cand_t[idx] / ref_t[idx]
    return float("inf")


def grade_replay(instance: Instance, replay_path: str, cache_dir: str, assets_dir: str,
                 lib_dir: str | None, out_root: str, cpus: str | None = None,
                 extra_mounts=(), extra_env: dict | None = None,
                 owner: str | None = None) -> ReplayGrade:
    spec = instance.metric
    case = read_case_meta(replay_path)
    name = case.name
    cdir = os.path.join(cache_dir, name)
    with open(os.path.join(cdir, "refcache.json")) as f:
        rc = json.load(f)
    # The refcache entry must have been built from this exact case file, or the
    # candidate would be compared against reference outputs of other content.
    cached_sha = rc.get("replay_sha256")
    replay_sha = replay_identity(replay_path)["replay_sha256"]
    if cached_sha and cached_sha != replay_sha:
        raise RuntimeError(
            f"refcache entry for {name} was built from a different case file: "
            f"refcache replay_sha256={cached_sha} but {os.path.abspath(replay_path)} "
            f"is {replay_sha}. Rebuild the entry "
            f"(python3 -m evalbase.grader.cli --corpus <corpus> --cache {cache_dir} refcache --only {name}).")
    with open(os.path.join(cdir, "n1", "ledger.json")) as f:
        ref_ledger = json.load(f)
    ref_snaps = [load_snapshot(instance, os.path.join(cdir, "n1"), e) for e in snapshots(ref_ledger)]
    threshold, recal = entry_threshold(instance, cdir, rc)
    timeout = max(60.0, 10.0 * float(rc.get("reference_wall_seconds") or 0.0))
    outdir = os.path.join(out_root, name)
    if os.path.exists(outdir):
        shutil.rmtree(outdir)
    res = drive(instance, replay_path, outdir, assets_dir, lib_dir, 1, timeout_s=timeout, cpus=cpus,
                extra_mounts=extra_mounts, extra_env=extra_env, owner=owner)
    cand_snaps = [load_snapshot(instance, outdir, e) for e in snapshots(res.ledger)]
    rep = metrics.score_replay(_distance(instance), ref_snaps, cand_snaps, threshold, spec.hill_n)
    # `exit` collapses everything that is not ok/driver_error/timeout into
    # "crash"; the returncode and signal say which crash it was (139 is the
    # candidate's own segfault, 137 is something outside the container).
    detail = {"exit": res.exit, "returncode": res.returncode,
              "signal": None if res.timed_out else containers.signal_name(res.returncode),
              "stderr_tail": res.stderr_tail, "candidate_wall_seconds": res.wall_seconds,
              "reference_wall_seconds": rc.get("reference_wall_seconds"), "threshold": threshold,
              "snapshot_defects": rep["snapshot_defects"], "snapshot_scores": rep["snapshot_scores"],
              "first_diverge_snapshot": rep["first_diverge_snapshot"]}
    if not cached_sha:
        detail["refcache_unstamped"] = True
    if recal:
        detail["threshold_recalibrated"] = recal
    score = rep["score"]
    if case.category == spec.performance_category:
        ratio = performance_ratio(instance, case, rc, res.ledger, res.exit == "ok", rep["score"])
        detail["ratio"] = ratio
        detail["replay_score"] = rep["score"]
        score = metrics.perf_score(ratio, spec.perf_half, spec.hill_n)
    elif case.category == spec.procedural_category:
        checks = instance.scorer.procedural_checks(case, ref_ledger, res.ledger,
                                                   os.path.join(cdir, "n1"), outdir, threshold)
        detail["checks"] = checks
        score = float(np.mean([c["ok"] for c in checks])) if checks else 0.0
        detail["replay_score"] = rep["score"]
    return ReplayGrade(name, case.category, case.family, score, detail)


grade_case = grade_replay


# ------------------------------------------------------------------ aggregate

def aggregate(spec: MetricSpec, grades: list[ReplayGrade]) -> dict:
    """Weighted overall score.

    Categories with no graded case (a subset run) are dropped and the weights
    renormalised over the categories present, so a partial run is not silently
    scored 0 for what it did not run. `weight` is the nominal weight,
    `effective_weight` the one used. The fidelity category is the mean over
    families of the mean over each family's cases; the others are means over
    cases. Full success is judged only over the categories present; a run with
    no case at all is never a full success.
    """
    weights = spec.weights
    cats: dict[str, list[ReplayGrade]] = {cat: [] for cat in weights}
    for g in grades:
        cats.setdefault(g.category, []).append(g)
    present = [cat for cat in weights if cats.get(cat)]
    total_w = sum(weights[cat] for cat in present)
    out = {"categories": {}, "overall": 0.0, "n": len(grades),
           "categories_present": present, "weights_renormalised": bool(present) and total_w < 0.999}
    for cat, w in weights.items():
        gs = cats.get(cat, [])
        if cat == spec.fidelity_category:
            fams: dict[str, list[float]] = {}
            for g in gs:
                fams.setdefault(g.family, []).append(g.score)
            score = float(np.mean([np.mean(v) for v in fams.values()])) if fams else 0.0
        else:
            score = float(np.mean([g.score for g in gs])) if gs else 0.0
        eff = (w / total_w) if (gs and total_w > 0) else 0.0
        out["categories"][cat] = {"score": score, "weight": w, "effective_weight": eff, "n": len(gs),
                                  "cases": {g.name: g.score for g in gs}}
        out["overall"] += eff * score

    def bar(cat):
        if cat == spec.fidelity_category:
            return all(g.score >= spec.case_bar for g in cats[cat])
        if cat == spec.procedural_category:
            return out["categories"][cat]["score"] >= spec.procedural_bar
        if cat == spec.performance_category:
            return all(g.detail.get("ratio") is not None and g.detail["ratio"] <= spec.perf_gate
                       for g in cats[cat])
        return out["categories"][cat]["score"] >= spec.case_bar

    out["full_success"] = bool(present) and all(bar(cat) for cat in present)
    return out
