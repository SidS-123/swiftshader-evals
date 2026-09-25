"""The opencode harness: launch recipe, tool boundary, event audit, credentials.

Everything
here is pure: no OpenCode binary, no provider, no key.
"""
import json

import pytest

from evalbase.harness import attempt as att
from evalbase.harness import opencode_boundary as ocb
from evalbase.harness.audit import audit_events, auth_failure_signal, rate_limit_signal
from evalbase.harness.cli_runner import launch_command, launch_env, opencode_config_path, opencode_credential
from evalbase.harness.privacy import Redactor

KEY = "sk-or-v1-" + "a1b2c3d4" * 8
MODEL = "openrouter/moonshotai/kimi-k3"


def options(**kw):
    return att.Options(harness="opencode", model=kw.pop("model", MODEL), **kw)


def config_of(tmp_path):
    return json.loads(opencode_config_path(tmp_path).read_text())


def test_launch_uses_the_non_interactive_json_runner(tmp_path, toy):
    control = tmp_path / "control"
    control.mkdir()
    cmd = launch_command(toy, options(), tmp_path, control, "opencode")
    assert cmd[:2] == ["opencode", "run"]
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[cmd.index("--agent") + 1] == "toy"
    assert cmd[cmd.index("--model") + 1] == MODEL
    assert cmd[cmd.index("--dir") + 1] == str(control)
    assert cmd[cmd.index("--title") + 1]
    assert "--pure" in cmd and "--print-logs" in cmd
    assert not any("API_KEY" in value or value.startswith("sk-") for value in cmd)


