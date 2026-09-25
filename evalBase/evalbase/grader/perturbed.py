"""The "reference with its own perturbations" control, computed offline.

Every run beside `n1` in a refcache entry is scored back against the
threshold the entry defines, with the same `distance` and `hill` the grader
uses. The runs in `MetricSpec.tolerance_perturbations` *defined* T, so the
threshold must accept them: the prediction is category >= 0.9 per tolerated
perturbation, per case >= 0.7 except cases whose T sits at the `t_hi` cap,
where the property cannot hold by construction. The runs that are not
tolerated are measured against the same T as penalised candidates; `n2` (the
reference at twice the samples, i.e. a correct candidate that differs only by
noise) is the "second seed" control.

No driver run, no container: everything is read from an existing refcache.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np

from ..interfaces import Instance, load_instance, read_case_meta
from . import metrics, runner


def modes(instance: Instance) -> list[tuple[str, str, str]]:
    """(cache directory, column key, role) for every run beside n1."""
    spec = instance.metric
    out = []
    for mode in spec.perturbations:
        role = "defines T" if mode in spec.tolerance_perturbations else "penalised"
        out.append((mode, f"s_{mode}", role))
    out.append(("n2", "s_n2", "accumulation"))
    return out


def score_mode(instance: Instance, ref_snaps: list, snapshots, threshold: float):
    """Mean Hill score of distance(ref, cand) over snapshots -- s_case for one control."""
    if not snapshots or len(snapshots) != len(ref_snaps) or any(f is None for f in snapshots):
        return None
    spec = instance.metric
    scores = [metrics.hill(instance.scorer.distance(r, c), threshold, spec.hill_n)
              for r, c in zip(ref_snaps, snapshots)]
    return float(np.mean(scores))


def family_aggregate(rows: list[dict]) -> float:
    """mean over families of mean over that family's cases -- runner.aggregate's fidelity grouping."""
    fams: dict[str, list[float]] = {}
    for r in rows:
        fams.setdefault(r["family"], []).append(r["score"])
    return float(np.mean([np.mean(v) for v in fams.values()])) if fams else 0.0


def perturbed_control(instance: Instance, cache: str, corpus: str, only=None,
                      all_categories: bool = False) -> dict:
    """Rows per case and an aggregate per mode; see the module docstring."""
    names = sorted(os.path.basename(os.path.dirname(p))
                   for p in glob.glob(os.path.join(cache, "*", "refcache.json")))
    if only:
        names = [n for n in names if n in set(only)]
    rows = []
    for name in names:
        corpus_path = os.path.join(corpus, name + ".json")
        if not os.path.exists(corpus_path):
            continue
        case = read_case_meta(corpus_path)
        if case.category != instance.metric.fidelity_category and not all_categories:
            continue
        cdir = os.path.join(cache, name)
        with open(os.path.join(cdir, "refcache.json")) as f:
            rc = json.load(f)
        # Not rc["threshold"] straight: an entry calibrated by an older metric
        # carries an older tolerance, and entry_threshold recomputes it from
        # that entry's own outputs, exactly as grading does.
        threshold, _ = runner.entry_threshold(instance, cdir, rc)
        ref_snaps = runner.cache_snapshots(instance, cdir, "n1")
        if not ref_snaps or any(f is None for f in ref_snaps):
            continue
        row = {"replay": name, "family": case.family, "T": threshold}
        for mode, key, _ in modes(instance):
            row[key] = score_mode(instance, ref_snaps, runner.cache_snapshots(instance, cdir, mode), threshold)
        rows.append(row)
    aggregate = {}
    for mode, key, role in modes(instance):
        present = [r for r in rows if r[key] is not None]
        vals = [r[key] for r in present]
        cat = family_aggregate([{"family": r["family"], "score": r[key]} for r in present])
        below = sorted(((r["replay"], r[key]) for r in present if r[key] < 0.9), key=lambda t: t[1])
        aggregate[key] = {"category_score": cat, "role": role,
                          "min": min(vals) if vals else None,
                          "median": float(np.median(vals)) if vals else None,
                          "n_below_0.9": len(below), "below_0.9": below}
    return {"rows": rows, "aggregate": aggregate, "modes": [(m, k, r) for m, k, r in modes(instance)]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instance", default=None)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--only", default=None, help="comma-separated case names to include")
    ap.add_argument("--all-categories", action="store_true", help="include procedural/performance too")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    instance = load_instance(a.instance)
    cache = a.cache or str(instance.refcache)
    corpus = a.corpus or str(instance.corpus_public)
    only = a.only.split(",") if a.only else None
    result = perturbed_control(instance, cache, corpus, only, a.all_categories)
    fmt = lambda x: f"{x:.4f}" if x is not None else "n/a"
    spec = instance.metric
    print(f"metric v{spec.version}: T is defined by " + "/".join(spec.tolerance_perturbations)
          + "; the other columns are measured against it, not tolerated by it")
    keys = [k for _, k, _ in result["modes"]]
    roles = {k: r for _, k, r in result["modes"]}
    print(f"{'case':32s} {'family':22s} {'T':>6s} " + " ".join(f"{k:>13s}" for k in keys))
    print(f"{'':32s} {'':22s} {'':>6s} " + " ".join(f"{roles[k]:>13s}" for k in keys))
    for r in result["rows"]:
        print(f"{r['replay']:32s} {r['family']:22s} {r['T']:6.4f} " + " ".join(f"{fmt(r[k]):>13s}" for k in keys))
    for key in keys:
        agg = result["aggregate"][key]
        print(f"\n{key} ({agg['role']}): category={agg['category_score']:.4f} min={fmt(agg['min'])} "
              f"median={fmt(agg['median'])} below_0.9={agg['n_below_0.9']} {agg['below_0.9']}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"rows": result["rows"], "aggregate": result["aggregate"]}, f, indent=1)


if __name__ == "__main__":
    main()
