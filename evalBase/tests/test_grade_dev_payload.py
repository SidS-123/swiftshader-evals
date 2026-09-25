"""What `grade_dev` hands back has to fit in a context window.

The checks are counts plus the failures, each failing detail cut at
CHECK_DETAIL_CHARS.
"""
import json
import os
from pathlib import Path

from conftest import make_session
from evalbase.grader import jsonio, runner
from evalbase.harness import tools


def test_passing_checks_become_counts_and_failures_are_kept():
    checks = [{"check": "a", "ok": True, "detail": ""},
              {"check": "b", "ok": True, "detail": ""},
              {"check": "bounds:world", "ok": False, "detail": [[0.0, 0.0], [1.0, 1.0]]}]
    assert tools.checks_summary(checks) == {
        "checks_total": 3, "checks_passed": 2,
        "checks_failed": [{"name": "bounds:world", "detail": "[[0.0, 0.0], [1.0, 1.0]]"}]}


def test_a_detail_is_cut_at_the_cap():
    summary = tools.checks_summary([{"check": "c", "ok": False, "detail": "x" * 500}])
    assert summary["checks_failed"][0]["detail"] == "x" * tools.CHECK_DETAIL_CHARS
    assert tools.CHECK_DETAIL_CHARS == 160


def test_a_mapping_detail_is_written_as_text():
    summary = tools.checks_summary([{"check": "pick:centre", "ok": False, "detail": {"hasHit": False, "index": 95}}])
    assert summary["checks_failed"][0]["detail"] == '{"hasHit": false, "index": 95}'


def _checks(n_pass, n_fail, detail_chars):
    checks = [{"check": f"ok_{i}", "ok": True, "detail": "y" * detail_chars} for i in range(n_pass)]
    checks += [{"check": f"bounds:fail_{i}", "ok": False, "detail": "z" * detail_chars} for i in range(n_fail)]
    return checks


class FakeGrade:
    def __init__(self, name, category, checks=None):
        self.name, self.category, self.family, self.score = name, category, name, 0.25
        self.detail = {"exit": "ok", "threshold": 0.03, "snapshot_defects": [1.0, 1.0],
                       "snapshot_scores": [0.5, 0.5], "first_diverge_snapshot": 0,
                       "checks": checks, "ratio": None, "replay_score": 0.25,
                       "stderr_tail": "", "candidate_wall_seconds": 1.0, "reference_wall_seconds": 1.0}


def _untrimmed(report, grades):
    old = json.loads(jsonio.dumps(report))
    for case, grade in zip(old["cases"], grades):
        for key in ("checks_total", "checks_passed", "checks_failed"):
            case.pop(key)
        case["checks"] = grade.detail["checks"]
    return old


def _full_corpus(tmp_path, monkeypatch, toy):
    session = make_session(tmp_path, toy)
    corpus = tmp_path / "corpus57"
    corpus.mkdir()
    grades = []
    for i in range(57):
        name = f"case_{i:02d}"
        (corpus / f"{name}.json").write_text("{}")
        procedural = i % 6 == 0
        grades.append(FakeGrade(name, "procedural" if procedural else "replay", _checks(24, 6, 400) if procedural else None))
    session.corpus_public = str(corpus)
    session.refcache = str(tmp_path / "refcache")
    session.corpus_assets = str(tmp_path / "assets")
    (session.workspace / "candidate.py").write_text("class Painter: pass\n")
    by_name = {g.name: g for g in grades}
    monkeypatch.setattr(runner, "grade_replay", lambda instance, replay, *a, **k: by_name[os.path.basename(replay)[:-5]])
    return session, grades


def test_the_full_corpus_payload_more_than_halves(tmp_path, monkeypatch, toy):
    session, grades = _full_corpus(tmp_path, monkeypatch, toy)
    report = session.grade_dev()
    after = len(jsonio.dumps(report))
    before = len(jsonio.dumps(_untrimmed(report, grades)))
    assert len(report["cases"]) == 57 and after < before / 2, f"{before} -> {after}"


def test_the_trimmed_payload_still_names_every_failed_check(tmp_path, monkeypatch, toy):
    session, _ = _full_corpus(tmp_path, monkeypatch, toy)
    report = session.grade_dev()
    procedural = [c for c in report["cases"] if c["category"] == "procedural"]
    assert procedural
    for case in procedural:
        assert case["checks_total"] == 30 and case["checks_passed"] == 24
        assert [f["name"] for f in case["checks_failed"]] == [f"bounds:fail_{i}" for i in range(6)]
        assert all(len(f["detail"]) == tools.CHECK_DETAIL_CHARS for f in case["checks_failed"])
    replay = [c for c in report["cases"] if c["category"] == "replay"][0]
    assert replay["checks_total"] == 0 and replay["checks_failed"] == [] and "checks" not in replay


def test_the_file_on_disk_is_the_same_document(tmp_path, monkeypatch, toy):
    session, _ = _full_corpus(tmp_path, monkeypatch, toy)
    report = session.grade_dev(["case_00"])
    written = sorted(Path(session.run_dir).glob("grade_dev/*/report.json"))[-1]
    assert json.loads(written.read_text()) == report
    assert "\"checks\":" not in written.read_text()


def test_the_tool_description_tells_the_model_what_it_gets(toy):
    grade_dev = [t for t in tools.tool_schemas(toy.task.tool_descriptions) if t["function"]["name"] == "grade_dev"][0]
    assert "failed check" in grade_dev["function"]["description"]
