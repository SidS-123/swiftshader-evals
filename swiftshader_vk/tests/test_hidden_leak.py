"""The public tree must not leak the hidden split (PLAN_v1.md §11.2 rules 1, 8, 9).

Needs the private generator tree (`SSVK_HIDDEN_GEN`, default the sibling
`../swiftshader-evals-hidden`); skipped without it.

- no hidden seed appears as a whole number anywhere in the public tree;
- no hidden case name (`<family>_hid_<k>`) appears in the public tree outside
  the generators' naming helper;
- no public parameter dict equals a hidden one (seed and tag aside), unless
  the seed is all that drives the variant;
- every hidden seed differs from every public seed.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # swiftshader_vk/
REPO = ROOT.parent
HIDDEN = Path(os.environ.get("SSVK_HIDDEN_GEN") or REPO.parent / "swiftshader-evals-hidden") / "gen"
SKIP_DIRS = {"runs", "hidden", "assets", "__pycache__", "build-logs", ".git", "site"}
SELECTORS = {"variant", "family"}
TEXT_SUFFIXES ={".py", ".md", ".json", ".txt", ".sh", ".cpp", ".hpp", ".toml", ".yaml", ".yml", ".lock", ""}

pytestmark = pytest.mark.skipif(not HIDDEN.is_dir(), reason="private hidden generator tree not available")


def _tables(path: Path, name: str) -> list[dict]:
    """The literal parameter table `name` (PUBLIC / HIDDEN) of a generator module."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            # our own tables, with constant expressions such as `1 << 20`: evaluated without builtins
            return eval(compile(ast.Expression(node.value), str(path), "eval"), {"__builtins__": {}})
    return []


def _hidden() -> dict[str, list[dict]]:
    return {p.stem: _tables(p, "HIDDEN") for p in sorted(HIDDEN.glob("*.py"))}


def _public() -> dict[str, list[dict]]:
    return {p.stem: _tables(p, "PUBLIC") for p in sorted((ROOT / "corpus" / "gen").glob("*.py"))}


def _public_files():
    for base in (ROOT, REPO / "docs"):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix in TEXT_SUFFIXES and p.stat().st_size < 8_000_000:
                    yield p


def test_every_family_has_a_hidden_table():
    hidden, public = _hidden(), _public()
    missing = [m for m in public if public[m] and not hidden.get(m)]
    assert not missing, f"public families without hidden cases: {missing}"


def test_hidden_seeds_are_new():
    pub_seeds = {p["seed"] for t in _public().values() for p in t if "seed" in p}
    hid_seeds = [p["seed"] for t in _hidden().values() for p in t if "seed" in p]
    assert len(hid_seeds) == len(set(hid_seeds)), "hidden seeds repeat"
    assert not pub_seeds & set(hid_seeds), f"seeds shared by both splits: {sorted(pub_seeds & set(hid_seeds))}"


def test_hidden_parameters_differ_from_public():
    pub, hid = _public(), _hidden()
    strip = lambda p: {k: v for k, v in p.items() if k not in ("seed", "tag")}  # noqa: E731
    same = []
    for fam, table in hid.items():
        pubs = [strip(p) for p in pub.get(fam, [])]
        for k, p in enumerate(table):
            q = strip(p)
            # a variant with no parameters but its seed and selectors (e.g. {"variant": "cube"},
            # {"variant": "compressed", "family": "BC"}) is drawn by the seed alone
            if q in pubs and not set(q) <= SELECTORS:
                same.append(f"{fam}[{k}]")
    assert not same, f"hidden cases with public parameters: {same}"


def test_public_tree_does_not_mention_hidden_seeds_or_names():
    seeds = {str(p["seed"]) for t in _hidden().values() for p in t if "seed" in p}
    seed_re = re.compile(r"(?<![\d.])(" + "|".join(sorted(seeds)) + r")(?![\d.])")
    name_re = re.compile(r"\b[a-z_]+_hid_\d\d\b")
    hits = []
    for f in _public_files():
        text = f.read_text(errors="replace")
        for m in seed_re.finditer(text):
            hits.append(f"{f.relative_to(REPO)}: seed {m.group(1)}")
        for m in name_re.finditer(text):
            hits.append(f"{f.relative_to(REPO)}: name {m.group(0)}")
    assert not hits, "\n".join(hits[:20])
