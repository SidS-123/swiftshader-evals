"""A timed-out performance case must not cost the model its feedback.

`metrics.perf_score(inf)` is 0.0, but the ratio itself stays `inf` so that
`aggregate()` can test its full-success bar. `inf` is not JSON; everything
goes through evalbase/grader/jsonio.py: null, with a note, and never an
exception. No score changes -- the sanitizer runs at serialization time.
"""
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import make_session
from evalbase.grader import cli, jsonio, runner


# ------------------------------------------------------------- the encoder

def test_a_non_finite_ratio_becomes_null_with_a_note():
    text = jsonio.dumps({"name": "perf_case", "score": 0.0, "ratio": float("inf")})
    back = json.loads(text)
    assert back == {"name": "perf_case", "score": 0.0, "ratio": None, "ratio_note": jsonio.RATIO_NOTE}


def test_a_nan_ratio_says_so_differently():
    assert json.loads(jsonio.dumps({"ratio": float("nan")}))["ratio_note"] == jsonio.NAN_NOTE


def test_a_note_the_caller_already_wrote_is_left_alone():
    back = json.loads(jsonio.dumps({"ratio": float("inf"), "ratio_note": "mine"}))
    assert back["ratio_note"] == "mine"


def test_only_ratio_is_annotated():
    back = json.loads(jsonio.dumps({"threshold": float("inf"), "xs": [float("-inf"), 1.0]}))
    assert back == {"threshold": None, "xs": [None, 1.0]}


def test_numpy_scalars_and_nested_structures_survive():
    back = json.loads(jsonio.dumps({"a": np.float32(1.5), "b": np.int64(3), "c": np.bool_(True),
                                    "d": [{"ratio": np.float32("inf")}]}))
    assert back == {"a": 1.5, "b": 3, "c": True, "d": [{"ratio": None, "ratio_note": jsonio.RATIO_NOTE}]}


def test_finite_numbers_are_untouched():
    report = {"aggregate": {"overall": 0.4235, "categories": {"performance": {"score": 0.0}}},
              "cases": [{"score": 0.1875, "ratio": 3.25}]}
    assert json.loads(jsonio.dumps(report)) == report


def test_strict_mode_refuses_instead_of_writing_null(tmp_path):
    with pytest.raises(ValueError, match="not JSON compliant"):
        jsonio.dumps({"threshold": float("nan")}, strict=True)
    with pytest.raises(ValueError, match="not JSON compliant"):
        jsonio.write_json(tmp_path / "refcache.json", {"threshold": float("inf")}, strict=True)


def test_write_json_and_atomic_write_json_produce_strict_json(tmp_path):
    payload = {"cases": [{"name": "p", "ratio": float("inf")}]}
    for path, write in ((tmp_path / "a.json", jsonio.write_json),
                        (tmp_path / "b.json", jsonio.atomic_write_json)):
        write(path, payload)
        text = path.read_text()
        assert "Infinity" not in text
        assert json.loads(text)["cases"][0]["ratio"] is None
    assert not list(tmp_path.glob("*.tmp"))


# ------------------------------------------- the grade_dev tool, end to end

class FakeGrade:
    """What runner.grade_replay returns for a performance case that timed out."""

    def __init__(self, name, category="performance", score=0.0, ratio=float("inf")):
        self.name, self.category, self.family, self.score = name, category, name, score
        self.detail = {"exit": "timeout", "ratio": ratio, "replay_score": 0.0,
                       "threshold": 0.03, "snapshot_defects": [], "snapshot_scores": [],
                       "first_diverge_snapshot": None, "stderr_tail": "",
                       "candidate_wall_seconds": 60.0, "reference_wall_seconds": 4.175}


def _session_ready_to_grade(tmp_path, monkeypatch, toy, grade):
    session = make_session(tmp_path, toy)
    (session.workspace / "candidate.py").write_text("class Painter: pass\n")
    session.refcache = str(tmp_path / "refcache")
    session.corpus_assets = str(tmp_path / "assets")
    monkeypatch.setattr(runner, "grade_replay", lambda *a, **k: grade)
    return session


