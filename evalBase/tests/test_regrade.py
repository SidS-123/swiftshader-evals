"""`regrade`: rescoring a graded run from its cached outputs.

A fake driver
writes real (tiny) PGMs through the toy scorer's format; the grading path is
the real one.
"""
import json
import os
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from evalbase.grader import cli, regrade, runner
from evalbase.interfaces import DriveResult
from examples_helpers import noisier, picture, write_case, write_output

CASES = ("rep_case", "proc_case", "perf_case")


@pytest.fixture
def world(tmp_path, monkeypatch, toy):
    corpus, cache, run = tmp_path / "corpus", tmp_path / "cache", tmp_path / "run"
    corpus.mkdir()
    ref = [picture(s) for s in range(2)]
    outputs = {"": ref, "halfsamples": noisier(ref, 0.12, 1), "jitter": noisier(ref, 0.10, 2)}
    cand = noisier(ref, 0.14, 9)

    def fake_drive(instance, replay_path, outdir, assets_dir, lib_dir=None, sample_mult=1, perturb="", **kw):
        snapshots = cand if lib_dir else outputs[perturb]
        wall = 1.5 if lib_dir else 0.5
        ledger = write_output(outdir, snapshots, wall)
        return DriveResult(outdir, ledger, 0, wall * len(snapshots), False, "")

    monkeypatch.setattr(runner, "drive", fake_drive)
    for name in CASES:
        category = {"proc_case": "procedural", "perf_case": "performance"}.get(name, "replay")
        path = write_case(corpus, name, category)
        runner.build_refcache(toy, str(path), str(cache), str(corpus))
    a = SimpleNamespace(instance=toy, corpus=str(corpus), cache=str(cache), assets=str(corpus), cpus=None,
                        only=None, out=str(run), extra_mounts=(), extra_env=None, owner=None)
    report = cli._grade(a, str(tmp_path / "lib"), "the-run")
    return SimpleNamespace(corpus=str(corpus), cache=str(cache), run=str(run), report=report, ref=ref,
                           tmp=tmp_path, instance=toy)


def _regrade(world, out="regraded", **kw):
    return regrade.regrade_run(world.instance, world.run, world.corpus, world.cache,
                               str(world.tmp / out), verbose=False, **kw)


def test_regrade_reproduces_the_grade_it_came_from(world):
    again = _regrade(world)
    was = {c["name"]: c["score"] for c in world.report["cases"]}
    now = {c["name"]: c["score"] for c in again["cases"]}
    assert set(now) == set(CASES)
    for name in CASES:
        assert now[name] == pytest.approx(was[name], abs=1e-12)
    assert again["aggregate"]["overall"] == pytest.approx(world.report["aggregate"]["overall"], abs=1e-12)
    assert again["regrade_reused_cases"] == {}


def test_regrade_recomputes_every_d_iefect_rather_than_copying_it(world):
    again = _regrade(world)
    case = next(c for c in again["cases"] if c["name"] == "rep_case")
    cdir = os.path.join(world.cache, "rep_case", "n1")
    ref = runner.cache_snapshots(world.instance, os.path.join(world.cache, "rep_case"), "n1")
    run_case = os.path.join(world.run, "cases", "rep_case")
    with open(os.path.join(run_case, "ledger.json")) as f:
        cand_ledger = json.load(f)
    cand = [runner.load_snapshot(world.instance, run_case, e) for e in runner.snapshots(cand_ledger)]
    want = [world.instance.scorer.distance(a, b) for a, b in zip(ref, cand)]
    assert case["detail"]["snapshot_defects"] == pytest.approx(want)
    assert max(want) > 0, "the fixture's candidate must actually differ from the reference"


def test_a_defect_free_candidate_scores_one(world):
    for name in CASES:
        write_output(os.path.join(world.run, "cases", name), world.ref, wall=0.5)
    again = _regrade(world, out="perfect")
    by_name = {c["name"]: c for c in again["cases"]}
    assert by_name["rep_case"]["score"] == 1.0
    assert by_name["proc_case"]["score"] == 1.0
    assert all(d == 0.0 for c in again["cases"] for d in c["detail"]["snapshot_defects"])
    assert by_name["perf_case"]["detail"]["replay_score"] == 1.0


