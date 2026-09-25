"""The perturbed-reference control, computed offline from a refcache."""
import json
import os

import numpy as np

from evalbase.grader import perturbed
from examples_helpers import picture, write_output


def test_perturbed_control_scores(tmp_path, toy):
    cache, corpus = tmp_path / "refcache", tmp_path / "corpus"
    corpus.mkdir()
    name = "fake_case"
    cdir = cache / name
    cdir.mkdir(parents=True)
    ref = picture(0, 64)
    gray = np.full_like(ref, 0.5)
    write_output(cdir / "n1", [ref, ref])
    write_output(cdir / "halfsamples", [ref, ref])      # identical -> D = 0 -> exactly 1.0
    write_output(cdir / "jitter", [gray, gray])         # uniform vs structured -> D = 1 -> near 0
    write_output(cdir / "n2", [ref, gray])              # one clean, one pathological -> ~0.5
    (cdir / "refcache.json").write_text(json.dumps({
        "replay": name, "snapshots": 2, "threshold": 0.05, "metric_version": toy.metric.version,
        "noise": 0.0, "sensitivity": 0.0, "motion_p90": 0.0}))
    (corpus / f"{name}.json").write_text(json.dumps({"name": name, "category": "replay", "family": "fake_family"}))
    result = perturbed.perturbed_control(toy, str(cache), str(corpus))
    row = result["rows"][0]
    assert row["replay"] == name and row["family"] == "fake_family" and row["T"] == 0.05
    assert row["s_halfsamples"] == 1.0 and row["s_jitter"] < 0.01
    assert 0.49 < row["s_n2"] < 0.51
    assert [r for _, _, r in result["modes"]] == ["defines T", "defines T", "accumulation"]
    agg = result["aggregate"]
    assert agg["s_halfsamples"]["category_score"] == 1.0 and agg["s_halfsamples"]["n_below_0.9"] == 0
    assert agg["s_jitter"]["n_below_0.9"] == 1 and agg["s_jitter"]["below_0.9"][0][0] == name


def test_an_older_entry_is_scored_at_the_threshold_of_record(tmp_path, toy, capsys):
    """An entry stamped by an older metric is recalibrated from its own outputs,
    exactly as grading does, so the column means what the metric says."""
    cache, corpus = tmp_path / "refcache", tmp_path / "corpus"
    corpus.mkdir()
    name = "fake_case"
    cdir = cache / name
    cdir.mkdir(parents=True)
    ref = picture(0, 64)
    noisy = lambda sigma, seed: [np.clip(ref + np.random.default_rng(seed).normal(0, sigma, ref.shape), 0, 1).astype(np.float32)]
    write_output(cdir / "n1", [ref])
    write_output(cdir / "n2", [ref])
    write_output(cdir / "halfsamples", noisy(0.07, 1))
    write_output(cdir / "jitter", noisy(0.05, 2))
    (cdir / "refcache.json").write_text(json.dumps({
        "replay": name, "snapshots": 1, "threshold": 0.3, "metric_version": "0.0",
        "noise": 0.0, "sensitivity": 0.15, "motion_p90": 0.0}))
    (corpus / f"{name}.json").write_text(json.dumps({"name": name, "category": "replay", "family": "fake_family"}))
    out = tmp_path / "out.json"
    perturbed.main(["--instance", str(toy.root), "--cache", str(cache), "--corpus", str(corpus), "--json", str(out)])
    row = json.loads(out.read_text())["rows"][0]
    assert row["T"] < 0.3 and row["s_halfsamples"] > 0.85
    assert "defines T" in capsys.readouterr().out
