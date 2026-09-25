"""The hidden split's generators live outside the public tree.

On the toy's corpus spec: a public checkout with no private tree generates the whole
public split; "hidden" then stops with one message naming the env var; with
a private tree present the delegation loads it; the loader refuses a tree
inside a forbidden root; no public generator writes the hidden split.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evalbase.corpus import common
from evalbase.interfaces import CorpusSpec

ROOT = Path(__file__).resolve().parents[1]
RUNNER = """
import sys
from pathlib import Path
from evalbase.interfaces import CorpusSpec
from evalbase.corpus import common
root = Path(sys.argv[1])
spec = CorpusSpec(root=root, gen_dir=root / "gen", public_dir=root / "public", hidden_dir=root / "hidden",
                  assets_dir=root / "assets", hidden_env="TEST_HIDDEN_GEN",
                  hidden_default=root.parent / "no-such-sibling", hidden_package="test_hidden_gen",
                  forbidden_hidden_roots=(root,))
if sys.argv[2] == "module":
    import importlib
    sys.path.insert(0, str(spec.gen_dir))
    mod = importlib.import_module(sys.argv[3])
    if sys.argv[4] == "hidden":
        common.require_hidden_gen(spec)
    mod.generate(sys.argv[4], common.CorpusWriter(spec, verbose=False))
else:
    common.generate_all(spec, [sys.argv[2]], verbose=False)
"""


@pytest.fixture
def tree(tmp_path, toy):
    repo = tmp_path / "repo"
    shutil.copytree(toy.corpus.gen_dir, repo / "gen", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "run.py").write_text(RUNNER)
    return repo


def run(tree, args, private):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TEST_HIDDEN_GEN=str(private),
               PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, str(tree / "run.py"), str(tree), *args],
                          cwd=str(tree), capture_output=True, text=True, env=env)


def fake_private(tmp_path):
    gen = tmp_path / "private" / "gen"
    gen.mkdir(parents=True)
    (gen / "shapes.py").write_text(
        "from shapes import static_case\n\n"
        "def generate(split, corpus):\n"
        "    static_case(corpus, 'rects_static_hid_probe', 'hidden', seed=5, size=8, n=1, samples=1, background=0.0)\n")
    return gen.parent


def test_public_generation_works_with_no_private_tree(tree, tmp_path, toy):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    proc = run(tree, ["public"], empty)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    made = sorted(p.name for p in (tree / "public").glob("*.json"))
    assert made == sorted(p.name for p in toy.corpus.public_dir.glob("*.json"))
    assert not (tree / "hidden").exists()
    assert "TEST_HIDDEN_GEN" not in proc.stdout


@pytest.mark.parametrize("args", [["hidden"], ["both"], ["module", "shapes", "hidden"]])
def test_hidden_fails_with_one_clear_message_when_the_tree_is_absent(tree, tmp_path, args):
    missing = tmp_path / "nothing-here"
    missing.mkdir()
    proc = run(tree, args, missing)
    assert proc.returncode != 0
    message = proc.stderr.strip()
    assert "TEST_HIDDEN_GEN" in message and str(missing) in message
    assert "Traceback" not in message
    assert len(message.splitlines()) == 1, message
    assert not (tree / "hidden").exists()
    assert not (tree / "public").exists(), "must not half-generate"


def test_hidden_generation_runs_the_private_tree(tree, tmp_path):
    private = fake_private(tmp_path)
    proc = run(tree, ["module", "shapes", "hidden"], private)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert (tree / "hidden" / "rects_static_hid_probe.json").exists()
    assert not (tree / "public").exists()


def test_the_private_module_does_not_shadow_the_public_one(tree, tmp_path):
    """The private modules are loaded by path, so `from shapes import ...` inside
    one still reaches the public module of the same name."""
    private = fake_private(tmp_path)
    proc = run(tree, ["both"], private)
    assert proc.returncode != 0, "the fake private tree is incomplete: queries/perf are missing"
    assert "queries.py is missing" in proc.stderr or "perf.py is missing" in proc.stderr
    proc = run(tree, ["module", "shapes", "hidden"], private)
    assert proc.returncode == 0
    assert (tree / "hidden" / "rects_static_hid_probe.json").exists()


def test_the_loader_refuses_a_private_tree_inside_a_forbidden_root(tmp_path, monkeypatch, toy):
    spec = CorpusSpec(root=tmp_path, gen_dir=tmp_path / "gen", public_dir=tmp_path / "public",
                      hidden_dir=tmp_path / "hidden", assets_dir=tmp_path / "assets", hidden_env="TEST_HIDDEN_GEN")
    for inside in ("", "public", "gen"):
        monkeypatch.setenv("TEST_HIDDEN_GEN", str(tmp_path / inside) if inside else str(tmp_path))
        with pytest.raises(common.HiddenGeneratorsUnavailable) as excinfo:
            common.require_hidden_gen(spec)
        assert "inside" in str(excinfo.value) and "TEST_HIDDEN_GEN" in str(excinfo.value)


def test_the_toy_allows_its_shipped_private_tree_but_not_one_inside_its_corpus(monkeypatch, toy):
    monkeypatch.delenv(toy.corpus.hidden_env, raising=False)
    assert common.require_hidden_gen(toy.corpus) == str(toy.corpus.hidden_default / "gen")
    monkeypatch.setenv(toy.corpus.hidden_env, str(toy.corpus.public_dir))
    with pytest.raises(common.HiddenGeneratorsUnavailable):
        common.require_hidden_gen(toy.corpus)


def test_the_default_is_the_sibling_directory(tmp_path, monkeypatch):
    spec = CorpusSpec(root=tmp_path / "public-repo", gen_dir=tmp_path, public_dir=tmp_path, hidden_dir=tmp_path,
                      assets_dir=tmp_path, hidden_env="TEST_HIDDEN_GEN")
    monkeypatch.delenv("TEST_HIDDEN_GEN", raising=False)
    assert common.hidden_gen_root(spec) == str(tmp_path / "public-repo-hidden")


def test_no_public_generator_writes_the_hidden_split(toy):
    offenders = []
    for path in sorted(toy.corpus.gen_dir.glob("*.py")):
        text = path.read_text()
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"""split\s*=\s*["']hidden["']""", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
        if re.search(r"^def hid_|_hid_", text, re.M):
            offenders.append(f"{path.name}: a hidden case name")
    assert not offenders, "hidden generator code in the public tree:\n" + "\n".join(offenders)


def test_every_family_module_delegates_the_hidden_split(toy):
    missing = []
    for path in sorted(toy.corpus.gen_dir.glob("*.py")):
        text = path.read_text()
        if re.search(r"^def generate\(", text, re.M) and "corpus.hidden_generate(__file__, split)" not in text:
            missing.append(path.name)
    assert not missing, missing
