"""Docker-free tests for evalbase.reports.controls_summary.

A tiny fake run tree with the JSON layout the grader writes, and the table math.
"""
import json
import os
import statistics

import pytest

from evalbase.reports import controls_summary as cs


@pytest.fixture(autouse=True)
def configured():
    cs.configure(None, controls=["gain_x2", "stale_output", "one_thread"],
                 extra=["midgray", "normal_garbage", "hardcode_dev", "quarter_spp",
                        "malformed-pfm", "malformed-huge", "malformed-crash"],
                 corpora=["public", "hidden"])
    yield


def _case(name, category, score, family=None, **detail):
    return {"name": name, "category": category, "family": family or name, "score": score, "detail": detail}


def _report(label, cases, overall, replay=None, procedural=None, performance=None, full_success=False):
    categories = {}
    if replay is not None:
        categories["replay"] = {"score": replay, "weight": 0.6, "effective_weight": 0.6, "n": 1, "cases": {}}
    if procedural is not None:
        categories["procedural"] = {"score": procedural, "weight": 0.3, "effective_weight": 0.3, "n": 1, "cases": {}}
    if performance is not None:
        categories["performance"] = {"score": performance, "weight": 0.1, "effective_weight": 0.1, "n": 1, "cases": {}}
    return {"label": label, "lib_dir": label, "corpus": "corpus/public", "cache": "runs/refcache", "image": "img",
            "aggregate": {"categories": categories, "overall": overall, "n": len(cases),
                          "categories_present": list(categories), "weights_renormalised": False,
                          "full_success": full_success},
            "cases": cases}


def _write_report(runs_dir, dirname, report):
    d = os.path.join(runs_dir, dirname)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "report.json"), "w") as f:
        json.dump(report, f)


def test_summary_table_and_full_success(tmp_path):
    runs_dir = str(tmp_path / "runs")
    cases = [_case("case_a", "replay", 0.95, threshold=0.03), _case("case_b", "replay", 0.40, threshold=0.05),
             _case("proc_x", "procedural", 1.0)]
    _write_report(runs_dir, "control-gain_x2-public",
                  _report("control-gain_x2-public", cases, overall=0.80, replay=0.675, procedural=1.0))
    lines, reports = cs.build_summary_table(runs_dir)
    row = next(l for l in lines if l.startswith("| `gain_x2` | public"))
    assert "0.800" in row and "0.675" in row and "False" in row
    assert any("stale_output` | public | (not run)" in l for l in lines)
    assert ("gain_x2", "public") in reports


def test_lowest_replay_cases_sorted_ascending():
    cases = [_case("hi", "replay", 0.99, threshold=0.03), _case("lo", "replay", 0.10, threshold=0.03),
             _case("mid", "replay", 0.50, threshold=0.03)] + [_case(f"filler{i}", "replay", 1.0, threshold=0.03) for i in range(10)]
    rows = cs.lowest_replay_cases(_report("x", cases, overall=0.5, replay=0.5), n=8)
    assert len(rows) == 8 and rows[0]["name"] == "lo" and rows[1]["name"] == "mid" and rows[0]["threshold"] == 0.03


def test_perfvar_min_median_max(tmp_path):
    runs_dir = str(tmp_path / "runs")
    ratios, scores = [1.0, 2.0, 3.0, 4.0, 5.0], [0.99, 0.95, 0.90, 0.80, 0.60]
    for i, (ratio, score) in enumerate(zip(ratios, scores), start=1):
        cases = [_case("perf_x", "performance", score, ratio=ratio, replay_score=1.0)]
        _write_report(runs_dir, os.path.join("perfvar", f"run{i}", "public"),
                      _report(f"perfvar-public-run{i}", cases, overall=score, performance=score))
    by_replay = cs.perfvar_rows(runs_dir, "public")
    d = by_replay["perf_x"]
    assert d["ratios"] == ratios and statistics.median(d["ratios"]) == 3.0
    lines = cs.build_perfvar_table(runs_dir)
    table_line = next(l for l in lines if l.startswith("| public | perf_x"))
    assert "| 1.000 | 3.000 | 5.000 |" in table_line and "0.6000" in table_line


def test_perfvar_ignores_inf_ratio(tmp_path):
    runs_dir = str(tmp_path / "runs")
    for i, ratio in enumerate([1.0, float("inf"), 2.0], start=1):
        cases = [_case("perf_y", "performance", 0.5, ratio=ratio, replay_score=1.0)]
        _write_report(runs_dir, os.path.join("perfvar", f"run{i}", "public"),
                      _report(f"perfvar-public-run{i}", cases, overall=0.5, performance=0.5))
    by_replay = cs.perfvar_rows(runs_dir, "public")
    assert by_replay["perf_y"]["ratios"] == [1.0, 2.0] and len(by_replay["perf_y"]["scores"]) == 3


def test_metric_constants_and_version_come_from_the_instance(toy):
    cs.configure(toy)
    consts = cs.metric_constants()
    for name in ("k_noise", "k_sens", "t_lo", "t_hi", "hill_n", "perf_half", "perturbations"):
        assert name in consts
    assert cs.metric_version() == "v" + toy.metric.version
    assert cs.CONTROL_NAMES == ["brightness_x2", "half_samples", "stale_output"]


def test_log_image_id_and_timestamps(tmp_path):
    log = tmp_path / "controls.log"
    log.write_text("=== run_controls start 2026-09-17T00:00:00Z ===\nimage: x\nimage id: sha256:deadbeef\n"
                   "--- stuff ---\n=== run_controls end 2026-09-17T01:00:00Z ===\n")
    assert cs.log_image_id(str(log)) == "sha256:deadbeef"
    assert cs.log_timestamps(str(log)) == ("2026-09-17T00:00:00Z", "2026-09-17T01:00:00Z")
    missing = str(tmp_path / "nope.log")
    assert cs.log_image_id(missing) is None and cs.log_timestamps(missing) == (None, None)


