"""Stage 6 measurement: the ssvk distance D between the base oracle run and each variant.

    python3 swiftshader_vk/tools/perturb_distance.py [RUNS_DIR] [--tolerate subzero vtxjitter texcoord_ulp]

RUNS_DIR is tools/determinism.py's work directory (runs/pilot/determinism/runs):
<case>/{base0, thr1_0, subzero, vtxjitter, texcoord_ulp, lavapipe}/. For every
case it prints the mean snapshot D of each variant against base0, the T the
metric would derive from the tolerated set, and the score each variant would
get under that T (lavapipe's is the fairness number).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "evalBase"))

import scorer as sc                     # noqa: E402
from evalbase.grader import metrics     # noqa: E402
from evalbase.interfaces import MetricSpec  # noqa: E402

VARIANTS = ["thr1_0", "subzero", "vtxjitter", "texcoord_ulp", "lavapipe"]


def snaps(run: Path):
    lp = run / "ledger.json"
    if not lp.exists():
        return None
    L = json.loads(lp.read_text())
    s = sc.SsvkScorer()
    return [s.load_output(str(run), e) for e in L.get("events", []) if e.get("op") == "snapshot"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="?", default=str(HERE.parent / "runs" / "pilot" / "determinism" / "runs"))
    ap.add_argument("--tolerate", nargs="*", default=["subzero", "vtxjitter", "texcoord_ulp"])
    a = ap.parse_args()
    spec = MetricSpec()
    rows = []
    for case in sorted(Path(a.runs).iterdir()):
        base = snaps(case / "base0")
        if not base:
            continue
        d = {}
        for v in VARIANTS:
            other = snaps(case / v) if (case / v).exists() else None
            d[v] = metrics.mean_snapshot_defect(sc.SsvkScorer().distance, base, other) if other else None
        sens = max([d[v] for v in a.tolerate if d.get(v) is not None] or [0.0])
        noise = d.get("thr1_0") or 0.0
        t = min(max(max(2 * noise, 2 * sens), spec.t_lo), spec.t_hi)
        rows.append((case.name, d, t))
    print("| Case | " + " | ".join(f"D {v}" for v in VARIANTS) + " | T | lavapipe score |")
    print("|---|" + "---|" * (len(VARIANTS) + 2))
    fair = []
    for name, d, t in rows:
        cells = ["-" if d[v] is None else f"{d[v]:.4f}" for v in VARIANTS]
        lp = d.get("lavapipe")
        ls = metrics.hill(lp, t, 4) if lp is not None else None
        if ls is not None:
            fair.append(ls)
        print(f"| {name} | " + " | ".join(cells) + f" | {t:.3f} | {'-' if ls is None else f'{ls:.3f}'} |")
    ts = np.array([t for _, _, t in rows])
    print(f"\nT: min {ts.min():.3f} median {np.median(ts):.3f} max {ts.max():.3f}; at t_lo {int((ts <= spec.t_lo + 1e-12).sum())}"
          f"/{len(ts)}, at t_hi {int((ts >= spec.t_hi - 1e-12).sum())}; tolerated: {a.tolerate}")
    if fair:
        print(f"lavapipe mean snapshot-score over {len(fair)} snapshot cases: {np.mean(fair):.3f}")


if __name__ == "__main__":
    main()