def test_grade_dev_returns_a_report_when_a_performance_case_timed_out(tmp_path, monkeypatch, toy):
    session = _session_ready_to_grade(tmp_path, monkeypatch, toy, FakeGrade("dev_case"))
    report = session.grade_dev(["dev_case"])
    text = json.dumps(report, allow_nan=False)
    assert "Infinity" not in text
    assert json.loads(text) == report
    categories = report["aggregate"]["categories"]
    assert set(categories) == {"replay", "procedural", "performance"}
    assert categories["performance"]["score"] == 0.0 and categories["performance"]["n"] == 1
    assert report["aggregate"]["overall"] == 0.0
    assert report["graded"] == ["dev_case"]
    case = report["cases"][0]
    assert case["ratio"] is None and case["ratio_note"] == jsonio.RATIO_NOTE
    assert case["score"] == 0.0 and case["exit"] == "timeout"


def test_grade_dev_writes_the_same_report_to_disk(tmp_path, monkeypatch, toy):
    session = _session_ready_to_grade(tmp_path, monkeypatch, toy, FakeGrade("dev_case"))
    report = session.grade_dev(["dev_case"])
    written = sorted(Path(session.run_dir).glob("grade_dev/*/report.json"))[-1]
    assert "Infinity" not in written.read_text()
    assert json.loads(written.read_text()) == report


def test_grade_dev_reaches_the_model_through_the_mcp_message(tmp_path, monkeypatch, toy):
    from evalbase.harness import mcp_server
    session = _session_ready_to_grade(tmp_path, monkeypatch, toy, FakeGrade("dev_case"))
    session.call = lambda name, arguments: session.grade_dev(arguments.get("names"))
    response = mcp_server.response(
        {"method": "tools/call", "params": {"name": "grade_dev", "arguments": {"names": ["dev_case"]}}}, session)
    assert response["isError"] is False
    seen = json.loads(response["content"][0]["text"])
    assert seen["aggregate"]["categories"]["performance"]["score"] == 0.0
    assert seen["cases"][0]["ratio"] is None
    assert seen["cases"][0]["ratio_note"] == jsonio.RATIO_NOTE


def test_a_finite_ratio_is_reported_as_a_number(tmp_path, monkeypatch, toy):
    session = _session_ready_to_grade(tmp_path, monkeypatch, toy, FakeGrade("dev_case", score=0.75, ratio=2.5))
    case = session.grade_dev(["dev_case"])["cases"][0]
    assert case["ratio"] == 2.5 and "ratio_note" not in case


# ---------------------------------------------- the grader's own report.json

def test_the_grader_report_is_strict_json(tmp_path, monkeypatch, toy):
    grades = [runner.ReplayGrade("perf_case", "performance", "perf", 0.0, {"exit": "timeout", "ratio": float("inf")})]
    monkeypatch.setattr(cli, "_replays", lambda corpus, only: ["x.json"])
    monkeypatch.setattr(runner, "grade_replay", lambda *a, **k: grades[0])
    args = SimpleNamespace(instance=toy, out=str(tmp_path / "run"), corpus="c", cache="k", assets="a",
                           cpus=None, only=None)
    report = cli._grade(args, None, "control-x")
    text = (tmp_path / "run" / "report.json").read_text()
    assert "Infinity" not in text
    on_disk = json.loads(text)
    assert on_disk["cases"][0]["detail"]["ratio"] is None
    assert on_disk["cases"][0]["detail"]["ratio_note"] == jsonio.RATIO_NOTE
    assert on_disk["cases"][0]["score"] == 0.0
    assert math.isinf(report["cases"][0]["detail"]["ratio"])
    assert report["aggregate"]["full_success"] is False
