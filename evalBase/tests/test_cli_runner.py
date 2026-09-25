"""Launch recipes, the event audit, and attempt options.

The MCP server name is the instance's (`toy`).
"""
import json

import pytest

from evalbase.harness import attempt as att
from evalbase.harness.audit import audit_events
from evalbase.harness.cli_runner import launch_command
from evalbase.harness.codex_boundary import restricted_catalog


def options(harness="codex", **kw):
    return att.Options(harness=harness, model=kw.pop("model", "exact-model"), **kw)


# ------------------------------------------------------------------- codex

def test_codex_launch_removes_host_affordances(tmp_path, toy):
    cmd = launch_command(toy, options("codex"), tmp_path, tmp_path, "codex")
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "--json"):
        assert flag in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("-m") + 1] == "exact-model"
    for value in ("features.shell_tool=false", "features.apps=false", "features.plugins=false",
                  "features.multi_agent=false", "features.skip_host_skill_discovery=true",
                  'web_search="disabled"', "tools.update_plan.enabled=false",
                  "tools.experimental_request_user_input.enabled=false",
                  "approval_policy=\"never\"", "project_doc_max_bytes=0"):
        assert value in cmd, value
    assert 'mcp_servers.toy.enabled_tools=["shell", "oracle", "driver", "grade_dev", "checkpoint"]' in cmd
    assert "mcp_servers.toy.required=true" in cmd
    assert not any("API_KEY" in value or "sk-" in value for value in cmd)


def test_codex_reasoning_is_passed_through_or_omitted(tmp_path, toy):
    cmd = launch_command(toy, options("codex", reasoning="xhigh"), tmp_path, tmp_path, "codex")
    assert 'model_reasoning_effort="xhigh"' in cmd
    plain = launch_command(toy, options("codex"), tmp_path, tmp_path, "codex")
    assert not any("model_reasoning_effort" in v for v in plain)


def test_codex_catalog_restriction_preserves_the_model():
    import copy
    entry = {"slug": "exact-model", "apply_patch_tool_type": "freeform",
             "experimental_supported_tools": ["clock"], "multi_agent_version": "v2",
             "context_window": 100000, "default_reasoning_level": "high"}
    source = {"models": [entry]}
    before = copy.deepcopy(source)
    catalog, record = restricted_catalog(source, "exact-model")
    assert source == before
    assert catalog["models"][0] == entry | {"apply_patch_tool_type": None, "experimental_supported_tools": [],
                                            "multi_agent_version": None}
    assert record["original_model_sha256"] != record["restricted_model_sha256"]
    with pytest.raises(RuntimeError, match="exact requested model"):
        restricted_catalog(source, "another-model")


# -------------------------------------------------------------- claude code

def test_claude_launch_exposes_only_the_instance_mcp(tmp_path, toy):
    cmd = launch_command(toy, options("claude-code", reasoning="high"), tmp_path, tmp_path, "claude")
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__toy__*"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert "--strict-mcp-config" in cmd and "--restricted" in cmd
    assert "--no-session-persistence" in cmd and "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    config = json.loads((tmp_path / "claude-mcp.private.json").read_text())
    assert set(config["mcpServers"]) == {"toy"}
    assert config["mcpServers"]["toy"]["args"][0].endswith("harness/agent_mcp.py")
    assert (tmp_path / "claude-mcp.private.json").stat().st_mode & 0o777 == 0o600
    assert not any("API_KEY" in value for value in cmd)
    assert cmd[cmd.index("--system-prompt") + 1] == toy.task.instructions


def test_the_cli_never_sees_the_evaluation_repository(tmp_path, toy):
    control = tmp_path / "empty"
    control.mkdir()
    cmd = launch_command(toy, options("claude-code"), tmp_path, control, "claude")
    assert not list(control.iterdir())
    system_prompt = cmd[cmd.index("--system-prompt") + 1]
    assert "/task" in system_prompt and "refcache" not in system_prompt
    codex = launch_command(toy, options("codex"), tmp_path, control, "codex")
    assert codex[codex.index("-C") + 1] == str(control)


# ------------------------------------------------------------------- audit

def streams(tmp_path, items, stderr="", harness="codex"):
    events = tmp_path / "events.jsonl"
    diagnostics = tmp_path / "stderr.log"
    if harness == "codex":
        events.write_text("".join(json.dumps({"type": "item.completed", "item": i}) + "\n" for i in items))
    else:
        events.write_text("".join(json.dumps(i) + "\n" for i in items))
    diagnostics.write_text(stderr)
    return events, diagnostics


def test_audit_accepts_a_clean_mcp_only_codex_run(tmp_path):
    events, err = streams(tmp_path, [{"type": "mcp_tool_call", "server": "toy", "tool": "shell"},
                                     {"type": "mcp_tool_call", "server": "toy", "tool": "grade_dev"},
                                     {"type": "agent_message", "text": "done"}])
    result = audit_events(events, "codex", err, server="toy")
    assert result["valid_tool_boundary"] and result["mcp_tool_calls"] == 2 and not result["errors"]


