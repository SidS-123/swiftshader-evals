"""The toy instance, end to end, with no Docker: corpus, reference cache,
the reference as a candidate, the controls, a regrade and the no-key smoke.

These are the methodology's own assertions, run on the toy: the reference
scores exactly 1.0, the stub sits in the null band, a tolerance perturbation
scores above 0.9, a uniform gain and a stale snapshot score low.
"""
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from evalbase.corpus import common
from evalbase.grader import cli, regrade, runner
from evalbase.interfaces import CorpusSpec


@pytest.fixture(scope="module")
def world(tmp_path_factory, toy):
    """A generated public+hidden corpus and both reference caches, built once."""
    root = tmp_path_factory.mktemp("toy-world")
    spec = CorpusSpec(root=toy.root, gen_dir=toy.corpus.gen_dir, public_dir=root / "public",
                      hidden_dir=root / "hidden", assets_dir=root / "assets",
                      hidden_env=toy.corpus.hidden_env, hidden_default=toy.corpus.hidden_default,
                      hidden_package=toy.corpus.hidden_package, forbidden_hidden_roots=())
    common.generate_all(spec, ["public", "hidden"], verbose=False)
    caches = {}
    for split in ("public", "hidden"):
        corpus = root / split
        cache = root / f"refcache-{split}"
        for case in sorted(corpus.glob("*.json")):
            runner.build_refcache(toy, str(case), str(cache), str(root / "assets"))
        caches[split] = (corpus, cache)
    return SimpleNamespace(root=root, caches=caches)


def _grade(toy, world, split, name, out):
    corpus, cache = world.caches[split]
    if name == "reference":
        lib = None
    else:
        lib = str(toy.controls.build(name, out / "lib"))
    a = SimpleNamespace(instance=toy, corpus=str(corpus), cache=str(cache), assets=str(world.root / "assets"),
                        cpus=None, only=None, out=str(out), extra_mounts=(), extra_env=None, owner=None)
    return cli._grade(a, lib, f"control-{name}-{split}")


def test_the_corpus_has_two_different_draws(world):
    public = sorted(p.stem for p in world.caches["public"][0].glob("*.json"))
    hidden = sorted(p.stem for p in world.caches["hidden"][0].glob("*.json"))
    assert len(public) == 7 and len(hidden) == 11
    assert not set(public) & set(hidden)
    families = {json.loads(p.read_text())["family"] for p in world.caches["public"][0].glob("*.json")}
    assert families == {json.loads(p.read_text())["family"] for p in world.caches["hidden"][0].glob("*.json")}


def test_the_reference_cache_is_stamped_and_calibrated(world, toy):
    for split, (corpus, cache) in world.caches.items():
        for case in corpus.glob("*.json"):
            rc = json.loads((cache / case.stem / "refcache.json").read_text())
            assert rc["replay_sha256"] == runner.replay_identity(str(case))["replay_sha256"]
            assert rc["metric_version"] == toy.metric.version
            assert toy.metric.t_lo <= rc["threshold"] <= toy.metric.t_hi
            assert set(rc["perturbation_defects"]) == set(toy.metric.perturbations)
            for mode in ("n1", "n2", *toy.metric.perturbations):
                assert (cache / case.stem / mode / "ledger.json").is_file()


@pytest.mark.parametrize("split", ["public", "hidden"])
def test_the_reference_as_a_candidate_scores_one(world, toy, tmp_path, split):
    report = _grade(toy, world, split, "reference", tmp_path / "ref")
    agg = report["aggregate"]
    # The residual is the performance category: the reference timing itself is
    # a hair off 1.0, and the Hill curve at ratio 1 is 1/(1 + (1/16)**4).
    assert agg["overall"] > 0.9999
    assert agg["full_success"] is True
    assert agg["categories"]["replay"]["score"] == 1.0
    assert agg["categories"]["procedural"]["score"] == 1.0
    assert agg["categories"]["performance"]["score"] > 0.999
    for case in report["cases"]:
        assert all(d == 0.0 for d in case["detail"]["snapshot_defects"])


