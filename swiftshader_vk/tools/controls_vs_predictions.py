"""Observed control scores against the committed predictions (DESIGN.md "Controls").

    python swiftshader_vk/tools/controls_vs_predictions.py [--predictions runs/predictions.json]

Reads runs/control-<name>-<split>/report.json and the predictions written by
tools/predict_controls.py at the predictions commit, and applies the committed
bands: category ±0.05 and overall ±0.03; exact 1.000 replay/procedural (perf >=
0.98) for reference, one_thread, round_trunc and hardcode_public on public;
±0.10 for lavapipe and for the procedural score of stub, init_only,
success_everywhere, crash, malformed and hardcode_public on hidden; lavapipe
performance is not predicted. Prints one row per control and split, and the
cases most responsible for each miss.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXACT = {"reference", "one_thread", "round_trunc"}
WIDE_PROC = {"stub", "init_only", "success_everywhere", "crash", "malformed"}


def band(ctl, split, cat):
    if ctl in EXACT or (ctl == "hardcode_public" and split == "public"):
        return ("min", 0.98) if cat == "performance" else ("exact", 0.0005)
    if ctl == "lavapipe":
        return ("none", None) if cat in ("performance", "overall") else ("abs", 0.10)
    if cat == "procedural" and (ctl in WIDE_PROC or (ctl == "hardcode_public" and split == "hidden")):
        return ("abs", 0.10)
    return ("abs", 0.03 if cat == "overall" else 0.05)


def hit(kind, tol, pred, obs):
    if kind == "none" or pred != pred:
        return None
    if kind == "min":
        return obs >= tol
    if kind == "exact":
        return abs(obs - 1.0) <= tol
    return abs(obs - pred) <= tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", default=str(ROOT / "runs" / "predictions.json"))
    ap.add_argument("--json")
    a = ap.parse_args()
    pred = json.loads(Path(a.predictions).read_text())
    rows, out = [], {}
    print("| Control | Split | Replay obs (pred) | Procedural obs (pred) | Perf obs (pred) | Overall obs (pred) | Full success obs (pred) | Verdict |")
    print("|---|---|---|---|---|---|---|---|")
    for key in pred:
        ctl, split = key.split("/")
        rp = ROOT / "runs" / f"control-{ctl}-{split}" / "report.json"
        if not rp.exists():
            print(f"| `{ctl}` | {split} | (not run) |||||")
            continue
        R = json.loads(rp.read_text())
        agg = R["aggregate"]
        obs = {c: agg["categories"][c]["score"] for c in ("replay", "procedural", "performance")}
        obs["overall"] = agg["overall"]
        p = pred[key]
        cells, verdicts = [], []
        for cat in ("replay", "procedural", "performance", "overall"):
            kind, tol = band(ctl, split, cat)
            h = hit(kind, tol, p[cat], obs[cat])
            pv = "—" if p[cat] != p[cat] else f"{p[cat]:.3f}"
            mark = "" if h is None else (" ✓" if h else " **✗**")
            cells.append(f"{obs[cat]:.3f} ({pv}){mark}")
            if h is False:
                verdicts.append(cat)
        fs_pred = p.get("full_success")
        fs_obs = bool(agg["full_success"])
        fs_ok = None if ctl == "lavapipe" else (fs_obs == fs_pred)
        if fs_ok is False:
            verdicts.append("full_success")
        fs = f"{'yes' if fs_obs else 'no'} ({'—' if ctl == 'lavapipe' else ('yes' if fs_pred else 'no')})" + (
            "" if fs_ok is None else (" ✓" if fs_ok else " **✗**"))
        verdict = "hit" if not verdicts else "miss: " + ", ".join(verdicts)
        print(f"| `{ctl}` | {split} | " + " | ".join(cells) + f" | {fs} | {verdict} |")
        out[key] = {"observed": obs, "full_success": fs_obs, "predicted": p, "verdict": verdict}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
