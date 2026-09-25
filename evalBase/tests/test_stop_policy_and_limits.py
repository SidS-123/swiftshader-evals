"""The `submit` stop policy, provider rate-limit handling, and uid cleanup.

Nothing here starts a container or contacts a provider.
"""
import json
import os
import subprocess

import pytest

from evalbase.harness import attempt as att
from evalbase.harness import cli_runner, tools, workspace as ws
from evalbase.harness.audit import audit_events, rate_limit_signal
from evalbase.harness.cli_runner import SUBMIT_MARKER, CLIAttempt, _declares_final, _final_text


def test_stop_policy_defaults_to_budget_and_only_accepts_the_two_names():
    assert att.Options(harness="claude-code", model="m").stop_policy == "budget"
    att.Options(harness="claude-code", model="m", stop_policy="submit").validate()
    with pytest.raises(ValueError, match="--stop-policy"):
        att.Options(harness="claude-code", model="m", stop_policy="whenever").validate()
    with pytest.raises(ValueError, match="CLI harnesses only"):
        att.Options(harness="openrouter", model="m", stop_policy="submit").validate()
    with pytest.raises(ValueError, match="--rate-limit-wait"):
        att.Options(harness="codex", model="m", rate_limit_wait_hours=-1).validate()
    with pytest.raises(ValueError, match="--sandbox"):
        att.Options(harness="codex", model="m", sandbox="vm").validate()


def test_the_policy_and_the_wall_caps_are_recorded_in_the_manifest(toy, monkeypatch):
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    monkeypatch.setattr(att, "sweep_containers", lambda stage, owner: [])
    options = att.Options(harness="claude-code", model="m", budget_hours=12, rate_limit_wait_hours=6).validate()
    manifest = att.base_manifest(toy, options, simulated=True, name="t")
    assert manifest["budget"]["rate_limit_wait_seconds"] == 6 * 3600
    assert manifest["budget"]["total_wall_cap_seconds"] == 18 * 3600
    assert manifest["rate_limit_wait_seconds"] == 0.0 and manifest["sandbox"] == "docker"
    submit = att.base_manifest(toy, att.Options(harness="claude-code", model="m", stop_policy="submit").validate(),
                               simulated=True, name="t")
    assert submit["stop_policy"] == "submit"
    assert submit["submission"] == {"confirmations": 0, "declared_final": False, "declared_at_segment": None}


def test_only_the_exact_marker_counts_as_a_submission():
    assert _declares_final(SUBMIT_MARKER)
    assert _declares_final(f"**{SUBMIT_MARKER}**")
    assert _declares_final(f"Everything builds and grades.\n{SUBMIT_MARKER}\n")
    assert not _declares_final(f"I could reply {SUBMIT_MARKER} but there is more to do")
    assert not _declares_final("done for now") and not _declares_final("")


def test_the_final_text_comes_from_the_cli_result_event():
    stream = "\n".join([
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hm"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": SUBMIT_MARKER}),
    ])
    assert _final_text(stream, "claude-code") == SUBMIT_MARKER
    codex = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": SUBMIT_MARKER}})
    assert _final_text(codex, "codex") == SUBMIT_MARKER


def _attempt(tmp_path, toy, **kw):
    options = att.Options(harness="claude-code", model="m", **kw).validate()
    return CLIAttempt(toy, options, tmp_path / "run")


def test_each_prompt_kind_says_what_the_model_may_do(tmp_path, toy):
    submit = _attempt(tmp_path, toy, stop_policy="submit")
    initial = submit._prompt(600, 1, "initial")
    assert "Stopping policy: submit" in initial and SUBMIT_MARKER in initial
    assert toy.task.instructions.strip()[:40] in initial
    confirm = submit._prompt(600, 2, "confirmation")
    assert SUBMIT_MARKER in confirm and "keep working" in confirm
    assert "neither calls a tool nor states that line ends the attempt" in confirm
    resumed = submit._prompt(600, 3, "rate_limit_resume")
    assert "usage limit" in resumed and "did not consume your time budget" in resumed
    budget = _attempt(tmp_path, toy, stop_policy="budget")._prompt(600, 1, "initial")
    assert "Stopping policy: budget" in budget and SUBMIT_MARKER not in budget


