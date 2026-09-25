"""The operator CLI: dispatch, budgets, grading targets, no silent substitution."""
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evalbase.harness import attempt as att, run as run_cli

ROOT = Path(__file__).resolve().parents[1]


def test_default_command_is_attempt_and_harness_is_required(monkeypatch):
    seen = {}
    monkeypatch.setattr(run_cli, "cmd_attempt", lambda a: seen.update(vars(a)))
    assert run_cli.main(["--harness", "openrouter", "--model", "z/y", "--out", "/tmp/x",
                         "--budget-hours", "2", "--max-cost-usd", "3"]) == 0
    assert seen["harness"] == "openrouter" and seen["model"] == "z/y"
    assert seen["budget_hours"] == 2.0 and seen["max_cost_usd"] == 3.0
    assert run_cli.main(["--instance", "x", "--harness", "codex", "--model", "m", "--out", "/tmp/x"]) == 0
    assert seen["instance"] == "x" and seen["sandbox"] == "docker"
    with pytest.raises(SystemExit):
        run_cli.main(["--model", "z/y", "--out", "/tmp/x"])
    with pytest.raises(SystemExit):
        run_cli.main(["--harness", "aider", "--model", "z", "--out", "/tmp/x"])


def test_subcommands_are_recognised(monkeypatch):
    calls = []
    for name in ("cmd_resume", "cmd_grade_checkpoints", "cmd_smoke", "cmd_report", "cmd_cleanup", "cmd_versions"):
        monkeypatch.setattr(run_cli, name, lambda a, n=name: calls.append(n))
    run_cli.main(["resume", "runs/attempts/a"])
    run_cli.main(["grade-checkpoints", "runs/attempts/a"])
    run_cli.main(["smoke", "--harness", "codex"])
    run_cli.main(["report", "runs/attempts/a"])
    run_cli.main(["cleanup"])
    run_cli.main(["versions"])
    assert calls == ["cmd_resume", "cmd_grade_checkpoints", "cmd_smoke", "cmd_report", "cmd_cleanup", "cmd_versions"]


def test_cost_caps_are_refused_for_cli_harnesses(monkeypatch, capsys, toy):
    monkeypatch.setenv("EVALBASE_INSTANCE", str(toy.root))
    assert run_cli.main(["--harness", "codex", "--model", "m", "--out", "/tmp/x", "--max-cost-usd", "5"]) == 2
    assert "openrouter harness" in capsys.readouterr().err


def test_module_help_runs_without_side_effects():
    result = subprocess.run([sys.executable, "-m", "evalbase.harness.run", "--help"],
                            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert result.returncode == 0
    for word in ("--instance", "resume", "grade-checkpoints", "smoke"):
        assert word in result.stdout
    grader = subprocess.run([sys.executable, "-m", "evalbase.grader.cli", "--help"],
                            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert grader.returncode == 0 and "refcache" in grader.stdout


def _fake_artifacts(tmp_path, *, hidden: bool):
    for name, replay in (("public", "a_pub"), ("hidden", "a_hid")):
        if name == "hidden" and not hidden:
            (tmp_path / "corpus" / "hidden").mkdir(parents=True)
            continue
        (tmp_path / "corpus" / name).mkdir(parents=True)
        (tmp_path / "corpus" / name / f"{replay}.json").write_text("{}")
        ledger = tmp_path / "runs" / ("refcache" if name == "public" else "refcache-hidden") / replay
        ledger.mkdir(parents=True)
        (ledger / "refcache.json").write_text("{}")
    return tmp_path


def _instance_at(toy, tmp_path):
    corpus = dataclasses.replace(toy.corpus, public_dir=tmp_path / "corpus" / "public",
                                 hidden_dir=tmp_path / "corpus" / "hidden")
    return dataclasses.replace(toy, corpus=corpus, runs_dir=tmp_path / "runs")


def test_hidden_corpus_is_graded_against_the_hidden_refcache(tmp_path, toy, monkeypatch):
    for var in ("EVALBASE_REFCACHE", "EVALBASE_REFCACHE_HIDDEN", "EVALBASE_CORPUS_PUBLIC", "EVALBASE_CORPUS_HIDDEN"):
        monkeypatch.delenv(var, raising=False)
    _fake_artifacts(tmp_path, hidden=True)
    corpus, refcache, label = att.grading_target(_instance_at(toy, tmp_path), att.Options(harness="codex", model="m"))
    assert label == "hidden"
    assert corpus == tmp_path / "corpus" / "hidden"
    assert refcache == tmp_path / "runs" / "refcache-hidden"


def test_public_fallback_is_graded_against_the_public_refcache(tmp_path, toy, monkeypatch):
    for var in ("EVALBASE_REFCACHE", "EVALBASE_REFCACHE_HIDDEN", "EVALBASE_CORPUS_PUBLIC", "EVALBASE_CORPUS_HIDDEN"):
        monkeypatch.delenv(var, raising=False)
    _fake_artifacts(tmp_path, hidden=False)
    corpus, refcache, label = att.grading_target(_instance_at(toy, tmp_path), att.Options(harness="codex", model="m"))
    assert label == "public (no hidden corpus on this host)"
    assert corpus == tmp_path / "corpus" / "public"
    assert refcache == tmp_path / "runs" / "refcache"


def test_overrides_and_a_mismatched_refcache_fail_before_the_rebuild(tmp_path, toy, monkeypatch):
    for var in ("EVALBASE_REFCACHE", "EVALBASE_REFCACHE_HIDDEN", "EVALBASE_CORPUS_PUBLIC", "EVALBASE_CORPUS_HIDDEN"):
        monkeypatch.delenv(var, raising=False)
    _fake_artifacts(tmp_path, hidden=True)
    instance = _instance_at(toy, tmp_path)
    options = att.Options(harness="codex", model="m", corpus_hidden=str(tmp_path / "corpus" / "hidden"),
                          refcache_hidden=str(tmp_path / "runs" / "refcache-hidden"))
    assert att.grading_target(instance, options).refcache == tmp_path / "runs" / "refcache-hidden"
    options.refcache_hidden = str(tmp_path / "runs" / "refcache")
    with pytest.raises(ValueError, match="no entry for case a_hid"):
        att.grading_target(instance, options)
    options.refcache_hidden = None
    options.corpus_hidden = str(tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no cases"):
        att.grading_target(instance, options)


def test_hidden_refcache_default_follows_the_public_override(monkeypatch, toy):
    monkeypatch.setenv("EVALBASE_REFCACHE", "/abs/elsewhere/runs/refcache")
    monkeypatch.delenv("EVALBASE_REFCACHE_HIDDEN", raising=False)
    assert str(toy.refcache_hidden) == "/abs/elsewhere/runs/refcache-hidden"


def test_the_grader_cli_needs_an_instance_for_grading_but_not_for_a_sweep(monkeypatch):
    from evalbase.grader import cli, containers
    monkeypatch.delenv("EVALBASE_INSTANCE", raising=False)
    monkeypatch.setattr(containers, "kill_stale_containers", lambda *a, **k: [])
    assert cli.main(["sweep"]) == []
    with pytest.raises(ValueError, match="EVALBASE_INSTANCE"):
        cli.main(["grade-ref"])