def test_write_replaces_only_marked_section(tmp_path):
    controls_md = tmp_path / "CONTROLS.md"
    controls_md.write_text("# Controls\n\nintro\n\n<!-- controls-summary:begin -->\nold content here\n"
                           "<!-- controls-summary:end -->\n\n## gain_x2\n\ndetails that must survive\n")
    cs._replace_marked(str(controls_md), "controls-summary", "NEW CONTENT")
    text = controls_md.read_text()
    assert "NEW CONTENT" in text and "old content here" not in text and "details that must survive" in text
    f = tmp_path / "NoMarkers.md"
    f.write_text("nothing here\n")
    with pytest.raises(SystemExit):
        cs._replace_marked(str(f), "controls-summary", "x")


def test_corpus_size(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    for i in range(3):
        (d / f"r{i}.json").write_text("{}")
    (d / "notjson.txt").write_text("x")
    assert cs.corpus_size(str(d)) == 3


def test_extra_controls_are_absent_until_they_are_run(tmp_path):
    runs_dir = str(tmp_path / "runs")
    _write_report(runs_dir, "control-gain_x2-public", _report("control-gain_x2-public", [], overall=0.5, replay=0.5))
    lines, reports = cs.extra_control_rows(runs_dir)
    assert lines == [] and reports == {}
    _write_report(runs_dir, "control-midgray-public",
                  _report("control-midgray-public", [_case("case_a", "replay", 0.02)], overall=0.012, replay=0.02))
    lines, reports = cs.extra_control_rows(runs_dir)
    assert len(lines) == 1 and lines[0].startswith("| `midgray` | public | 0.012 | 0.020 |")


def _regrade_report(label, cases, overall, from_version=None, from_overall=None, **kw):
    rep = _report(label, cases, overall, **kw)
    rep["metric_version"] = "0.5"
    rep["regraded_from"] = {"run_dir": f"runs/{label}", "report": f"runs/{label}/report.json",
                            "report_sha256": "0" * 64, "metric_version": from_version, "overall": from_overall}
    rep["regrade_reused_cases"] = {}
    return rep


def _write_regrade(runs_dir, dirname, report, version="0.5"):
    d = os.path.join(runs_dir, dirname, f"regrade-v{version}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "report.json"), "w") as f:
        json.dump(report, f)


def _both(runs_dir, dirname, native_overall, regrade_overall, **kw):
    _write_report(runs_dir, dirname, _report(dirname, [], overall=native_overall, replay=native_overall, **kw))
    _write_regrade(runs_dir, dirname, _regrade_report(dirname, [], overall=regrade_overall, replay=regrade_overall,
                                                      from_version=None, from_overall=native_overall, **kw))


def test_regrade_is_off_by_default(tmp_path):
    runs_dir = str(tmp_path / "runs")
    _both(runs_dir, "control-gain_x2-public", 0.485, 0.215)
    lines, _ = cs.build_summary_table(runs_dir)
    row = next(l for l in lines if l.startswith("| `gain_x2` | public"))
    assert "0.485" in row and "0.215" not in row
    assert cs.ReportSource().footnote() == []


def test_regrade_prefers_the_regraded_report_and_footnotes_the_rest(tmp_path):
    runs_dir = str(tmp_path / "runs")
    _both(runs_dir, "control-gain_x2-public", 0.485, 0.215)
    _write_report(runs_dir, "control-one_thread-public", _report("control-one_thread-public", [], overall=0.997, replay=1.0))
    source = cs.ReportSource(regrade="0.5")
    lines, reports = cs.build_summary_table(runs_dir, source)
    assert "0.215" in next(l for l in lines if l.startswith("| `gain_x2` | public"))
    assert "0.997" in next(l for l in lines if l.startswith("| `one_thread` | public"))
    assert source.rows["control-gain_x2-public"]["regraded"] is True
    assert source.rows["control-one_thread-public"]["regraded"] is False
    note = "\n".join(source.footnote())
    assert "1 of 2" in note and "`control-one_thread-public`" in note and "regrade-v0.5" in note


def test_main_regrade_writes_the_footnote_and_the_perfvar_note(tmp_path, capsys):
    runs_dir = tmp_path / "runs"
    _both(str(runs_dir), "control-gain_x2-public", 0.485, 0.215)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "CONTROLS.md").write_text("# Controls\n\n<!-- controls-summary:begin -->\nold\n<!-- controls-summary:end -->\n\nkeep\n")
    (reports_dir / "PERF_VARIANCE.md").write_text("# Perf\n\n<!-- perfvar:begin -->\nold\n<!-- perfvar:end -->\n\nkeep\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.json").write_text("{}")
    cs.main(["--runs-dir", str(runs_dir), "--reports-dir", str(reports_dir), "--corpus-public", str(corpus),
             "--corpus-hidden", str(corpus), "--controls", "gain_x2,stale_output,one_thread",
             "--controls-log", str(tmp_path / "missing.log"), "--regrade", "v0.5", "--write"])
    controls = (reports_dir / "CONTROLS.md").read_text()
    assert "Source: regrade." in controls and "regrade-v0.5" in controls and "0.215" in controls and "keep" in controls
    perf = (reports_dir / "PERF_VARIANCE.md").read_text()
    assert "performance-only" in perf and "keep" in perf
