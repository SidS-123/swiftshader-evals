"""export_and_grade must persist the manifest to disk at every status change."""
import json
from pathlib import Path

import pytest

from conftest import make_workspace
from evalbase.harness import attempt as att
from evalbase.harness.privacy import Redactor
from evalbase.harness.tools import atomic_json


@pytest.fixture
def grading_setup(tmp_path, monkeypatch, toy):
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    workspace, corpus = make_workspace(tmp_path, toy)
    cache = tmp_path / "cache" / "dev_case"
    cache.mkdir(parents=True)
    (cache / "refcache.json").write_text("{}")

    def fake_build_and_grade(instance, source, out, *, corpus, cache, label, owner=None, sandbox="docker"):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        report = {"aggregate": {"overall": 1.0, "full_success": True, "categories": {}}, "build_failure": False, "cases": []}
        (out / "report.json").write_text(json.dumps(report))
        return report

    monkeypatch.setattr(att.tools, "build_and_grade", fake_build_and_grade)
    monkeypatch.setattr(att, "sweep_containers", lambda stage, owner: [])
    options = att.Options(harness="codex", model="m", corpus_hidden=str(corpus), refcache_hidden=str(tmp_path / "cache"))
    return workspace, options


def test_status_sequence_written_to_disk_is_solver_finished_grading_complete(tmp_path, grading_setup, toy):
    workspace, options = grading_setup
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    redactor = Redactor(root=toy.root)
    manifest = att.base_manifest(toy, options, simulated=True, name="attempt")
    attempt_json = run_dir / "attempt.json"
    disk_statuses = []

    def save(m):
        atomic_json(attempt_json, redactor.clean(m), mode=0o644)
        disk_statuses.append(json.loads(attempt_json.read_text())["status"])

    manifest["status"] = "solver_finished"
    save(manifest)
    result = att.export_and_grade(toy, run_dir, workspace, options, manifest, redactor, save=save)
    assert disk_statuses == ["solver_finished", "grading", "complete"]
    assert result["status"] == "complete" and result["grade"]["overall"] == 1.0
    assert json.loads(attempt_json.read_text())["grade"]["overall"] == 1.0
    assert json.loads(attempt_json.read_text())["graded_corpus"] == "hidden"


def test_status_sequence_on_a_build_failure_still_reaches_complete_on_disk(tmp_path, grading_setup, toy):
    _workspace, options = grading_setup
    empty_workspace = tmp_path / "empty_task"
    empty_workspace.mkdir()
    run_dir = tmp_path / "run2"
    run_dir.mkdir()
    redactor = Redactor(root=toy.root)
    manifest = att.base_manifest(toy, options, simulated=True, name="attempt")
    attempt_json = run_dir / "attempt.json"
    disk_statuses = []

    def save(m):
        atomic_json(attempt_json, redactor.clean(m), mode=0o644)
        disk_statuses.append(json.loads(attempt_json.read_text())["status"])

    manifest["status"] = "solver_finished"
    save(manifest)
    result = att.export_and_grade(toy, run_dir, empty_workspace, options, manifest, redactor, save=save)
    assert disk_statuses == ["solver_finished", "complete"]
    assert result["status"] == "complete" and result["grade"]["build_failure"] is True
