"""The tolerance set is part of the metric: run vs tolerated perturbations,
recalibration from cached outputs, staleness of a cache missing a run.

The toy tolerates both of its perturbations, so a second MetricSpec with one of
them untolerated stands in for the version change.
"""
import dataclasses
import json
import os
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from evalbase.grader import cli, metrics, runner
from evalbase.interfaces import DriveResult, MetricSpec
from examples_helpers import noisier, picture, write_case, write_output


def _mean_defect(scorer, snapshots, other):
    return float(np.mean([scorer.distance(a, b) for a, b in zip(snapshots, other)]))


def test_tolerated_is_a_subset_of_run(toy):
    assert set(toy.metric.tolerance_perturbations) <= set(toy.metric.perturbations)
    with pytest.raises(ValueError):
        MetricSpec(perturbations=("a",), tolerance_perturbations=("a", "b")).validate()


def test_a_sub_floor_perturbation_has_no_defect_at_all(toy):
    snapshots = [picture(s, 64) for s in range(3)]
    assert all(_mean_defect(toy.scorer, snapshots, noisier(snapshots, 0.02, seed)) == 0.0 for seed in range(4))
    assert _mean_defect(toy.scorer, snapshots, noisier(snapshots, 0.12, 7)) > 0.05


def test_sensitivity_is_the_max_over_every_perturbation(toy):
    d = toy.scorer.distance
    snapshots = [picture(s, 64) for s in range(3)]
    mild, loud = noisier(snapshots, 0.08, 1), noisier(snapshots, 0.12, 2)
    assert _mean_defect(toy.scorer, snapshots, loud) > _mean_defect(toy.scorer, snapshots, mild) > 0
    t_mild = metrics.replay_threshold(d, snapshots, None, [mild], toy.metric)
    t_both = metrics.replay_threshold(d, snapshots, None, [mild, loud], toy.metric)
    assert t_both.sensitivity == metrics.replay_threshold(d, snapshots, None, [loud, mild], toy.metric).sensitivity
    assert t_both.sensitivity == pytest.approx(_mean_defect(toy.scorer, snapshots, loud))
    assert t_both.threshold >= t_mild.threshold


def test_a_tolerated_perturbation_is_accepted_and_an_untolerated_one_is_not(toy):
    d = toy.scorer.distance
    snapshots = [picture(s, 64) for s in range(3)]
    half, quarter = noisier(snapshots, 0.07, 1), noisier(snapshots, 0.11, 2)
    narrow = metrics.replay_threshold(d, snapshots, None, [half], toy.metric)
    wide = metrics.replay_threshold(d, snapshots, None, [half, quarter], toy.metric)
    assert narrow.threshold < wide.threshold <= toy.metric.t_hi
    # "the threshold must accept what defined it": Hill at D = sensitivity is
    # 1/(1 + 0.5**4) = 0.941 per snapshot, so the defining run clears 0.85 with
    # per-snapshot variation
    assert metrics.score_replay(d, snapshots, half, narrow.threshold)["score"] > 0.85
    assert metrics.hill(narrow.sensitivity, narrow.threshold) == pytest.approx(1 / (1 + 0.5 ** 4))
    penalised = metrics.score_replay(d, snapshots, quarter, narrow.threshold)["score"]
    tolerated = metrics.score_replay(d, snapshots, quarter, wide.threshold)["score"]
    assert penalised < 0.1 < 0.7 < tolerated


# ------------------------------------------------------------------ the drives

def _stub_drive(monkeypatch, calls, snapshots=2):
    def fake_drive(instance, replay_path, outdir, assets_dir, lib_dir=None, sample_mult=1, perturb="", **kw):
        calls.append({"outdir": os.path.basename(outdir), "lib_dir": lib_dir, "sample_mult": sample_mult,
                      "perturb": perturb})
        os.makedirs(outdir, exist_ok=True)
        events = []
        for i in range(snapshots):
            events += [{"op": "run", "wall_seconds": 0.01},
                       {"op": "snapshot", "name": f"snap_{i:03d}", "files": {"gray": f"snap_{i:03d}.pgm"}}]
        ledger = {"exit": "ok", "events": events, "errors": []}
        with open(os.path.join(outdir, "ledger.json"), "w") as f:
            json.dump(ledger, f)
        return DriveResult(outdir, ledger, 0, 0.5, False, "")

    monkeypatch.setattr(runner, "drive", fake_drive)
    monkeypatch.setattr(runner, "load_snapshot", lambda instance, outdir, event, channel=None: np.zeros((16, 16), np.float32))


