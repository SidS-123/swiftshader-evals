"""Summarise a reference cache: the tolerance distribution and reference timings (Stage 7, PLAN_v1.md §11.3).

    python swiftshader_vk/tools/refcache_summary.py [CACHE_DIR] [--corpus DIR]

Prints, per family: case count, threshold T min / median / max, how many cases
sit at t_lo and t_hi, the mean recorded defect of each perturbation, and the
slowest reference wall time; then the timed runs of performance cases, and every
non-performance case whose reference wall time is 1 s or more (the timing gate).
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
T_LO, T_HI = 0.03, 0.30


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache", nargs="?", default=str(HERE.parent / "runs" / "refcache"))
    ap.add_argument("--corpus", default=str(HERE.parent / "corpus" / "public"))
    a = ap.parse_args()
    corpus = Path(a.corpus)
    rows = []
    for entry in sorted(Path(a.cache).glob("*/refcache.json")):
        name = entry.parent.name
        case = corpus / f"{name}.json"
        if not case.exists():
            continue                      # an entry for a case no longer in this corpus
        c = json.loads(case.read_text())
        e = json.loads(entry.read_text())
        rows.append((c["family"], c["category"], name, e))
    fams = defaultdict(list)
    for fam, cat, name, e in rows:
        fams[fam].append((cat, name, e))
    perts = sorted({p for _, _, _, e in rows for p in e.get("perturbation_defects", {})})
    print(f"{len(rows)} cases in {a.cache}\n")
    print("| Family | Cases | T min | T median | T max | at t_lo | at t_hi | "
          + " | ".join(f"{p} mean D" for p in perts) + " | slowest ref (s) |")
    print("|---|---|---|---|---|---|---|" + "---|" * len(perts) + "---|")
    for fam in sorted(fams):
        es = [e for _, _, e in fams[fam]]
        ts = [float(e["threshold"]) for e in es]
        pd = [statistics.mean(float(e["perturbation_defects"].get(p) or 0.0) for e in es) for p in perts]
        print(f"| `{fam}` | {len(es)} | {min(ts):.3f} | {statistics.median(ts):.3f} | {max(ts):.3f} | "
              f"{sum(t <= T_LO + 1e-9 for t in ts)} | {sum(t >= T_HI - 1e-9 for t in ts)} | "
              + " | ".join(f"{x:.3f}" for x in pd)
              + f" | {max(float(e.get('reference_wall_seconds') or 0) for e in es):.2f} |")
    ts = [float(e["threshold"]) for _, _, _, e in rows]
    print(f"\nall: T min {min(ts):.3f}, median {statistics.median(ts):.3f}, max {max(ts):.3f}; "
          f"{sum(t <= T_LO + 1e-9 for t in ts)} at t_lo, {sum(t >= T_HI - 1e-9 for t in ts)} at t_hi")
    print("\nperformance (timed run seconds, reference):")
    for fam, cat, name, e in rows:
        if cat == "performance":
            print(f"  {name}: run {e.get('reference_run_seconds')}, wall {e.get('reference_wall_seconds'):.2f}")
    slow = [(name, float(e.get("reference_wall_seconds") or 0)) for _, cat, name, e in rows
            if cat != "performance" and float(e.get("reference_wall_seconds") or 0) >= 1.0]
    walls = [float(e.get("reference_wall_seconds") or 0) for _, cat, _, e in rows if cat != "performance"]
    print(f"\nnon-performance reference wall: max {max(walls):.2f} s, median {statistics.median(walls):.2f} s; "
          f"{len(slow)} at 1 s or more" + "".join(f"\n  {n}: {w:.2f} s" for n, w in sorted(slow, key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
