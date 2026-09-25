"""A stub coding-agent CLI for the no-key smoke.

It accepts the same launch command line the real Claude Code / Codex / OpenCode
CLI is given, discovers the MCP server the way that client would (Claude Code
from `--mcp-config`, Codex from its `-c` overrides, OpenCode from the
`OPENCODE_CONFIG` document the harness wrote), speaks JSON-RPC to it over
stdio, runs the scripted tool calls, and emits the same event stream the real
CLI emits (so the audit and the usage parsing are exercised for real). It
never contacts a model.

Segment 1 runs the script and exits 0 (a "final answer"). Because the stop
policy is budget, the harness relaunches it; later segments idle so that the
attempt really ends on the wall budget.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evalbase.grader import jsonio  # noqa: E402
from evalbase.harness.smoke import SMOKE_MARKER_ENV, SMOKE_MODEL, SMOKE_SANDBOX_ENV, build_script, check  # noqa: E402
from evalbase.interfaces import load_instance  # noqa: E402

SESSION = "ses_evalbasesmoke00000000000"


class MCPStdioClient:
    def __init__(self, command, args, cwd, env=None):
        self.proc = subprocess.Popen([command, *args], cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=sys.stderr,
                                     env={**os.environ, **(env or {})}, text=True, bufsize=1)
        self.next_id = 0

    def rpc(self, method, params=None):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("the MCP server closed the connection")
        reply = json.loads(line)
        if reply.get("error"):
            raise RuntimeError("MCP error: " + json.dumps(reply["error"]))
        return reply["result"]

    def call_tool(self, name, arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        text = result["content"][0]["text"]
        if result.get("isError"):
            return {"tool_error": text}
        return json.loads(text)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def discover_server(argv, harness):
    """Find the MCP server the way the real client would: from its own flags."""
    if harness == "opencode":
        mcp = json.loads(Path(os.environ["OPENCODE_CONFIG"]).read_text())["mcp"]
        server = next(iter(mcp))
        entry = mcp[server]
        command, *args = entry["command"]
        return server, command, args, entry.get("cwd"), entry.get("environment") or {}
    if harness == "claude-code":
        path = argv[argv.index("--mcp-config") + 1]
        servers = json.loads(Path(path).read_text())["mcpServers"]
        server = next(iter(servers))
        entry = servers[server]
        return server, entry["command"], entry["args"], entry.get("cwd"), entry.get("env") or {}
    config = {}
    for i, value in enumerate(argv):
        if value == "-c" and i + 1 < len(argv):
            key, _, raw = argv[i + 1].partition("=")
            config[key] = json.loads(raw)
    server = next(k.split(".")[1] for k in config if k.startswith("mcp_servers."))
    prefix = f"mcp_servers.{server}."
    return (server, config[prefix + "command"], config[prefix + "args"],
            config.get(prefix + "cwd"), config.get(prefix + "env") or {})


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def part(payload):
    return {"sessionID": SESSION, "part": {"sessionID": SESSION, **payload}}


def tool_event(harness, server, name, arguments, result):
    if harness == "opencode":
        emit({"type": "tool_use", "timestamp": int(time.time() * 1000),
              **part({"type": "tool", "tool": f"{server}_{name}", "callID": f"call_{name}",
                      "state": {"status": "completed", "input": arguments,
                                "output": jsonio.dumps(result)[:400], "title": name}})})
        return
    if harness == "codex":
        emit({"type": "item.completed", "item": {
            "type": "mcp_tool_call", "server": server, "tool": name, "status": "completed"}})
    else:
        emit({"type": "assistant", "message": {
            "model": SMOKE_MODEL, "role": "assistant",
            "content": [{"type": "tool_use", "id": f"toolu_{name}",
                         "name": f"mcp__{server}__{name}", "input": arguments}]}})
        emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{name}",
             "content": jsonio.dumps(result)[:400]}]}})


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--harness", required=True)
    args, rest = parser.parse_known_args()
    harness = args.harness
    sys.stdin.read()          # the launch prompt, as the real CLI reads it

    marker = Path(os.environ.get("TMPDIR", "/tmp")) / "evalbase-smoke-segment.marker"
    segment_file = Path(os.environ.get(SMOKE_MARKER_ENV, str(marker)))
    first = not segment_file.exists()
    segment_file.write_text(str(time.time()))
    instance = load_instance(os.environ.get("EVALBASE_INSTANCE"))
    script = build_script(instance, os.environ.get(SMOKE_SANDBOX_ENV, "docker"))
    server_name = instance.task.mcp_server

    if harness == "opencode":
        emit({"type": "step_start", "timestamp": int(time.time() * 1000), **part({"type": "step-start"})})
    elif harness == "codex":
        emit({"type": "item.completed", "item": {"type": "reasoning", "text": "smoke"}})
    else:
        emit({"type": "system", "subtype": "init", "model": SMOKE_MODEL,
              "mcp_servers": [{"name": server_name, "status": "connected"}]})

    if not first:
        # Budget policy: the harness relaunched us. Idle until it stops us.
        if harness == "opencode":
            idle = {"type": "text", "timestamp": int(time.time() * 1000),
                    **part({"type": "text", "text": "idle", "time": {"start": 0, "end": 1}})}
        elif harness == "codex":
            idle = {"type": "item.completed", "item": {
                "type": "agent_message", "text": "continuing; nothing left in the smoke script"}}
        else:
            idle = {"type": "assistant", "message": {
                "model": SMOKE_MODEL, "role": "assistant",
                "content": [{"type": "text", "text": "idle"}]}}
        emit(idle)
        while True:
            time.sleep(1)

    server, command, argv, cwd, env = discover_server(rest, harness)
    client = MCPStdioClient(command, argv, cwd, env)
    failures = []
    try:
        client.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "evalbase-smoke-stub", "version": "1.0"}})
        listed = {t["name"] for t in client.rpc("tools/list")["tools"]}
        expected = {name for name, _, _ in script}
        if not expected <= listed:
            raise RuntimeError(f"MCP server is missing tools: {sorted(expected - listed)}")
        for index, (name, arguments, _) in enumerate(script):
            result = client.call_tool(name, arguments)
            tool_event(harness, server, name, arguments, result)
            try:
                check(script, index, result)
            except AssertionError as exc:
                failures.append(str(exc))
    finally:
        client.close()

    if harness == "opencode":
        emit({"type": "text", "timestamp": int(time.time() * 1000),
              **part({"type": "text", "text": "smoke script complete", "time": {"start": 0, "end": 1}})})
        emit({"type": "step_finish", "timestamp": int(time.time() * 1000),
              **part({"type": "step-finish", "reason": "stop", "cost": 0.0,
                      "tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                                 "cache": {"write": 0, "read": 0}}})})
    elif harness == "codex":
        emit({"type": "item.completed", "item": {"type": "agent_message", "text": "smoke script complete"}})
        emit({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}})
    else:
        emit({"type": "result", "subtype": "success", "is_error": False,
              "num_turns": len(script), "total_cost_usd": 0.0,
              "usage": {"input_tokens": 0, "output_tokens": 0}})
    (segment_file.parent / "smoke-failures.json").write_text(json.dumps(failures, indent=1))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