def test_build_refcache_drives_every_run_with_the_right_arguments(tmp_path, monkeypatch, toy):
    calls = []
    _stub_drive(monkeypatch, calls)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    info = runner.build_refcache(toy, str(write_case(corpus, "case")), str(cache), str(corpus))
    assert [c["outdir"] for c in calls] == ["n1", "n2", *toy.metric.perturbations]
    by_dir = {c["outdir"]: c for c in calls}
    assert by_dir["n1"]["sample_mult"] == 1 and by_dir["n1"]["perturb"] == ""
    assert by_dir["n2"]["sample_mult"] == 2 and by_dir["n2"]["perturb"] == ""
    for mode in toy.metric.perturbations:
        assert by_dir[mode] == {"outdir": mode, "lib_dir": None, "sample_mult": 1, "perturb": mode}
    assert info["perturbations"] == list(toy.metric.perturbations)
    assert info["tolerance_perturbations"] == list(toy.metric.tolerance_perturbations)
    assert set(info["perturbation_defects"]) == set(toy.metric.perturbations)


def test_a_failing_perturbation_run_stops_the_build(tmp_path, monkeypatch, toy):
    calls = []
    _stub_drive(monkeypatch, calls)
    good = runner.drive

    def fail_on_jitter(instance, replay_path, outdir, *a, perturb="", **kw):
        if perturb == "jitter":
            os.makedirs(outdir, exist_ok=True)
            return DriveResult(outdir, {"exit": "crash", "events": []}, 1, 0.1, False, "")
        return good(instance, replay_path, outdir, *a, perturb=perturb, **kw)

    monkeypatch.setattr(runner, "drive", fail_on_jitter)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    with pytest.raises(RuntimeError, match="reference jitter failed"):
        runner.build_refcache(toy, str(write_case(corpus, "case")), str(cache), str(corpus))


