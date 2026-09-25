"""Which checkpoints the progression curve grades, and which it reuses or skips.

No
Docker: the checkpoints are hand-written manifests and the grader is a
counting stub.
"""
import json
import shutil
from pathlib import Path

import pytest

from evalbase.harness import attempt as att


def make_attempt(tmp_path, entries, *, start=1000.0):
    run = tmp_path / "attempt"
    (run / "checkpoints").mkdir(parents=True)
    (run / "attempt.json").write_text(json.dumps({"attempt": "fake", "started_unix": start}))
    for index, (name, note, elapsed, sha) in enumerate(entries, start=1):
        directory = run / "checkpoints" / name
        (directory / "source").mkdir(parents=True)
        (directory / "source" / "x.c").write_text(sha)
        (directory / "checkpoint.json").write_text(json.dumps(
            {"index": index, "note": note, "source_sha256": sha,
             "elapsed_seconds": elapsed, "created": start + elapsed}))
    return run


def timeline(tmp_path, minutes_and_notes, *, sha_of=lambda i: f"sha{i:02d}"):
    entries = [(f"{i + 1:06d}", note, minutes * 60.0, sha_of(i))
               for i, (minutes, note) in enumerate(minutes_and_notes)]
    entries[-1] = ("final", "Final source at the end of the attempt", entries[-1][2], entries[-1][3])
    return make_attempt(tmp_path, entries)


def ids(rows):
    return [row["id"] for row in rows]


def test_trigger_and_wall_time_come_from_the_note_and_the_manifest(tmp_path):
    run = make_attempt(tmp_path, [("000001", "poking at the spec", 30.0, "aa"),
                                  ("000002", "Automatic checkpoint", 900.0, "bb"),
                                  ("final", "Final source at the end of the attempt", 1800.0, "cc")])
    rows = att.read_checkpoints(run)
    assert [r["trigger"] for r in rows] == ["tool", "auto", "final"]
    assert [r["elapsed_seconds"] for r in rows] == [30.0, 900.0, 1800.0]
    assert [r["index"] for r in rows] == [1, 2, 3]
    assert rows[0]["source_sha256"] == "aa"


def test_wall_time_falls_back_to_created_minus_attempt_start(tmp_path):
    run = make_attempt(tmp_path, [("000001", "note", 60.0, "aa")])
    record = run / "checkpoints" / "000001" / "checkpoint.json"
    entry = json.loads(record.read_text()) | {"elapsed_seconds": None}
    record.write_text(json.dumps(entry))
    assert att.read_checkpoints(run)[0]["elapsed_seconds"] == 60.0


def test_a_directory_without_a_manifest_is_not_a_checkpoint(tmp_path):
    run = make_attempt(tmp_path, [("000001", "note", 1.0, "aa")])
    (run / "checkpoints" / "scratch").mkdir()
    assert ids(att.read_checkpoints(run)) == ["000001"]


def test_no_options_selects_every_checkpoint(tmp_path):
    run = timeline(tmp_path, [(m, "note") for m in (0, 1, 2, 3, 200)])
    selected, skipped = att.select_checkpoints(att.read_checkpoints(run))
    assert len(selected) == 5 and skipped == []
    assert all(row["reused_from"] is None for row in selected)


def test_every_keeps_the_last_of_each_bucket_plus_the_first_and_the_final(tmp_path):
    run = timeline(tmp_path, [(0, "a"), (5, "b"), (29, "c"), (31, "d"), (59, "e"), (61, "f"), (75, "g")])
    selected, skipped = att.select_checkpoints(att.read_checkpoints(run), every=30)
    assert ids(selected) == ["000001", "000003", "000005", "final"]
    assert ids(skipped) == ["000002", "000004", "000006"]
    assert all("bucket" in row["reason"] for row in skipped)


def test_every_keeps_the_final_even_when_it_shares_a_bucket_with_a_later_pick(tmp_path):
    run = timeline(tmp_path, [(0, "a"), (10, "b"), (11, "c")])
    selected, _ = att.select_checkpoints(att.read_checkpoints(run), every=60)
    assert ids(selected) == ["000001", "final"]


def test_auto_only_keeps_the_harness_checkpoints_and_the_anchors(tmp_path):
    run = timeline(tmp_path, [(0, "I wrote a note"), (15, "Automatic checkpoint"),
                              (20, "another note"), (30, "Automatic checkpoint"), (40, "done")])
    selected, skipped = att.select_checkpoints(att.read_checkpoints(run), auto_only=True)
    assert ids(selected) == ["000001", "000002", "000004", "final"]
    assert ids(skipped) == ["000003"]
    assert "not auto" in skipped[0]["reason"]


def test_compaction_and_resume_checkpoints_count_as_automatic(tmp_path):
    run = timeline(tmp_path, [(0, "Attempt start or resume"), (5, "mine"),
                              (10, "Before context compaction"), (20, "last")])
    selected, _ = att.select_checkpoints(att.read_checkpoints(run), auto_only=True)
    assert ids(selected) == ["000001", "000003", "final"]


