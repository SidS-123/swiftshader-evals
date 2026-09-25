"""Rescore an already graded run from its cached outputs, under the current metric.

`grade` runs the candidate and scores what comes out. Everything it scored is
still on disk afterwards -- `<run>/cases/<case>/` holds the outputs and the
ledger -- so when the metric changes, the run does not have to be repeated to
be scored again. No driver, no container, no candidate artifact is needed, and
the source run directory is only ever read.

What is recomputed and what is reused:

* fidelity cases -- recomputed from the cached outputs against the refcache's
  n1 output, at the case's threshold under the metric of record (an entry
  calibrated by an older metric is recalibrated from its own outputs on the
  fly; nothing is written into the refcache).
* procedural cases -- recomputed: the instance's checks are driven by the two
  ledgers and the two output directories, all of which the run keeps.
* performance cases -- the wall-time ratio is reused (a timing is a live
  measurement), but the `replay >= 0.5` gate that turns a ratio into `inf` is
  re-evaluated under the new metric.
* a case whose outputs are not in the run directory keeps its stored score,
  marked `regrade_reused`.

The aggregate comes from `runner.aggregate`, the same function `grade` uses.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os

import numpy as np

from ..interfaces import Instance, read_case_meta
from . import jsonio, metrics, runner


def report_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def default_out(instance: Instance, run_dir: str) -> str:
    return os.path.join(run_dir, f"regrade-v{instance.metric.version}")


def _load_ledger(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def regrade_case(instance: Instance, case: dict, run_dir: str, corpus: str, cache: str) -> runner.ReplayGrade:
    """One case of a graded run, rescored under the metric of record."""
    spec = instance.metric
    name = case["name"]
    stored = dict(case.get("detail") or {})
    reuse = lambda why: runner.ReplayGrade(
        name, case["category"], case.get("family", "unknown"), case["score"],
        dict(stored, regrade_reused=why))

    replay_path = os.path.join(corpus, name + ".json")
    cdir = os.path.join(cache, name)
    entry = os.path.join(cdir, "refcache.json")
    if not os.path.exists(replay_path) or not os.path.exists(entry):
        return reuse(f"no case file in {corpus} or no refcache entry in {cache}")
    cand_dir = os.path.join(run_dir, "cases", name)
    cand_ledger = _load_ledger(os.path.join(cand_dir, "ledger.json"))
    if cand_ledger is None:
        return reuse("the run directory has no cached ledger for this case")

    meta = read_case_meta(replay_path)
    with open(entry) as f:
        rc = json.load(f)
    cached_sha = rc.get("replay_sha256")
    replay_sha = runner.replay_identity(replay_path)["replay_sha256"]
    if cached_sha and cached_sha != replay_sha:
        raise RuntimeError(
            f"refcache entry for {name} was built from a different case file: "
            f"refcache replay_sha256={cached_sha} but {os.path.abspath(replay_path)} is {replay_sha}")
    ref_ledger = _load_ledger(os.path.join(cdir, "n1", "ledger.json"))
    if ref_ledger is None:
        return reuse("the refcache entry has no n1 ledger")
    ref_dir = os.path.join(cdir, "n1")
    ref_snaps = [runner.load_snapshot(instance, ref_dir, e) for e in runner.snapshots(ref_ledger)]
    threshold, recal = runner.entry_threshold(instance, cdir, rc)

    cand_snaps = [runner.load_snapshot(instance, cand_dir, e) for e in runner.snapshots(cand_ledger)]
    rep = metrics.score_replay(instance.scorer.distance, ref_snaps, cand_snaps, threshold, spec.hill_n)
    detail = dict(stored)
    detail.update({"threshold": threshold, "snapshot_defects": rep["snapshot_defects"],
                   "snapshot_scores": rep["snapshot_scores"],
                   "first_diverge_snapshot": rep["first_diverge_snapshot"]})
    if recal:
        detail["threshold_recalibrated"] = recal
    else:
        detail.pop("threshold_recalibrated", None)
    score = rep["score"]

    if meta.category == spec.performance_category:
        exited_ok = (stored.get("exit") or cand_ledger.get("exit")) == "ok"
        stored_ratio = stored.get("ratio")
        if not (exited_ok and rep["score"] >= 0.5):
            ratio, how = float("inf"), "gate: replay score under 0.5 or the run did not exit ok"
        elif stored_ratio is not None and np.isfinite(stored_ratio):
            ratio, how = float(stored_ratio), "stored wall-time ratio reused"
        else:
            ratio = runner.performance_ratio(instance, meta, rc, cand_ledger, True, rep["score"])
            how = "recomputed from the cached ledger (the gate now passes; no stored ratio)"
        detail["ratio"] = ratio
        detail["replay_score"] = rep["score"]
        detail["wall_time"] = how
        score = metrics.perf_score(ratio, spec.perf_half, spec.hill_n)
    elif meta.category == spec.procedural_category:
        checks = instance.scorer.procedural_checks(meta, ref_ledger, cand_ledger, ref_dir, cand_dir, threshold)
        detail["checks"] = checks
        score = float(np.mean([c["ok"] for c in checks])) if checks else 0.0
        detail["replay_score"] = rep["score"]
    detail.pop("regrade_reused", None)
    return runner.ReplayGrade(name, meta.category, meta.family, score, detail)


def regrade_run(instance: Instance, run_dir: str, corpus: str, cache: str, out_dir: str | None = None,
                only: list[str] | None = None, verbose: bool = True) -> dict:
    """Rescore `<run_dir>/report.json` from its cached outputs. Returns the new report."""
    version = instance.metric.version
    run_dir = os.path.abspath(run_dir)
    src_path = os.path.join(run_dir, "report.json")
    with open(src_path) as f:
        source = json.load(f)
    out_dir = os.path.abspath(out_dir or default_out(instance, run_dir))
    os.makedirs(out_dir, exist_ok=True)

    grades, reused = [], []
    for case in source.get("cases", []):
        if only and case["name"] not in only:
            continue
        g = regrade_case(instance, case, run_dir, corpus, cache)
        if g.detail.get("regrade_reused"):
            reused.append((g.name, g.detail["regrade_reused"]))
        grades.append(g)
        if verbose:
            moved = g.score - case["score"]
            print(f"[{g.category}] {g.name}: {g.score:.4f} ({moved:+.4f})"
                  + (f"  reused: {g.detail['regrade_reused']}" if g.detail.get("regrade_reused") else ""))
    agg = runner.aggregate(instance.metric, grades)
    report = dict(source)
    report.update({
        "corpus": corpus, "cache": cache,
        "metric_version": version,
        "aggregate": agg,
        "cases": [{"name": g.name, "category": g.category, "family": g.family,
                   "score": g.score, "detail": g.detail} for g in grades],
        "regraded_from": {"run_dir": run_dir, "report": src_path,
                          "report_sha256": report_sha256(src_path),
                          "metric_version": source.get("metric_version"),
                          "overall": source.get("aggregate", {}).get("overall")},
        "regraded_at": datetime.datetime.now(datetime.timezone.utc)
                               .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "regrade_reused_cases": dict(reused),
    })
    path = os.path.join(out_dir, "report.json")
    jsonio.write_json(path, report)
    if verbose:
        src = source.get("aggregate", {})
        print(f"\n== {report.get('label')} regraded under metric v{version} ==")
        print(f"  {'category':12s} {'v' + (source.get('metric_version') or 'unstamped'):>22s}"
              f" {'v' + version:>10s}")
        was_str = lambda x: "-" if x is None else f"{x:.4f}"
        for cat, c in agg["categories"].items():
            was = src.get("categories", {}).get(cat, {}).get("score")
            print(f"  {cat:12s} {was_str(was):>22} {c['score']:10.4f}"
                  f"  (weight {c['weight']}, n={c['n']})")
        print(f"  {'overall':12s} {was_str(src.get('overall')):>22} {agg['overall']:10.4f}"
              f"   full_success={agg['full_success']}")
        if reused:
            print(f"  reused {len(reused)} stored case result(s): "
                  + ", ".join(n for n, _ in reused[:8]) + (" ..." if len(reused) > 8 else ""))
        print("report:", path)
    return report


# ----------------------------------------------------------------- harness

def attempt_run_dirs(attempt_dir: str) -> list[tuple[str, str]]:
    """(label, dir) of every graded run inside a harness attempt directory."""
    attempt_dir = os.path.abspath(attempt_dir)
    out = []
    if os.path.exists(os.path.join(attempt_dir, "grade", "report.json")):
        out.append(("grade", os.path.join(attempt_dir, "grade")))
    for row in checkpoint_curve(attempt_dir):
        rel = row.get("report")
        if rel and os.path.exists(os.path.join(attempt_dir, rel)):
            out.append((f"checkpoint-{row['id']}", os.path.dirname(os.path.join(attempt_dir, rel))))
    dev_root = os.path.join(attempt_dir, "grade_dev")
    for entry in sorted(os.listdir(dev_root) if os.path.isdir(dev_root) else []):
        d = os.path.join(dev_root, entry)
        if os.path.exists(os.path.join(d, "report.json")):
            out.append((f"grade_dev-{entry}", d))
    return out


def checkpoint_curve(attempt_dir: str) -> list[dict]:
    path = os.path.join(attempt_dir, "checkpoint-curve.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def regrade_attempt(instance: Instance, attempt_dir: str, corpus: str, cache: str, out_root: str,
                    include_dev: bool = False, verbose: bool = True) -> dict:
    """Regrade a harness attempt's runs and rebuild its checkpoint curve.

    Writes `<out_root>/<label>/report.json` per run and, when the attempt has a
    checkpoint-curve.json, `<out_root>/checkpoint-curve.v<version>.json` with
    the same rows in the same order. The attempt directory is never written to.
    """
    version = instance.metric.version
    attempt_dir, out_root = os.path.abspath(attempt_dir), os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)
    reports = {}
    for label, run_dir in attempt_run_dirs(attempt_dir):
        if label.startswith("grade_dev-") and not include_dev:
            continue
        if verbose:
            print(f"\n=== {label} ({run_dir}) ===")
        reports[label] = regrade_run(instance, run_dir, corpus, cache,
                                     out_dir=os.path.join(out_root, label), verbose=verbose)
    curve = []
    for row in checkpoint_curve(attempt_dir):
        row = dict(row)
        rep = reports.get(f"checkpoint-{row['id']}")
        if rep is not None:
            agg = rep["aggregate"]
            row.update({"overall": agg["overall"], "full_success": agg["full_success"],
                        "report": os.path.relpath(os.path.join(out_root, f"checkpoint-{row['id']}",
                                                               "report.json"), out_root),
                        "metric_version": version})
        curve.append(row)
    if curve:
        path = os.path.join(out_root, f"checkpoint-curve.v{version}.json")
        with open(path, "w") as f:
            f.write(json.dumps(curve, indent=1) + "\n")
        if verbose:
            print("curve:", path)
    return {"reports": reports, "curve": curve, "out_root": out_root}