def test_launch_writes_an_attempt_local_boundary_config(tmp_path, toy):
    launch_command(toy, options(), tmp_path, tmp_path, "opencode")
    path = opencode_config_path(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert "opencode-config.private.json" in att.PRIVATE_FILES
    document = json.loads(path.read_text())
    assert set(document["mcp"]) == {"toy"}
    assert document["mcp"]["toy"]["command"][1].endswith("harness/agent_mcp.py")
    assert document["mcp"]["toy"]["enabled"] is True
    assert document["mcp"]["toy"]["timeout"] >= 600_000
    assert document["plugin"] == [] and document["instructions"] == []
    assert document["share"] == "disabled" and document["autoupdate"] is False
    assert document["small_model"] == MODEL
    assert all(value is False for value in document["tools"].values())
    assert document["agent"]["toy"]["mode"] == "primary"
    assert document["agent"]["toy"]["prompt"] == toy.task.instructions


def test_reasoning_becomes_a_per_model_provider_option(tmp_path, toy):
    launch_command(toy, options(reasoning="high"), tmp_path, tmp_path, "opencode")
    provider = config_of(tmp_path)["provider"]["openrouter"]
    assert provider["models"]["moonshotai/kimi-k3"]["options"]["reasoning"]["effort"] == "high"
    launch_command(toy, options(), tmp_path, tmp_path, "opencode")
    assert "provider" not in config_of(tmp_path)


def test_the_model_id_is_never_substituted(tmp_path, toy):
    cmd = launch_command(toy, options(model="openrouter/z-ai/glm-5"), tmp_path, tmp_path, "opencode")
    assert cmd[cmd.index("--model") + 1] == "openrouter/z-ai/glm-5"
    assert config_of(tmp_path)["model"] == "openrouter/z-ai/glm-5"
    assert ocb.split_model("openrouter/openai/gpt-5") == ("openrouter", "openai/gpt-5")
    with pytest.raises(ValueError, match="provider"):
        ocb.split_model("gpt-5")


def test_opencode_never_sees_the_evaluation_repository(tmp_path, toy):
    control = tmp_path / "empty"
    control.mkdir()
    cmd = launch_command(toy, options(), tmp_path, control, "opencode")
    assert not list(control.iterdir())
    assert cmd[cmd.index("--dir") + 1] == str(control)
    env = launch_env(options(), control, tmp_path)
    assert env["PWD"] == str(control)


def test_launch_env_keeps_the_key_for_opencode_only(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    env = launch_env(options(), tmp_path, tmp_path)
    assert env["OPENROUTER_API_KEY"] == KEY
    assert env["OPENCODE_CONFIG"] == str(opencode_config_path(tmp_path))
    assert env["OPENCODE_CONFIG_DIR"].endswith("opencode-config.d")
    for name in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_AUTH_CONTENT", "OPENCODE_PERMISSION"):
        assert name not in env
    for name, value in ocb.ISOLATION_ENV.items():
        assert env[name] == value
    for harness in ("claude-code", "codex"):
        other = launch_env(att.Options(harness=harness, model="m"), tmp_path, tmp_path)
        assert "OPENROUTER_API_KEY" not in other and "OPENCODE_CONFIG" not in other


def test_an_inherited_inline_override_cannot_replace_the_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"tools":{"bash":true}}')
    monkeypatch.setenv("OPENCODE_PERMISSION", '{"bash":"allow"}')
    env = launch_env(options(), tmp_path, tmp_path)
    assert "OPENCODE_CONFIG_CONTENT" not in env and "OPENCODE_PERMISSION" not in env


def document(**overrides):
    base = ocb.config_document(model=MODEL, server="toy", agent="toy",
                               bridge={"command": "python3", "args": ["agent_mcp.py"], "cwd": "/tmp", "env": {}},
                               instructions="task", tool_timeout=600)
    base.update(overrides)
    return base


def test_check_config_accepts_the_document_the_harness_writes():
    ocb.check_config(document(), server="toy", agent="toy")


@pytest.mark.parametrize("break_it, expected", [
    (lambda d: d["tools"].update(bash=True), "bash"),
    (lambda d: d["tools"].update(webfetch=None), "webfetch"),
    (lambda d: d["permission"].update(external_directory="allow"), "external_directory"),
    (lambda d: d["mcp"].update(other={"type": "local"}), "MCP server"),
    (lambda d: d["mcp"]["toy"].update(enabled=False), "not enabled"),
    (lambda d: d.update(plugin=["evil"]), "plugins"),
    (lambda d: d.update(instructions=["AGENTS.md"]), "instruction files"),
    (lambda d: d.update(share="auto"), "sharing"),
    (lambda d: d.update(autoupdate=True), "auto-update"),
    (lambda d: d["agent"]["toy"].update(mode="subagent"), "primary"),
    (lambda d: d["agent"]["toy"]["tools"].update(task=True), "task"),
])
def test_check_config_rejects_anything_that_leaves_a_host_tool(break_it, expected):
    doc = document()
    break_it(doc)
    with pytest.raises(RuntimeError, match=expected):
        ocb.check_config(doc, server="toy", agent="toy")


def agent_rules(*pairs):
    return [{"permission": name, "pattern": "*", "action": action} for name, action in pairs]


def test_check_agent_reads_the_rule_list_as_last_one_wins():
    rules = agent_rules(("*", "allow"), *[(name, "deny") for name in ocb.DENIED_PERMISSIONS])
    resolved = {"name": "toy", "mode": "primary", "tools": {name: False for name in ocb.OFFERED_BUILTIN_TOOLS},
                "permission": rules}
    ocb.check_agent(resolved, agent="toy")
    assert ocb.effective_action(rules, "bash") == "deny"
    assert ocb.effective_action(agent_rules(("bash", "deny"), ("bash", "allow")), "bash") == "allow"


def test_check_agent_rejects_a_reopened_tool():
    rules = agent_rules(("*", "allow"), *[(name, "deny") for name in ocb.DENIED_PERMISSIONS])
    resolved = {"name": "toy", "mode": "primary", "tools": {name: False for name in ocb.OFFERED_BUILTIN_TOOLS},
                "permission": rules + agent_rules(("bash", "allow"))}
    with pytest.raises(RuntimeError, match="bash"):
        ocb.check_agent(resolved, agent="toy")
    reopened = dict(resolved, permission=rules, tools={"bash": False, "edit": True})
    with pytest.raises(RuntimeError, match="edit"):
        ocb.check_agent(reopened, agent="toy")
    with pytest.raises(RuntimeError, match="unverifiable"):
        ocb.check_agent(dict(resolved, tools={}), agent="toy")
    with pytest.raises(RuntimeError, match="primary"):
        ocb.check_agent(dict(resolved, mode="subagent"), agent="toy")


def test_the_boundary_is_version_stamped():
    assert ocb.BOUNDARY_VERSION == "opencode-mcp-v1"
    assert ocb.QUALIFIED_CLI_VERSION == "1.18.32"


def stream(tmp_path, events, stderr=""):
    log = tmp_path / "events.jsonl"
    err = tmp_path / "stderr.log"
    log.write_text("".join(json.dumps(e) + "\n" for e in events))
    err.write_text(stderr)
    return log, err


def tool_use(name, status="completed"):
    return {"type": "tool_use", "sessionID": "ses_1",
            "part": {"type": "tool", "tool": name, "callID": "c1", "state": {"status": status, "error": "bad argument"}}}


def step_finish(cost=0.25, **tokens):
    counts = {"total": 30, "input": 20, "output": 10, "reasoning": 0, "cache": {"write": 3, "read": 4}}
    counts.update(tokens)
    return {"type": "step_finish", "sessionID": "ses_1",
            "part": {"type": "step-finish", "reason": "stop", "tokens": counts, "cost": cost}}


def test_audit_accepts_a_clean_mcp_only_opencode_run(tmp_path):
    log, err = stream(tmp_path, [
        {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
        tool_use("toy_shell"), tool_use("toy_grade_dev"),
        {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "done"}},
        step_finish()])
    result = audit_events(log, "opencode", err, server="toy")
    assert result["valid_tool_boundary"] and not result["errors"]
    assert result["mcp_tool_calls"] == 2
    assert result["usage_totals"] == {"input_tokens": 20, "output_tokens": 10,
                                      "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
    assert result["cost"]["total_usd"] == 0.25 and result["cost"]["is_billed_charge"] is False


def test_audit_rejects_a_native_opencode_tool(tmp_path):
    log, err = stream(tmp_path, [tool_use("toy_shell"), tool_use("bash"), tool_use("webfetch"), step_finish()])
    result = audit_events(log, "opencode", err, server="toy")
    assert not result["valid_tool_boundary"]
    assert result["violations"] == ["unexpected OpenCode tool bash", "unexpected OpenCode tool webfetch"]


def test_audit_records_a_rejected_tool_argument_without_failing_the_attempt(tmp_path):
    log, err = stream(tmp_path, [tool_use("toy_oracle", status="error"), step_finish()])
    result = audit_events(log, "opencode", err, server="toy")
    assert result["valid_tool_boundary"]
    assert result["tool_errors"] == [{"tool": "toy_oracle", "line": 1, "error": "bad argument"}]


@pytest.mark.parametrize("bad", ["unknown_event", "malformed_json", "missing_stderr", "empty", "malformed_part"])
def test_audit_fails_closed_on_incomplete_evidence(tmp_path, bad):
    log, err = stream(tmp_path, [tool_use("toy_shell"), step_finish()])
    if bad == "unknown_event":
        log.write_text(json.dumps({"type": "shell_out", "sessionID": "s"}) + "\n")
    if bad == "malformed_json":
        log.write_text("{truncated\n")
    if bad == "missing_stderr":
        err.unlink()
    if bad == "empty":
        log.write_text("")
    if bad == "malformed_part":
        log.write_text(json.dumps({"type": "tool_use", "part": "not-an-object"}) + "\n")
    result = audit_events(log, "opencode", err, server="toy")
    assert not result["valid_tool_boundary"] and result["errors"]


def api_error(status, message, reset=None):
    data = {"message": message, "statusCode": status, "isRetryable": status == 429,
            "responseHeaders": {"x-ratelimit-reset": reset} if reset else {}}
    return {"type": "error", "sessionID": "s", "error": {"name": "APIError", "data": data}}


def test_a_429_is_a_pause_with_the_reset_time_in_seconds():
    hit = rate_limit_signal(json.dumps(api_error(429, "Rate limit exceeded", "1790100000000")))
    assert hit["signal"] == "opencode_api_error" and hit["kind"] == "http_429" and hit["resets_at"] == 1790100000
    assert rate_limit_signal(json.dumps(api_error(429, "slow down")))["resets_at"] is None


def test_a_rejected_credential_is_not_a_rate_limit(tmp_path):
    text = json.dumps(api_error(401, "No auth credentials found"))
    assert rate_limit_signal(text) is None
    assert auth_failure_signal(text) == {"status": 401, "evidence": "No auth credentials found"}
    assert auth_failure_signal(json.dumps(api_error(429, "Rate limit exceeded"))) is None
    log, err = stream(tmp_path, [api_error(403, "forbidden")])
    result = audit_events(log, "opencode", err, server="toy")
    assert not result["valid_tool_boundary"] and result["errors"][0]["status"] == 403


def test_a_bogus_key_fails_the_auth_check_without_echoing_it(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "definitely-not-a-key")
    with pytest.raises(RuntimeError) as excinfo:
        opencode_credential("opencode", MODEL)
    assert "sk-or-v1-" in str(excinfo.value) and "definitely-not-a-key" not in str(excinfo.value)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    assert opencode_credential("opencode", MODEL) == "OPENROUTER_API_KEY on the host"


def test_a_missing_credential_names_the_two_places_it_can_live(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake = tmp_path / "opencode-fake"
    fake.write_text("#!/bin/sh\necho 'data " + str(tmp_path) + "'\n")
    fake.chmod(0o755)
    with pytest.raises(RuntimeError, match="no openrouter credential"):
        opencode_credential(str(fake), MODEL)
    (tmp_path / "auth.json").write_text(json.dumps({"openrouter": {"type": "api"}}))
    assert opencode_credential(str(fake), MODEL) == "OpenCode auth store (openrouter)"


def test_the_recorded_launch_and_config_carry_no_secret(monkeypatch, tmp_path, toy):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    control = tmp_path / "control"
    control.mkdir()
    cmd = launch_command(toy, options(reasoning="high"), tmp_path, control, "opencode")
    redactor = Redactor(root=toy.root)
    record = redactor.clean({"argv": cmd, "cwd": str(control), "env_note": f"Bearer {KEY}", "api_key": KEY})
    blob = json.dumps(record)
    assert KEY not in blob and "[REDACTED]" in blob
    assert KEY not in json.dumps(redactor.clean(config_of(tmp_path)))


def test_the_submission_marker_is_read_from_an_opencode_text_event():
    from evalbase.harness.cli_runner import _declares_final, _final_text
    events = "\n".join(json.dumps(e) for e in [
        {"type": "text", "sessionID": "s", "part": {"type": "text", "text": "working"}},
        tool_use("toy_shell"),
        {"type": "text", "sessionID": "s", "part": {"type": "text", "text": "SUBMISSION FINAL"}},
        step_finish()])
    assert _final_text(events, "opencode") == "SUBMISSION FINAL"
    assert _declares_final(_final_text(events, "opencode"))


def test_opencode_is_a_first_class_harness():
    assert "opencode" in att.HARNESSES
    from evalbase.harness.cli_runner import EXECUTABLE
    assert EXECUTABLE["opencode"] == "opencode"
    assert att.Options(harness="opencode", model=MODEL, stop_policy="submit").validate()
    with pytest.raises(ValueError, match="openrouter harness"):
        att.Options(harness="opencode", model=MODEL, max_cost_usd=5).validate()