SEGMENT_SECONDS = 600.0


class _Scripted:
    def __init__(self, records, clock):
        self.records = list(records)
        self.clock = clock
        self.kinds = []

    def __call__(self, segment, remaining, info, manifest, raw, errlog, started, *, kind="continuation"):
        self.kinds.append(kind)
        self.clock["t"] += SEGMENT_SECONDS
        record = self.records.pop(0) if self.records else {"exit_code": 0, "tool_calls": 0, "submission_final": False}
        return {"segment": segment, "seconds": SEGMENT_SECONDS, "prompt_kind": kind,
                "stop_requested": False, "rate_limit": None, "final_text": None, **record}


def _run_scripted(tmp_path, monkeypatch, toy, records, **kw):
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    clock = {"t": 0.0}
    scripted = _Scripted(records, clock)
    monkeypatch.setattr(CLIAttempt, "_segment", scripted, raising=True)
    monkeypatch.setattr(CLIAttempt, "_elapsed", lambda self, started: clock["t"])
    monkeypatch.setattr(cli_runner, "preflight", lambda h, model=None: {"executable": "/bin/true", "version": "x", "authentication": "y"})
    monkeypatch.setattr(cli_runner.ws, "populate", lambda *a, **k: None)
    monkeypatch.setattr(cli_runner.ws, "checkpoint", lambda *a, **k: (_ for _ in ()).throw(ValueError("no workspace")))
    monkeypatch.setattr(cli_runner.tools, "cleanup_containers", lambda owner=None: {})
    monkeypatch.setattr(cli_runner, "audit_events", lambda *a, **k: {
        "valid_tool_boundary": True, "violations": [], "boundary_evidence": [], "errors": [],
        "events": 1, "mcp_tool_calls": 3, "rate_limit_events": [], "usage": None,
        "usage_entries": [], "usage_totals": {}, "cost": {}, "usage_source": "test", "served_models": None})
    monkeypatch.setattr(cli_runner.att, "export_and_grade", lambda *a, **k: a[4].update(status="complete"))
    monkeypatch.setattr(cli_runner.att, "sweep_containers", lambda stage, owner: [])
    options = att.Options(harness="claude-code", model="m", budget_hours=1.0, **kw).validate()
    return CLIAttempt(toy, options, tmp_path / "run").run(), scripted


def test_submit_confirms_once_and_stops_on_the_explicit_marker(tmp_path, monkeypatch, toy):
    manifest, scripted = _run_scripted(tmp_path, monkeypatch, toy, [
        {"exit_code": 0, "tool_calls": 12, "submission_final": False},
        {"exit_code": 0, "tool_calls": 0, "submission_final": True},
    ], stop_policy="submit")
    assert scripted.kinds == ["initial", "confirmation"]
    assert manifest["stop_reason"] == "model_final"
    assert manifest["submission"]["declared_final"] is True
    assert manifest["submission"]["confirmations"] == 1 and manifest["submission"]["declared_at_segment"] == 2


def test_submit_also_stops_on_a_second_exit_with_no_tool_call(tmp_path, monkeypatch, toy):
    manifest, scripted = _run_scripted(tmp_path, monkeypatch, toy, [
        {"exit_code": 0, "tool_calls": 4, "submission_final": False},
        {"exit_code": 0, "tool_calls": 0, "submission_final": False},
    ], stop_policy="submit")
    assert scripted.kinds == ["initial", "confirmation"]
    assert manifest["stop_reason"] == "model_final"
    assert manifest["submission"]["declared_final"] is False
    assert "no tool call" in manifest["submission"]["ended_on"]


def test_a_model_that_goes_back_to_work_is_confirmed_again(tmp_path, monkeypatch, toy):
    manifest, scripted = _run_scripted(tmp_path, monkeypatch, toy, [
        {"exit_code": 0, "tool_calls": 4, "submission_final": False},
        {"exit_code": 0, "tool_calls": 7, "submission_final": False},
        {"exit_code": 0, "tool_calls": 0, "submission_final": True},
    ], stop_policy="submit")
    assert scripted.kinds == ["initial", "confirmation", "confirmation"]
    assert manifest["submission"]["confirmations"] == 2 and manifest["stop_reason"] == "model_final"


