"""Regenerate docs/CONTROLS.md's summary without naming hidden cases.

    python swiftshader_vk/tools/controls_report.py

Runs evalBase's `controls_summary --write`, then removes the per-case tables of
hidden runs ("**<control> (hidden)**" and the table under it) from the generated
section: hidden case names carry no parameters, but which hidden cases fail a
given alteration says what they contain, and no hidden case name may appear in
the public tree (tests/test_hidden_leak.py). Hidden aggregates stay.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def strip_hidden_tables(text: str) -> tuple[str, int]:
    out, skipping, removed = [], False, 0
    for line in text.split("\n"):
        if re.match(r"^\*\*\S+ \(hidden\)\*\*$", line):
            skipping, removed = True, removed + 1
            continue
        if skipping:
            if line.startswith("|") or not line.strip():
                continue
            skipping = False
        out.append(line)
    return "\n".join(out), removed


def main() -> int:
    r = subprocess.run([sys.executable, "-m", "evalbase.reports.controls_summary", "--instance", str(ROOT), "--write"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        return r.returncode
    p = ROOT / "docs" / "CONTROLS.md"
    text, n = strip_hidden_tables(p.read_text())
    text = text.replace("### 8 lowest-scoring replay cases per control",
                        "### 8 lowest-scoring replay cases per control (public; hidden case names are not published)")
    p.write_text(text)
    print(f"wrote {p} ({n} hidden per-case tables removed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