@pytest.mark.parametrize("split", ["public", "hidden"])
def test_the_controls_land_where_predicted(world, toy, tmp_path, split):
    stub = _grade(toy, world, split, "stub", tmp_path / "stub")["aggregate"]
    half = _grade(toy, world, split, "half_samples", tmp_path / "half")["aggregate"]
    bright = _grade(toy, world, split, "brightness_x2", tmp_path / "bright")["aggregate"]
    stale = _grade(toy, world, split, "stale_output", tmp_path / "stale")["aggregate"]
    assert stub["overall"] < 0.05 and stub["categories"]["replay"]["score"] < 0.001
    assert stub["categories"]["performance"]["score"] == 0.0
    assert half["categories"]["replay"]["score"] > 0.9 and half["categories"]["procedural"]["score"] == 1.0
    assert bright["categories"]["replay"]["score"] < 0.2
    assert stale["categories"]["replay"]["score"] < 0.4
    assert not any(a["full_success"] for a in (stub, bright, stale))


def test_a_regrade_reproduces_the_grade_without_the_driver(world, toy, tmp_path, monkeypatch):
    corpus, cache = world.caches["public"]
    report = _grade(toy, world, "public", "half_samples", tmp_path / "half")
    monkeypatch.setattr(runner, "drive", lambda *a, **k: pytest.fail("regrade must not run the driver"))
    again = regrade.regrade_run(toy, str(tmp_path / "half"), str(corpus), str(cache),
                                str(tmp_path / "regraded"), verbose=False)
    assert again["aggregate"]["overall"] == pytest.approx(report["aggregate"]["overall"], abs=1e-12)
    assert again["regrade_reused_cases"] == {}
    assert again["regraded_from"]["report_sha256"]


def test_the_procedural_checks_are_derived_from_the_reference(world, toy, tmp_path):
    report = _grade(toy, world, "public", "reference", tmp_path / "ref")
    proc = [c for c in report["cases"] if c["category"] == "procedural"]
    assert proc
    names = {c["check"] for case in proc for c in case["detail"]["checks"]}
    assert "output_matches_reference" in names and "error_codes" in names
    assert any(n.startswith("query:bounds") for n in names)
    stub = _grade(toy, world, "public", "stub", tmp_path / "stub")
    failed = {c["check"] for case in stub["cases"] if case["category"] == "procedural"
              for c in case["detail"]["checks"] if not c["ok"]}
    assert "output_matches_reference" in failed and "error_codes" in failed


def test_a_crashing_candidate_is_recorded_not_raised(world, toy, tmp_path):
    corpus, cache = world.caches["public"]
    lib = tmp_path / "crash"
    lib.mkdir()
    (lib / "candidate.py").write_text("class Painter:\n    def apply(self, op):\n        raise RuntimeError('boom')\n")
    case = sorted(corpus.glob("rects_static*.json"))[0]   # order-independent (see docs/internal/EVALBASE_CHANGES.md)
    grade = runner.grade_replay(toy, str(case), str(cache), str(world.root / "assets"), str(lib), str(tmp_path / "out"))
    assert grade.score < 1e-5 and grade.detail["exit"] == "crash"      # the Hill tail at D = 1
    assert grade.detail["returncode"] == 1 and grade.detail["signal"] is None


def test_the_no_key_smoke_runs_without_docker(world, toy, tmp_path, monkeypatch):
    from evalbase.harness.smoke import smoke
    corpus, cache = world.caches["public"]
    monkeypatch.setenv("EVALBASE_CORPUS_PUBLIC", str(corpus))
    monkeypatch.setenv("EVALBASE_REFCACHE", str(cache))
    monkeypatch.setenv("EVALBASE_ASSETS", str(world.root / "assets"))
    monkeypatch.setenv("EVALBASE_CORPUS_HIDDEN", str(world.caches["hidden"][0]))
    monkeypatch.setenv("EVALBASE_REFCACHE_HIDDEN", str(world.caches["hidden"][1]))
    manifest = smoke(toy, "openrouter", tmp_path / "smoke", budget_seconds=20, sandbox="none")
    assert manifest["simulated_model"] and manifest["stop_reason"] == "budget_wall"
    assert manifest["grade"]["overall"] <= toy.task.smoke.max_overall
    assert manifest["graded_corpus"] == "hidden"
    assert manifest["measurement_valid"] is True
