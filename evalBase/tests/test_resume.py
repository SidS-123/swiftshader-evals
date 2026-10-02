"""Resume preserves identity and the remaining budget; it never re-exports."""
import json

import pytest

from conftest import make_workspace
from evalbase.harness import attempt as att, cli_runner


def prepare(tmp_path, toy, monkeypatch, **manifest_over):
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    monkeypatch.setattr(att, "sweep_containers", lambda stage, owner: [])
    run = tmp_path / "attempt"
    run.mkdir(parents=True)
    workspace, corpus = make_workspace(tmp_path, toy)
    (run / "workspace").symlink_to(workspace)
    options = att.Options(harness="codex", model="exact-model", budget_hours=1.0)
    manifest = att.base_manifest(toy, options, simulated=True, name="attempt")
    manifest.update(status="interrupted", stop_reason="operator_interrupt",
                    solver_seconds=3599.0, segments=[{"segment": 1}], model_final_events=2)
    manifest.update(manifest_over)
    (run / "attempt.json").write_text(json.dumps(manifest))
    return run, options


def test_resume_refuses_a_completed_or_different_attempt(tmp_path, toy, monkeypatch):
    run, options = prepare(tmp_path, toy, monkeypatch, status="complete")
    with pytest.raises(ValueError, match="completed attempts"):
        cli_runner.CLIAttempt(toy, options, run, command_override=["/bin/false"], resume=True).run()
    run2, options2 = prepare(tmp_path / "b", toy, monkeypatch)
    other = att.Options(harness="codex", model="another-model", budget_hours=1.0)
    with pytest.raises(ValueError, match="harness and model"):
        cli_runner.CLIAttempt(toy, other, run2, command_override=["/bin/false"], resume=True).run()


def test_resume_refuses_after_the_source_was_exported(tmp_path, toy, monkeypatch):
    run, options = prepare(tmp_path, toy, monkeypatch)
    (run / "submission").mkdir()
    with pytest.raises(ValueError, match="already exported"):
        cli_runner.CLIAttempt(toy, options, run, command_override=["/bin/false"], resume=True).run()


def test_resume_continues_the_same_budget_and_never_relaunches_past_it(tmp_path, toy, monkeypatch):
    run, options = prepare(tmp_path, toy, monkeypatch)
    calls = []
    monkeypatch.setattr(cli_runner.att, "export_and_grade", lambda *a, **k: calls.append("graded") or a[4])
    monkeypatch.setattr(cli_runner.tools, "cleanup_containers", lambda owner=None: {})
    attempt = cli_runner.CLIAttempt(toy, options, run, command_override=["/bin/false"], resume=True)
    manifest = attempt.run()
    assert manifest["segments"] == [{"segment": 1}]
    assert manifest["stop_reason"] == "budget_wall"
    assert manifest["solver_seconds"] >= 3599.0
    assert manifest["resume_count"] == 1 and manifest["model_final_events"] == 2
    assert calls == ["graded"]
    assert manifest["measurement_valid"] is False
    assert json.loads((run / "audit.json").read_text())["errors"]


def test_an_operator_interrupt_pauses_without_exporting_so_resume_can_continue(tmp_path, toy, monkeypatch):
    run, options = prepare(tmp_path, toy, monkeypatch)
    calls = []
    monkeypatch.setattr(cli_runner.att, "export_and_grade", lambda *a, **k: calls.append("graded") or a[4])
    monkeypatch.setattr(cli_runner.tools, "cleanup_containers", lambda owner=None: {})

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli_runner.CLIAttempt, "_segment", interrupted)
    options = att.Options(harness="codex", model="exact-model", budget_hours=2.0)
    manifest = cli_runner.CLIAttempt(toy, options, run, command_override=["/bin/false"], resume=True).run()
    assert manifest["status"] == "interrupted" and manifest["stop_reason"] == "operator_interrupt"
    assert manifest["solver_seconds"] >= 3599.0
    assert calls == [] and not (run / "submission").exists() and not (run / "grade").exists()
    saved = json.loads((run / "attempt.json").read_text())
    assert saved["status"] == "interrupted" and saved["solver_seconds"] == manifest["solver_seconds"]
    monkeypatch.undo()
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    monkeypatch.setattr(att, "sweep_containers", lambda stage, owner: [])
    monkeypatch.setattr(cli_runner.att, "export_and_grade", lambda *a, **k: calls.append("graded") or a[4])
    monkeypatch.setattr(cli_runner.tools, "cleanup_containers", lambda owner=None: {})
    resumed = cli_runner.CLIAttempt(toy, options, run, command_override=["/bin/false"], resume=True).run()
    assert resumed["resume_count"] == 2 and calls == ["graded"]


def test_resume_detects_a_changed_corpus(tmp_path, toy, monkeypatch):
    run, options = prepare(tmp_path, toy, monkeypatch)
    manifest = json.loads((run / "attempt.json").read_text())
    manifest["task_version"]["corpus"]["sha256"] = "0" * 64
    (run / "attempt.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="corpus changed"):
        cli_runner.CLIAttempt(toy, options, run, command_override=["/bin/false"], resume=True).run()