def test_the_report_records_where_it_was_regraded_from(world):
    again = _regrade(world)
    assert again["metric_version"] == world.instance.metric.version
    src = again["regraded_from"]
    assert src["run_dir"] == os.path.abspath(world.run)
    assert src["report_sha256"] == regrade.report_sha256(os.path.join(world.run, "report.json"))
    assert src["metric_version"] == world.instance.metric.version
    assert src["overall"] == pytest.approx(world.report["aggregate"]["overall"])
    assert again["regraded_at"].endswith("Z")
    assert set(again) >= set(world.report)
    for case in again["cases"]:
        assert set(case) == {"name", "category", "family", "score", "detail"}


def test_the_default_output_directory_is_named_for_the_metric(world):
    assert regrade.default_out(world.instance, "/x/run") == f"/x/run/regrade-v{world.instance.metric.version}"


def test_a_case_with_no_cached_snapshots_keeps_its_stored_score(world):
    shutil.rmtree(os.path.join(world.run, "cases", "rep_case"))
    again = _regrade(world, out="partial")
    case = next(c for c in again["cases"] if c["name"] == "rep_case")
    was = next(c for c in world.report["cases"] if c["name"] == "rep_case")
    assert case["score"] == was["score"]
    assert "cached ledger" in case["detail"]["regrade_reused"]
    assert list(again["regrade_reused_cases"]) == ["rep_case"]


def test_a_performance_case_reuses_its_wall_time_ratio(world):
    again = _regrade(world)
    case = next(c for c in again["cases"] if c["name"] == "perf_case")
    was = next(c for c in world.report["cases"] if c["name"] == "perf_case")
    assert case["detail"]["ratio"] == was["detail"]["ratio"]
    assert case["detail"]["wall_time"] == "stored wall-time ratio reused"


def test_a_performance_case_whose_replay_score_collapses_loses_its_ratio(world):
    black = [np.zeros_like(f) for f in world.ref]
    write_output(os.path.join(world.run, "cases", "perf_case"), black)
    again = _regrade(world, out="gated")
    case = next(c for c in again["cases"] if c["name"] == "perf_case")
    assert case["detail"]["ratio"] == float("inf")
    assert case["score"] == 0.0
    assert "gate" in case["detail"]["wall_time"]


def test_a_procedural_case_is_rechecked_not_copied(world):
    again = _regrade(world)
    case = next(c for c in again["cases"] if c["name"] == "proc_case")
    names = [c["check"] for c in case["detail"]["checks"]]
    assert "output_matches_reference" in names and "error_codes" in names
    assert case["detail"]["replay_score"] > 0


def test_regrade_never_writes_into_the_run_or_the_cache(world):
    def stamp(root):
        return {p: os.path.getmtime(p) for d, _, fs in os.walk(root) for p in (os.path.join(d, f) for f in fs)}
    before = stamp(world.run), stamp(world.cache)
    _regrade(world, out="elsewhere")
    assert (stamp(world.run), stamp(world.cache)) == before


def test_regrade_needs_no_driver(world, monkeypatch):
    monkeypatch.setattr(runner, "drive", lambda *a, **k: pytest.fail("regrade must not run the driver"))
    _regrade(world, out="nodriver")


def test_the_cli_refuses_a_directory_that_is_not_a_graded_run(world, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--corpus", world.corpus, "--cache", world.cache, "regrade", "--run", str(tmp_path / "nothing")],
                 instance=world.instance)
    with pytest.raises(SystemExit):
        cli.main(["--corpus", world.corpus, "--cache", world.cache, "regrade"], instance=world.instance)


def test_the_cli_writes_the_default_directory_inside_the_run(world):
    cli.main(["--corpus", world.corpus, "--cache", world.cache, "--assets", world.corpus,
              "regrade", "--run", world.run], instance=world.instance)
    assert os.path.exists(os.path.join(regrade.default_out(world.instance, world.run), "report.json"))