def test_dedupe_keeps_the_point_and_marks_the_reused_grade(tmp_path):
    run = timeline(tmp_path, [(0, "a"), (5, "b"), (10, "c"), (15, "d")],
                   sha_of=lambda i: ["aa", "bb", "bb", "cc"][i])
    selected, skipped = att.select_checkpoints(att.read_checkpoints(run), dedupe=True)
    assert skipped == [] and len(selected) == 4
    assert [row["reused_from"] for row in selected] == [None, None, "000002", None]


def test_dedupe_reuses_the_first_of_a_long_identical_run(tmp_path):
    run = timeline(tmp_path, [(m, "a") for m in (0, 5, 10, 15, 20)], sha_of=lambda i: "aa" if i < 4 else "bb")
    selected, _ = att.select_checkpoints(att.read_checkpoints(run), dedupe=True)
    assert [row["reused_from"] for row in selected] == [None, "000001", "000001", "000001", None]


def test_max_thins_evenly_and_keeps_the_ends(tmp_path):
    run = timeline(tmp_path, [(m, "a") for m in range(9)])
    selected, skipped = att.select_checkpoints(att.read_checkpoints(run), max_points=3)
    assert ids(selected) == ["000001", "000005", "final"]
    assert len(skipped) == 6 and all("--max 3" in row["reason"] for row in skipped)


def test_max_above_the_count_changes_nothing(tmp_path):
    run = timeline(tmp_path, [(m, "a") for m in range(4)])
    selected, skipped = att.select_checkpoints(att.read_checkpoints(run), max_points=99)
    assert len(selected) == 4 and skipped == []


def test_filters_compose_and_max_applies_last(tmp_path):
    run = timeline(tmp_path, [(0, "a"), (10, "Automatic checkpoint"), (20, "Automatic checkpoint"),
                              (40, "Automatic checkpoint"), (50, "b"), (70, "Automatic checkpoint"), (80, "end")])
    rows = att.read_checkpoints(run)
    selected, _ = att.select_checkpoints(rows, every=30, auto_only=True)
    assert ids(selected) == ["000001", "000003", "000004", "final"]
    thinned, _ = att.select_checkpoints(rows, every=30, auto_only=True, max_points=3)
    assert ids(thinned) == ["000001", "000004", "final"]


def test_only_indices_still_win_over_the_anchors(tmp_path):
    run = timeline(tmp_path, [(m, "a") for m in range(5)])
    selected, skipped = att.select_checkpoints(att.read_checkpoints(run), indices={2, 3})
    assert ids(selected) == ["000002", "000003"]
    assert all(row["reason"] == "not in --only" for row in skipped)


def test_only_accepts_final_as_well_as_an_index(tmp_path):
    run = timeline(tmp_path, [(m, "a") for m in range(5)])
    rows = att.read_checkpoints(run)
    for spec in ("final", "final,000002", "5", "000002,3"):
        assert att.select_checkpoints(rows, indices=spec)[0], spec
    assert ids(att.select_checkpoints(rows, indices="final")[0]) == ["final"]
    assert ids(att.select_checkpoints(rows, indices="000002")[0]) == ["000002"]
    assert ids(att.select_checkpoints(rows, indices="2")[0]) == ["000002"]
    assert ids(att.select_checkpoints(rows, indices="final,000002")[0]) == ["000002", "final"]
    assert ids(att.select_checkpoints(rows, indices={2, 3})[0]) == ["000002", "000003"]


def test_parse_checkpoint_selection_keeps_both_spellings():
    assert att.parse_checkpoint_selection(None) is None
    assert att.parse_checkpoint_selection("final") == {"final"}
    assert att.parse_checkpoint_selection("000012") == {"000012", 12}
    assert att.parse_checkpoint_selection(" 3 , final ") == {"3", 3, "final"}
    assert att.parse_checkpoint_selection([7]) == {"7", 7}
    with pytest.raises(ValueError, match="selected nothing"):
        att.parse_checkpoint_selection(",, ")


def test_only_that_matches_no_checkpoint_says_so(tmp_path):
    run = timeline(tmp_path, [(m, "a") for m in range(3)])
    with pytest.raises(ValueError, match="matched no checkpoint"):
        att.select_checkpoints(att.read_checkpoints(run), indices="finel")


