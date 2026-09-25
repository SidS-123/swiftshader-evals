"""MCP framing: initialize, tools/list and tools/call over a stdio exchange."""
import io
import json
from pathlib import Path

import pytest

from conftest import FakeSandbox, make_workspace
from evalbase.harness import mcp_server, tools


def session(tmp_path, toy):
    run = tmp_path / "run"
    run.mkdir()
    workspace, corpus = make_workspace(tmp_path, toy)
    (run / "workspace").symlink_to(workspace)
    (run / "agent-config.json").write_text(json.dumps(
        {"max_seconds": 600, "checkpoint_seconds": 900, "corpus_public": str(corpus),
         "instance": str(toy.root / "instance.py")}))
    return mcp_server.CLIToolSession(run, sandbox=FakeSandbox(workspace), instance=toy)


def exchange(sess, requests):
    stdin = io.BytesIO(b"".join(json.dumps(r).encode() + b"\n" for r in requests))
    stdout = io.StringIO()
    mcp_server.serve(sess, stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_initialize_and_tool_listing(tmp_path, toy):
    sess = session(tmp_path, toy)
    replies = exchange(sess, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ])
    assert len(replies) == 3
    assert replies[0]["result"]["serverInfo"]["name"] == toy.task.mcp_server == "toy"
    listed = replies[1]["result"]["tools"]
    assert [t["name"] for t in listed] == list(tools.TOOL_NAMES)
    for entry in listed:
        assert entry["inputSchema"]["type"] == "object" and entry["description"]
    # the descriptions are the instance's own
    assert "candidate.py" in next(t["description"] for t in listed if t["name"] == "driver")


def test_tool_call_runs_and_is_transcribed(tmp_path, toy):
    sess = session(tmp_path, toy)
    replies = exchange(sess, [{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
        "name": "shell", "arguments": {"command": "echo hello-from-mcp", "timeout_seconds": 10}}}])
    payload = json.loads(replies[0]["result"]["content"][0]["text"])
    assert replies[0]["result"]["isError"] is False
    assert payload["exit_code"] == 0 and "hello-from-mcp" in payload["output"]
    state = json.loads((sess.run_dir / "tools-state.json").read_text())
    assert state["manifest"]["calls"] == 1 and state["manifest"]["tool_calls"]["shell"] == 1
    lines = [json.loads(l) for l in (sess.run_dir / "tools-transcript.jsonl").read_text().splitlines()]
    assert [l["event"] for l in lines] == ["tool_call", "tool_result"]


def test_tool_errors_are_reported_not_raised(tmp_path, toy):
    sess = session(tmp_path, toy)
    replies = exchange(sess, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "oracle", "arguments": {"case_path": "/etc/passwd", "outdir": "out"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "shell", "arguments": {"command": "ls", "extra": 1}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "docker", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "sampling/createMessage", "params": {}},
    ])
    assert all(r["result"]["isError"] for r in replies[:3])
    assert "inside /task" in replies[0]["result"]["content"][0]["text"]
    assert "unexpected tool argument" in replies[1]["result"]["content"][0]["text"]
    assert "unknown tool" in replies[2]["result"]["content"][0]["text"]
    assert replies[3]["error"]["code"] == -32601


def test_session_state_carries_over_between_cli_segments(tmp_path, toy):
    sess = session(tmp_path, toy)
    (Path(sess.workspace) / "candidate.py").write_text("class Painter: pass\n")
    exchange(sess, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "checkpoint", "arguments": {"note": "first"}}}])
    sess.save()
    again = mcp_server.CLIToolSession(sess.run_dir, sandbox=FakeSandbox(sess.workspace), instance=toy)
    replies = exchange(again, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "checkpoint", "arguments": {"note": "second"}}}])
    assert json.loads(replies[0]["result"]["content"][0]["text"])["checkpoint"] == 2
    state = json.loads((sess.run_dir / "tools-state.json").read_text())
    assert state["manifest"]["segments"] == 2 and len(state["checkpoints"]) == 2


def test_budget_exhaustion_refuses_further_tool_calls(tmp_path, toy):
    sess = session(tmp_path, toy)
    sess.options.max_seconds = 0
    replies = exchange(sess, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "shell", "arguments": {"command": "echo x"}}}])
    assert replies[0]["result"]["isError"]
    assert "budget" in replies[0]["result"]["content"][0]["text"]


def test_oversized_messages_are_refused(tmp_path, toy):
    sess = session(tmp_path, toy)
    stdin = io.BytesIO(b"x" * (mcp_server.MAX_MESSAGE_BYTES + 2) + b"\n")
    with pytest.raises(ValueError, match="size limit"):
        mcp_server.serve(sess, stdin=stdin, stdout=io.StringIO())


def test_the_bridge_loads_the_instance_named_in_its_config(tmp_path, toy):
    run = tmp_path / "run"
    run.mkdir()
    workspace, _ = make_workspace(tmp_path, toy)
    (run / "workspace").symlink_to(workspace)
    (run / "agent-config.json").write_text(json.dumps(
        {"max_seconds": 600, "checkpoint_seconds": 900, "instance": str(toy.root)}))
    sess = mcp_server.CLIToolSession(run, sandbox=FakeSandbox(workspace))
    assert sess.instance.name == "toy"
