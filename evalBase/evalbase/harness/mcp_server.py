"""stdio MCP bridge: the CLI harnesses' only tools.

The server name is
the instance's `TaskSpec.mcp_server`.

The CLI and the operator's credentials stay on the host. Every command the
model writes runs inside the sandbox; the oracle runs through the instance's
driver; grading uses the real grader on the public corpus. No grader
administration or arbitrary host path operation is exposed.

Run as: python3 -m evalbase.harness.mcp_server <run_dir>
The run directory's `agent-config.json` names the instance.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from ..grader import jsonio
from ..interfaces import load_instance
from .privacy import Redactor
from .tools import TOOL_NAMES, ToolSession, atomic_json, make_sandbox, tool_schemas, validate_arguments

SERVER_VERSION = "1.0"
MAX_MESSAGE_BYTES = 4_000_000


class CLIToolSession(ToolSession):
    def __init__(self, run_dir, *, sandbox=None, instance=None):
        self.run_dir = Path(run_dir).resolve()
        config = json.loads((self.run_dir / "agent-config.json").read_text())
        self.instance = instance or load_instance(config.get("instance"))
        self.options = SimpleNamespace(**config)
        self.workspace = self.run_dir / "workspace"
        for attr in ("corpus_public", "corpus_assets", "refcache"):
            value = config.get(attr)
            if value:
                setattr(self, attr, Path(value))
        self.started = time.monotonic()
        self.redactor = Redactor(root=self.instance.root)
        self.tools = tool_schemas(self.instance.task.tool_descriptions)
        # A CLI attempt runs in segments (budget policy relaunches the CLI). Tool
        # state - checkpoint numbering and cumulative solver time - carries over.
        previous = self.run_dir / "tools-state.json"
        if previous.is_file():
            self.state = json.loads(previous.read_text())
            self.state["manifest"]["status"] = "running"
            self.state["manifest"]["segments"] = self.state["manifest"].get("segments", 0) + 1
        else:
            self.state = {"checkpoint_index": 0, "oracle_index": 0, "grade_index": 0,
                          "checkpoints": [], "manifest": {
                              "status": "running", "calls": 0, "segments": 1,
                              "tool_calls": {name: 0 for name in TOOL_NAMES},
                              "solver_seconds": 0.0, "stop_reason": None}}
        self.base_elapsed = float(self.state["manifest"].get("solver_seconds", 0.0))
        self.last_checkpoint = self.base_elapsed
        # Every container started for this attempt is labelled with the attempt
        # id, and only a sweep carrying that id may remove any of them.
        self.owner = config.get("owner") or self.run_dir.name
        self.state["manifest"].setdefault("infrastructure_incidents", [])
        self.sandbox = sandbox or make_sandbox(
            config.get("sandbox", "docker"), self.workspace,
            image=config.get("solver_image") or self.instance.solver_image,
            cpus=str(config.get("cpus", "4")), memory=str(config.get("memory", "8g")),
            owner=self.owner, on_incident=self.record_incident)
        self.manifest["sandbox_name"] = self.sandbox.name
        self.manifest["sandbox_argv"] = list(getattr(self.sandbox, "argv", []) or [])
        self.manifest["owner"] = self.owner
        self.save()

    @property
    def manifest(self):
        return self.state["manifest"]

    def elapsed(self):
        return self.base_elapsed + time.monotonic() - self.started

    def save(self):
        self.manifest["solver_seconds"] = round(self.elapsed(), 3)
        atomic_json(self.run_dir / "tools-state.json", self.state, mode=0o600)

    def log(self, event, **data):
        path = self.run_dir / "tools-transcript.jsonl"
        with path.open("a") as f:
            f.write(self.redactor.dumps({"event": event, "elapsed": round(self.elapsed(), 3), **data}) + "\n")

    def call(self, name, arguments):
        if self.elapsed() >= getattr(self.options, "max_seconds", 86400):
            raise ValueError("the time budget for this attempt has ended")
        validate_arguments(name, arguments)
        self.manifest["calls"] += 1
        self.manifest["tool_calls"][name] = self.manifest["tool_calls"].get(name, 0) + 1
        self.log("tool_call", name=name, arguments=arguments)
        result = self.execute({"function": {"name": name, "arguments": json.dumps(arguments)}})
        self.log("tool_result", name=name, result=result)
        interval = getattr(self.options, "checkpoint_seconds", 900)
        if name != "checkpoint" and self.elapsed() - self.last_checkpoint >= interval:
            self.checkpoint("Automatic checkpoint")
        self.save()
        return self.redactor.clean(result)

    def close(self):
        try:
            self.sandbox.close()
        finally:
            self.manifest["status"] = "stopped"
            self.save()


def response(request, session):
    method = request.get("method")
    params = request.get("params") or {}
    server = session.instance.task.mcp_server
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": server, "version": SERVER_VERSION}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": t["function"]["name"], "description": t["function"]["description"],
                           "inputSchema": t["function"]["parameters"]} for t in session.tools]}
    if method in ("resources/list",):
        return {"resources": []}
    if method in ("resources/templates/list",):
        return {"resourceTemplates": []}
    if method in ("prompts/list",):
        return {"prompts": []}
    if method == "tools/call":
        try:
            result = session.call(params.get("name"), params.get("arguments", {}))
            # jsonio: a tool result carrying a non-finite number must reach the
            # model as null, not break the message.
            return {"content": [{"type": "text", "text": jsonio.dumps(result)}],
                    "isError": False}
        except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
            session.log("tool_error", name=params.get("name"), error=str(exc))
            session.save()
            return {"content": [{"type": "text", "text": session.redactor.text(exc)}], "isError": True}
    raise ValueError("unsupported MCP method: " + str(method))


def serve(session, stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    while True:
        line = stdin.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            break
        if len(line) > MAX_MESSAGE_BYTES:
            raise ValueError("MCP message exceeds the size limit")
        if not line.strip():
            continue
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            envelope = {"jsonrpc": "2.0", "id": request["id"], "result": response(request, session)}
        except ValueError as exc:
            envelope = {"jsonrpc": "2.0", "id": request["id"],
                        "error": {"code": -32601, "message": str(exc)}}
        stdout.write(jsonio.dumps(envelope) + "\n")
        stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="evalbase.harness.mcp_server")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    session = CLIToolSession(args.run_dir)
    try:
        serve(session)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()


if __name__ == "__main__":
    main()