def _args(instance, corpus, cache, **kw):
    base = dict(instance=instance, corpus=str(corpus), cache=str(cache), assets=str(corpus), cpus=None,
                only=None, force=False, trust_unstamped=False, stamp=False, owner=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _old_cache(tmp_path, monkeypatch, toy):
    """A complete cache with its jitter/ run removed: an entry from an older metric."""
    calls = []
    _stub_drive(monkeypatch, calls)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    replay = write_case(corpus, "case")
    cli.cmd_refcache(_args(toy, corpus, cache))
    shutil.rmtree(cache / "case" / "jitter")
    return corpus, cache, replay, calls


def test_a_cache_without_a_perturbation_run_is_stale_and_is_rebuilt(tmp_path, monkeypatch, capsys, toy):
    corpus, cache, _, calls = _old_cache(tmp_path, monkeypatch, toy)
    capsys.readouterr()
    calls.clear()
    cli.cmd_refcache(_args(toy, corpus, cache))
    out = capsys.readouterr().out
    assert f"stale (no jitter run (metric v{toy.metric.version})), rebuilding" in out
    assert [c["outdir"] for c in calls] == ["n1", "n2", *toy.metric.perturbations]
    cli.cmd_refcache(_args(toy, corpus, cache))
    assert "[skip] case (cached)" in capsys.readouterr().out


def test_trust_unstamped_does_not_excuse_a_missing_perturbation(tmp_path, monkeypatch, capsys, toy):
    corpus, cache, _, _ = _old_cache(tmp_path, monkeypatch, toy)
    capsys.readouterr()
    cli.cmd_refcache(_args(toy, corpus, cache, trust_unstamped=True))
    assert "stale (no jitter run" in capsys.readouterr().out


def test_stamp_refuses_a_cache_without_every_run(tmp_path, monkeypatch, capsys, toy):
    corpus, cache, _, _ = _old_cache(tmp_path, monkeypatch, toy)
    entry = cache / "case" / "refcache.json"
    rc = json.loads(entry.read_text())
    rc.pop("replay_sha256")
    entry.write_text(json.dumps(rc))
    capsys.readouterr()
    monkeypatch.setattr(runner, "drive", lambda *a, **k: pytest.fail("stamp must not run the driver"))
    result = cli.cmd_refcache(_args(toy, corpus, cache, stamp=True))
    assert result["refused"] == ["case"]
    assert "needs a rebuild, not a stamp" in capsys.readouterr().out


# ------------------------------------------- calibration against real outputs

def _mode_drive(monkeypatch, tmp_path, toy, sigmas):
    ref = [picture(seed, 32) for seed in range(2)]
    snapshots = {"": ref}
    for mode, (sigma, seed) in sigmas.items():
        snapshots[mode] = noisier(ref, sigma, seed)

    def fake_drive(instance, replay_path, outdir, assets_dir, lib_dir=None, sample_mult=1, perturb="", **kw):
        ledger = write_output(outdir, snapshots[perturb], 0.5)
        return DriveResult(outdir, ledger, 0, 1.0, False, "")

    monkeypatch.setattr(runner, "drive", fake_drive)
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    info = runner.build_refcache(toy, str(write_case(corpus, "case")), str(cache), str(corpus))
    return info, corpus, cache, ref, snapshots


SIGMAS = {"halfsamples": (0.13, 1), "jitter": (0.06, 2)}


def test_the_cached_threshold_comes_from_the_tolerated_runs(tmp_path, monkeypatch, toy):
    info, _, cache, _, _ = _mode_drive(monkeypatch, tmp_path, toy, SIGMAS)
    # what the cache holds is what was scored: the snapshots as the scorer reads them back
    cdir = str(cache / "case")
    ref = runner.cache_snapshots(toy, cdir, "n1")
    snapshots = {m: runner.cache_snapshots(toy, cdir, m) for m in toy.metric.perturbations}
    tolerated = metrics.replay_threshold(toy.scorer.distance, ref, None,
                                         [snapshots[m] for m in toy.metric.tolerance_perturbations], toy.metric)
    assert info["sensitivity"] == pytest.approx(tolerated.sensitivity)
    assert info["threshold"] == pytest.approx(tolerated.threshold)
    for mode in toy.metric.perturbations:
        assert info["perturbation_defects"][mode] == pytest.approx(_mean_defect(toy.scorer, ref, snapshots[mode]))


def test_an_entry_calibrated_by_an_older_metric_is_recalibrated_not_rebuilt(tmp_path, monkeypatch, toy):
    """A cache built under a wider tolerance has every run the narrower metric
    needs, so its threshold is recovered from the snapshots it already has."""
    info, corpus, cache, ref, snapshots = _mode_drive(monkeypatch, tmp_path, toy, SIGMAS)
    narrower = dataclasses.replace(toy.metric, version="0.2", tolerance_perturbations=("jitter",))
    instance = dataclasses.replace(toy, metric=narrower)
    entry = cache / "case" / "refcache.json"
    stored = json.loads(entry.read_text())
    assert stored["metric_version"] == toy.metric.version != narrower.version
    monkeypatch.setattr(runner, "drive", lambda *a, **k: pytest.fail("must not run the driver"))
    threshold, note = runner.entry_threshold(instance, str(cache / "case"), stored)
    want = metrics.replay_threshold(toy.scorer.distance, ref, None, [snapshots["jitter"]], narrower).threshold
    assert threshold == pytest.approx(want) and threshold < stored["threshold"]
    assert f"calibrated by metric v{toy.metric.version}" in note
    assert json.loads(entry.read_text())["threshold"] == stored["threshold"]   # reading is not writing
    assert runner.entry_threshold(toy, str(cache / "case"), stored) == (stored["threshold"], None)


def test_recalibrate_rewrites_the_cache_without_running_the_driver(tmp_path, monkeypatch, capsys, toy):
    info, corpus, cache, ref, snapshots = _mode_drive(monkeypatch, tmp_path, toy, SIGMAS)
    narrower = dataclasses.replace(toy.metric, version="0.2", tolerance_perturbations=("jitter",))
    instance = dataclasses.replace(toy, metric=narrower)
    entry = cache / "case" / "refcache.json"
    before = json.loads(entry.read_text())
    monkeypatch.setattr(runner, "drive", lambda *a, **k: pytest.fail("must not run the driver"))
    result = cli.cmd_refcache(_args(instance, corpus, cache, recalibrate=True))
    assert result == {"recalibrated": ["case"], "skipped": [], "refused": []}
    now = json.loads(entry.read_text())
    assert now["metric_version"] == "0.2" and now["tolerance_perturbations"] == ["jitter"]
    assert now["threshold"] < before["threshold"]
    assert now["replay_sha256"] == before["replay_sha256"] and now["reference_wall_seconds"] == before["reference_wall_seconds"]
    assert cli.cmd_refcache(_args(instance, corpus, cache, recalibrate=True))["skipped"] == ["case"]


def test_recalibrate_refuses_an_entry_that_is_missing_a_run(tmp_path, monkeypatch, capsys, toy):
    corpus, cache, _, _ = _old_cache(tmp_path, monkeypatch, toy)
    entry = cache / "case" / "refcache.json"
    rc = json.loads(entry.read_text())
    rc["metric_version"] = "0.0"
    entry.write_text(json.dumps(rc))
    capsys.readouterr()
    monkeypatch.setattr(runner, "drive", lambda *a, **k: pytest.fail("must not run the driver"))
    result = cli.cmd_refcache(_args(toy, corpus, cache, recalibrate=True))
    assert result["refused"] == ["case"]
    assert "needs a rebuild" in capsys.readouterr().out
    assert json.loads(entry.read_text()) == rc
