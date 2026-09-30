"""Summarise determinism.json's perturbation comparisons per case (Stage 4 measurement).

    python3 swiftshader_vk/tools/perturb_summary.py [runs/pilot/determinism/determinism.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def worst(block: dict) -> str:
    if block["identical"]:
        return "same"
    parts = []
    for d in block["diffs"]:
        if d["what"] == "snapshot":
            u = d.get("units")
            if isinstance(u, dict):
                bits = []
                for ch, v in u.items():
                    if isinstance(v, dict):
                        m = v.get("max_lsb", v.get("max_ulp", v.get("max_abs")))
                        unit = "LSB" if "max_lsb" in v else "ULP" if "max_ulp" in v else "abs"
                        bits.append(f"{ch}:{m}{unit}/{v.get('differing')}px")
                parts.append(f'{d["item"]}[{" ".join(bits)}]')
            else:
                parts.append(f'{d["item"]}[{u}]')
        else:
            parts.append(d["what"] + (" " + d["name"] if d.get("name") else ""))
    return "; ".join(sorted(set(parts)))[:160]


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "runs" / "pilot" / "determinism" / "determinism.json"
    R = json.loads(path.read_text())
    ps = sorted({p for r in R for p in r.get("perturbations", {})})
    print("| Case | Family | " + " | ".join(ps) + " |")
    print("|---|---|" + "---|" * len(ps))
    for r in sorted(R, key=lambda r: (r["family"] or "", r["case"])):
        print(f'| {r["case"]} | {r["family"]} | ' + " | ".join(worst(r["perturbations"][p]) for p in ps) + " |")


if __name__ == "__main__":
    main()