def test_audit_rejects_native_host_tools_and_foreign_servers(tmp_path):
    events, err = streams(tmp_path, [
        {"type": "mcp_tool_call", "server": "toy", "tool": "shell"},
        {"type": "command_execution", "command": "cat /etc/passwd"},
        {"type": "file_change", "path": "/task/src/x.cpp"},
        {"type": "mcp_tool_call", "server": "foreign", "tool": "read"}])
    result = audit_events(events, "codex", err, server="toy")
    assert not result["valid_tool_boundary"]
    assert set(result["violations"]) == {"command_execution", "file_change", "unexpected MCP tool"}


def test_the_server_name_is_not_the_default(tmp_path):
    """An audit run for the wrong server rejects the run: the name is load-bearing."""
    events, err = streams(tmp_path, [{"type": "mcp_tool_call", "server": "toy", "tool": "shell"}])
    assert not audit_events(events, "codex", err)["valid_tool_boundary"]
    assert audit_events(events, "codex", err, server="toy")["valid_tool_boundary"]


def test_audit_rejects_a_denied_patch_seen_only_on_stderr(tmp_path):
    events, err = streams(tmp_path, [{"type": "mcp_tool_call", "server": "toy", "tool": "shell"}],
                          stderr="2026-09-15T16:37:58Z ERROR codex_core::tools::router: error=apply_patch verification failed\n")
    result = audit_events(events, "codex", err, server="toy")
    assert result["violations"] == ["native_apply_patch_attempt"]
    assert result["boundary_evidence"][0]["stream"] == "stderr"


def test_audit_does_not_flag_candidate_output_that_mentions_patches(tmp_path):
    events, err = streams(tmp_path, [
        {"type": "mcp_tool_call", "server": "toy", "tool": "shell",
         "result": {"content": [{"type": "text", "text": "error=apply_patch in a log file"}]}},
        {"type": "agent_message", "text": "a patch failed; I used the shell tool"}],
        stderr="WARNING: ordinary diagnostic\n")
    assert audit_events(events, "codex", err, server="toy")["valid_tool_boundary"]


def test_audit_rejects_unexpected_claude_tools_and_reads_usage(tmp_path):
    events, err = streams(tmp_path, [
        {"type": "assistant", "message": {"model": "claude-x", "content": [{"type": "tool_use", "name": "mcp__toy__shell"}]}},
        {"type": "assistant", "message": {"model": "claude-x", "content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "result", "is_error": False, "usage": {"input_tokens": 5}}], harness="claude")
    result = audit_events(events, "claude-code", err, server="toy")
    assert not result["valid_tool_boundary"]
    assert result["violations"] == ["unexpected Claude tool Bash"]
    assert result["served_models"] == ["claude-x"] and result["usage"] == {"input_tokens": 5}


@pytest.mark.parametrize("bad", ["missing_stderr", "malformed_json", "unknown_item", "native_router_error", "empty"])
def test_audit_fails_closed_on_incomplete_evidence(tmp_path, bad):
    events, err = streams(tmp_path, [{"type": "mcp_tool_call", "server": "toy", "tool": "shell"}])
    if bad == "missing_stderr":
        err.unlink()
    if bad == "malformed_json":
        events.write_text("{truncated\n")
    if bad == "unknown_item":
        events.write_text(json.dumps({"item": {"type": "new_host_tool"}}) + "\n")
    if bad == "native_router_error":
        err.write_text("ERROR codex_core::tools::router: error=unexpected failure\n")
    if bad == "empty":
        events.write_text("")
    result = audit_events(events, "codex", err, server="toy")
    assert not result["valid_tool_boundary"] and result["errors"]


# ----------------------------------------------------------------- options

def test_options_validation():
    att.Options(harness="openrouter", model="x", max_cost_usd=5).validate()
    with pytest.raises(ValueError, match="--harness"):
        att.Options(harness="aider", model="x").validate()
    with pytest.raises(ValueError, match="model"):
        att.Options(harness="codex", model="  ").validate()
    with pytest.raises(ValueError, match="reasoning"):
        att.Options(harness="codex", model="x", reasoning="ultra").validate()
    with pytest.raises(ValueError, match="openrouter harness"):
        att.Options(harness="codex", model="x", max_cost_usd=5).validate()
    with pytest.raises(ValueError, match="budget-hours"):
        att.Options(harness="codex", model="x", budget_hours=0).validate()


def test_reasoning_is_recorded_as_requested(toy, monkeypatch):
    monkeypatch.setenv("EVALBASE_NO_DOCKER", "1")
    monkeypatch.setattr(att, "sweep_containers", lambda stage, owner: [])
    manifest = att.base_manifest(toy, options("openrouter", reasoning="medium"), simulated=True, name="t")
    assert manifest["reasoning_requested"] == "medium" and manifest["reasoning_served"] is None
    assert manifest["stop_policy"] == "budget"
    assert manifest["task_version"]["corpus"]["sha256"]
    assert "events.private.jsonl" in manifest["private_files"]


def test_harness_and_model_are_never_substituted(tmp_path, toy):
    cmd = launch_command(toy, options("codex", model="gpt-5.6-sol"), tmp_path, tmp_path, "codex")
    assert cmd[cmd.index("-m") + 1] == "gpt-5.6-sol"
    claude = launch_command(toy, options("claude-code", model="claude-opus-5"), tmp_path, tmp_path, "claude")
    assert claude[claude.index("--model") + 1] == "claude-opus-5"