def test_only_restricts_the_regrade_to_the_named_cases(world):
    again = _regrade(world, out="subset", only=["rep_case"])
    assert [c["name"] for c in again["cases"]] == ["rep_case"]
    assert again["aggregate"]["categories_present"] == ["replay"]


def _attempt(world, tmp_path):
    attempt = tmp_path / "attempt"
    (attempt / "checkpoints").mkdir(parents=True)
    shutil.copytree(world.run, attempt / "grade")
    curve = []
    for i, cp in enumerate(("000001", "final"), start=1):
        shutil.copytree(world.run, attempt / "checkpoints" / cp / "grade")
        curve.append({"index": i, "id": cp, "elapsed_seconds": 60.0 * i, "trigger": "auto",
                      "source_sha256": "0" * 64, "overall": 0.1234, "full_success": False,
                      "build_failure": False, "report": f"checkpoints/{cp}/grade/report.json",
                      "grade_reused": False, "reused_from": None})
    (attempt / "checkpoint-curve.json").write_text(json.dumps(curve, indent=1))
    return attempt


def test_an_attempt_regrades_its_grade_and_every_checkpoint(world, tmp_path):
    attempt = _attempt(world, tmp_path)
    out = tmp_path / "attempt-regrade"
    result = regrade.regrade_attempt(world.instance, str(attempt), world.corpus, world.cache, str(out), verbose=False)
    assert sorted(result["reports"]) == ["checkpoint-000001", "checkpoint-final", "grade"]
    for label in result["reports"]:
        assert os.path.exists(out / label / "report.json")


def test_the_rebuilt_curve_keeps_the_rows_and_replaces_the_scores(world, tmp_path):
    attempt = _attempt(world, tmp_path)
    out = tmp_path / "attempt-regrade"
    regrade.regrade_attempt(world.instance, str(attempt), world.corpus, world.cache, str(out), verbose=False)
    version = world.instance.metric.version
    curve = json.loads((out / f"checkpoint-curve.v{version}.json").read_text())
    source = json.loads((attempt / "checkpoint-curve.json").read_text())
    assert [r["id"] for r in curve] == [r["id"] for r in source]
    for row, was in zip(curve, source):
        assert row["elapsed_seconds"] == was["elapsed_seconds"]
        assert row["metric_version"] == version
        assert row["overall"] == pytest.approx(world.report["aggregate"]["overall"])
        assert row["report"] == f"checkpoint-{row['id']}/report.json"
    assert json.loads((attempt / "checkpoint-curve.json").read_text()) == source


def test_grade_dev_runs_are_regraded_only_when_asked(world, tmp_path):
    attempt = _attempt(world, tmp_path)
    shutil.copytree(world.run, attempt / "grade_dev" / "000003")
    labels = [label for label, _ in regrade.attempt_run_dirs(str(attempt))]
    assert "grade_dev-000003" in labels
    r = regrade.regrade_attempt(world.instance, str(attempt), world.corpus, world.cache, str(tmp_path / "no-dev"), verbose=False)
    assert "grade_dev-000003" not in r["reports"]
    r2 = regrade.regrade_attempt(world.instance, str(attempt), world.corpus, world.cache, str(tmp_path / "with-dev"),
                                 include_dev=True, verbose=False)
    assert "grade_dev-000003" in r2["reports"]


def test_a_build_failure_source_report_still_prints_and_regrades(world, tmp_path, capsys):
    src = os.path.join(world.run, "report.json")
    report = json.loads(open(src).read())
    report["aggregate"] = dict(report["aggregate"], categories={}, overall=None)
    open(src, "w").write(json.dumps(report))
    out = regrade.regrade_run(world.instance, world.run, world.corpus, world.cache, str(tmp_path / "bf"), verbose=True)
    printed = capsys.readouterr().out
    assert "overall" in printed and "-" in printed
    assert out["aggregate"]["overall"] == pytest.approx(world.report["aggregate"]["overall"], abs=1e-12)
    assert out["regraded_from"]["overall"] is None