def test_the_cli_grades_only_the_final_checkpoint(tmp_path, capsys):
    from evalbase.harness import run as run_cli
    run = timeline(tmp_path, [(0, "a"), (5, "b"), (10, "c")])
    assert run_cli.main(["grade-checkpoints", str(run), "--only", "final", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "3 checkpoints on disk: 1 curve point" in out
    assert "final" in out and "would grade" in out


def test_an_empty_checkpoint_list_selects_nothing():
    assert att.select_checkpoints([]) == ([], [])


@pytest.fixture
def grading(tmp_path, monkeypatch):
    corpus, cache = tmp_path / "corpus", tmp_path / "cache"
    corpus.mkdir()
    (corpus / "a.json").write_text("{}")
    (cache / "a").mkdir(parents=True)
    (cache / "a" / "refcache.json").write_text("{}")
    built = []

    def fake_build_and_grade(instance, source, out, *, corpus, cache, label, owner=None, sandbox="docker"):
        built.append(label)
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        report = {"aggregate": {"overall": 0.1 * len(built), "full_success": False}, "build_failure": False}
        (out / "report.json").write_text(json.dumps(report))
        return report

    monkeypatch.setattr(att.tools, "build_and_grade", fake_build_and_grade)
    monkeypatch.setattr(att, "sweep_containers", lambda stage, owner: [])
    return corpus, cache, built


def test_the_curve_records_id_wall_time_trigger_sha_and_reuse(tmp_path, grading, toy):
    corpus, cache, built = grading
    run = timeline(tmp_path, [(0, "a"), (5, "Automatic checkpoint"), (10, "c")],
                   sha_of=lambda i: ["aa", "aa", "bb"][i])
    curve = att.grade_checkpoints(toy, run, corpus=corpus, cache=cache, dedupe=True)
    assert len(built) == 2, "the duplicate source was rebuilt"
    assert [row["id"] for row in curve] == ["000001", "000002", "final"]
    assert [row["trigger"] for row in curve] == ["tool", "auto", "final"]
    assert [row["elapsed_seconds"] for row in curve] == [0.0, 300.0, 600.0]
    assert [row["source_sha256"] for row in curve] == ["aa", "aa", "bb"]
    assert [row["grade_reused"] for row in curve] == [False, True, False]
    assert curve[1]["reused_from"] == "000001"
    assert curve[1]["overall"] == curve[0]["overall"]
    assert json.loads((run / "checkpoint-curve.json").read_text()) == curve


def test_skipped_checkpoints_are_listed_in_the_curve_with_a_reason(tmp_path, grading, toy):
    corpus, cache, built = grading
    run = timeline(tmp_path, [(0, "a"), (5, "b"), (10, "c"), (70, "d")])
    curve = att.grade_checkpoints(toy, run, corpus=corpus, cache=cache, every=30)
    assert len(built) == 3
    skipped = [row for row in curve if "skipped" in row]
    assert [row["id"] for row in skipped] == ["000002"]
    assert "bucket" in skipped[0]["skipped"] and "overall" not in skipped[0]
    assert len(curve) == 4


def test_a_checkpoint_without_an_exported_source_is_an_error_row(tmp_path, grading, toy):
    corpus, cache, _ = grading
    run = timeline(tmp_path, [(0, "a"), (5, "b")])
    shutil.rmtree(run / "checkpoints" / "000001" / "source")
    curve = att.grade_checkpoints(toy, run, corpus=corpus, cache=cache)
    assert curve[0]["error"] == "checkpoint has no exported source"


def test_an_existing_report_is_reused_instead_of_rebuilt(tmp_path, grading, toy):
    corpus, cache, built = grading
    run = timeline(tmp_path, [(0, "a"), (5, "b")])
    out = run / "checkpoints" / "000001" / "grade"
    out.mkdir()
    (out / "report.json").write_text(json.dumps({"aggregate": {"overall": 0.5, "full_success": False}, "build_failure": False}))
    curve = att.grade_checkpoints(toy, run, corpus=corpus, cache=cache)
    assert built == ["checkpoint-000002"] and curve[0]["overall"] == 0.5


def test_dry_run_grades_nothing_and_writes_nothing(tmp_path, grading, toy):
    corpus, cache, built = grading
    run = timeline(tmp_path, [(0, "a"), (5, "b"), (10, "c"), (70, "d")], sha_of=lambda i: ["aa", "bb", "bb", "bb"][i])
    before = sorted(p.name for p in run.iterdir())
    curve = att.grade_checkpoints(toy, run, corpus=corpus, cache=cache, every=30, dedupe=True, dry_run=True)
    assert built == []
    assert not (run / "checkpoint-curve.json").exists()
    assert sorted(p.name for p in run.iterdir()) == before
    assert [row.get("would_grade") for row in curve] == [True, None, True, False]
    assert [row["id"] for row in curve if "skipped" in row] == ["000002"]


def test_dry_run_needs_no_agent_config_and_no_grading_pair(tmp_path):
    run = timeline(tmp_path, [(0, "a"), (5, "b")])
    curve = att.grade_checkpoints(None, run, dry_run=True)
    assert [row["id"] for row in curve] == ["000001", "final"]


def test_the_cli_prints_the_selection_and_the_counts(tmp_path, capsys):
    from evalbase.harness import run as run_cli
    run = timeline(tmp_path, [(0, "a"), (5, "b"), (10, "c"), (70, "d")], sha_of=lambda i: ["aa", "bb", "bb", "bb"][i])
    assert run_cli.main(["grade-checkpoints", str(run), "--every", "30", "--dedupe", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "4 checkpoints on disk: 3 curve points (2 to rebuild and grade, 1 reused), 1 skipped" in out
    assert "dry run: nothing was built, graded or written" in out
    assert "grade reused" in out and "would grade" in out