def test_budget_policy_is_unchanged_and_never_confirms(tmp_path, monkeypatch, toy):
    manifest, scripted = _run_scripted(tmp_path, monkeypatch, toy, [
        {"exit_code": 0, "tool_calls": 4, "submission_final": True},
        {"exit_code": 0, "tool_calls": 0, "submission_final": True},
    ], stop_policy="budget")
    assert set(scripted.kinds[1:]) <= {"continuation"}
    assert manifest["stop_reason"] == "budget_wall" and manifest["model_final_events"] >= 2


REJECTED = json.dumps({"type": "rate_limit_event", "uuid": "u", "session_id": "s",
                       "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour",
                                           "resetsAt": 1900000000, "utilization": 1.02}})
ALLOWED = json.dumps({"type": "rate_limit_event", "uuid": "u", "session_id": "s",
                      "rate_limit_info": {"status": "allowed_warning", "utilization": 0.8}})
HTTP_429 = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                       "api_error_status": 429, "num_turns": 3, "stop_reason": None,
                       "errors": ["API Error: 429 {\"type\":\"rate_limit_error\"}"]})


def test_the_two_rate_limit_signatures_are_recognised():
    hit = rate_limit_signal(REJECTED)
    assert hit["signal"] == "rate_limit_event" and hit["kind"] == "five_hour" and hit["resets_at"] == 1900000000
    http = rate_limit_signal(HTTP_429)
    assert http["signal"] == "result_api_error" and http["kind"] == "http_429"
    assert rate_limit_signal("", "ERROR: usage limit reached, resets_at 1900000000")["resets_at"] == 1900000000


LIVE_ALLOWED = json.dumps({"type": "rate_limit_event", "uuid": "u", "session_id": "s",
                           "rate_limit_info": {"status": "allowed", "resetsAt": 1789622400,
                                               "rateLimitType": "five_hour", "overageStatus": "rejected"}})


def test_ordinary_output_is_not_mistaken_for_a_rate_limit():
    assert rate_limit_signal(ALLOWED) is None and rate_limit_signal(LIVE_ALLOWED) is None
    assert rate_limit_signal(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                         "result": "I added a rate limiter to the scheduler."})) is None
    assert rate_limit_signal("not json at all\n") is None and rate_limit_signal("") is None


def test_a_rate_limited_result_is_a_pause_not_a_boundary_violation(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(REJECTED + "\n" + HTTP_429 + "\n")
    (tmp_path / "stderr.log").write_text("")
    result = audit_events(events, "claude-code", tmp_path / "stderr.log", server="toy")
    assert result["valid_tool_boundary"], result["errors"]
    assert len(result["rate_limit_events"]) == 2


def test_the_wait_is_capped_recorded_and_never_charged_to_the_solver(tmp_path, monkeypatch, toy):
    slept = []
    monkeypatch.setattr(cli_runner.time, "sleep", lambda s: slept.append(s))
    attempt = _attempt(tmp_path, toy, stop_policy="submit", rate_limit_wait_hours=0.05)
    manifest = {"rate_limit_wait_seconds": 0.0, "rate_limit_waits": [], "segments": []}
    monkeypatch.setattr(attempt, "_save", lambda m: None)
    assert attempt._wait_out_rate_limit({"signal": "rate_limit_event", "kind": "five_hour", "resets_at": None}, manifest, 0) is True
    assert manifest["rate_limit_waits"][0]["planned_seconds"] <= 180 and sum(slept) > 0
    manifest["rate_limit_wait_seconds"] = 180.0
    assert attempt._wait_out_rate_limit({"signal": "stderr", "resets_at": None}, manifest, 1) is False


def test_a_rate_limited_segment_relaunches_with_the_resume_prompt(tmp_path, monkeypatch, toy):
    monkeypatch.setattr(CLIAttempt, "_wait_out_rate_limit", lambda self, hit, m, p: True)
    manifest, scripted = _run_scripted(tmp_path, monkeypatch, toy, [
        {"exit_code": 1, "tool_calls": 0, "submission_final": False,
         "rate_limit": {"signal": "rate_limit_event", "kind": "five_hour", "resets_at": None}},
        {"exit_code": 0, "tool_calls": 6, "submission_final": False},
        {"exit_code": 0, "tool_calls": 0, "submission_final": True},
    ], stop_policy="submit")
    assert scripted.kinds == ["initial", "rate_limit_resume", "confirmation"]
    assert manifest["stop_reason"] == "model_final" and manifest.get("error") is None


def test_an_unclearable_rate_limit_ends_the_attempt_as_rate_limited(tmp_path, monkeypatch, toy):
    monkeypatch.setattr(CLIAttempt, "_wait_out_rate_limit", lambda self, hit, m, p: False)
    manifest, _ = _run_scripted(tmp_path, monkeypatch, toy, [
        {"exit_code": 1, "tool_calls": 0, "submission_final": False,
         "rate_limit": {"signal": "stderr", "kind": None, "resets_at": None}},
    ], stop_policy="submit")
    assert manifest["stop_reason"] == "rate_limited" and manifest["measurement_valid"] is False
    assert "rate-limit-wait" in manifest["error"]


def test_a_long_tool_call_heartbeat_is_passive_but_still_names_its_tool(tmp_path):
    events = tmp_path / "events.jsonl"
    (tmp_path / "stderr.log").write_text("")
    beat = {"type": "tool_progress", "tool_name": "mcp__toy__grade_dev", "tool_use_id": "t", "elapsed_time_seconds": 30}
    events.write_text(json.dumps(beat) + "\n" + json.dumps(
        {"type": "result", "subtype": "success", "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 1}}) + "\n")
    assert audit_events(events, "claude-code", tmp_path / "stderr.log", server="toy")["valid_tool_boundary"]
    events.write_text(json.dumps({**beat, "tool_name": "Bash"}) + "\n")
    bad = audit_events(events, "claude-code", tmp_path / "stderr.log", server="toy")
    assert not bad["valid_tool_boundary"] and bad["violations"] == ["unexpected Claude tool Bash"]


def test_a_subscription_cost_is_reported_as_an_estimate_not_a_charge(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "total_cost_usd": 0.0169057, "usage": {"input_tokens": 10, "output_tokens": 39},
        "modelUsage": {"m": {"costUSD": 0.0169057, "costBasis": "list"}}}) + "\n")
    (tmp_path / "stderr.log").write_text("")
    result = audit_events(events, "claude-code", tmp_path / "stderr.log", server="toy")
    assert result["cost"]["reported"] and result["cost"]["total_usd"] == 0.016906 and result["cost"]["basis"] == "list"
    assert result["cost"]["is_billed_charge"] is False and "nobody was billed" in result["cost"]["note"]
    assert result["usage_totals"]["input_tokens"] == 10 and result["usage_totals"]["output_tokens"] == 39


def test_no_cost_is_invented_when_the_cli_reports_none(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                  "usage": {"input_tokens": 3, "output_tokens": 4}}) + "\n")
    (tmp_path / "stderr.log").write_text("")
    cost = audit_events(events, "claude-code", tmp_path / "stderr.log", server="toy")["cost"]
    assert cost["reported"] is False and cost["total_usd"] is None and cost["is_billed_charge"] is False


def test_remove_tree_falls_back_to_a_container_when_the_host_cannot_unlink(tmp_path, monkeypatch):
    monkeypatch.delenv("EVALBASE_NO_DOCKER", raising=False)
    target = tmp_path / "task"
    (target / "build").mkdir(parents=True)
    (target / "build" / "artifact").write_bytes(b"x")
    calls = []
    real_rmtree = ws.shutil.rmtree

    def refuse(path):
        raise PermissionError(13, "Permission denied", "artifact")

    def fake_run(argv, **kw):
        calls.append(argv)
        real_rmtree(target)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ws.shutil, "rmtree", refuse)
    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    ws.remove_tree(target, image="img:1")
    assert calls and calls[0][:2] == ["docker", "run"] and "img:1" in calls[0]
    assert not target.exists()
    (target / "build").mkdir(parents=True)
    with pytest.raises(PermissionError):
        ws.remove_tree(target, allow_container=False)


def test_the_grade_build_container_runs_as_the_host_user():
    assert tools.BUILD_USER == f"{os.getuid()}:{os.getgid()}"
    source = (ws.Path(tools.__file__)).read_text()
    assert source.count('"--user", BUILD_USER') == 1
    assert '"--user", "1000:1000",' in source
