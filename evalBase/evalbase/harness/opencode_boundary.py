"""Restrict OpenCode's native tool catalog without replacing its agent loop.

OpenCode has no `--tools ''` and no `--ephemeral`. Everything is decided by one
configuration document, so the boundary *is* that document: the harness writes
an attempt-local `opencode.json`, hands it to the CLI through `OPENCODE_CONFIG`,
and then asks the binary what it actually resolved (`opencode debug config`,
`opencode debug agent <name>`) before a single token is spent.

Both tool lists were qualified against the installed binary, not the docs.
OpenCode 1.18.32 was pointed at a local endpoint that recorded the `tools`
array of the request it actually sent: with nothing disabled the model was
offered ten built-ins (`bash edit write read glob grep skill task todowrite
webfetch`) beside the MCP tools, and with the document this module builds it
was offered `<server>_*` and nothing else. `opencode debug agent` then reports
the build's full effective set, twelve ids, which is `OFFERED_BUILTIN_TOOLS`.
`BUILTIN_TOOLS` is deliberately wider: disabling a tool this build does not
have is inert, and costs nothing if a later build adds it.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

BOUNDARY_VERSION = "opencode-mcp-v1"
QUALIFIED_CLI_VERSION = "1.18.32"

# Every built-in tool this build can hand the model. See the module docstring.
BUILTIN_TOOLS = ("bash", "edit", "write", "read", "glob", "grep", "list", "patch",
                 "multiedit", "todowrite", "todoread", "webfetch", "websearch", "task",
                 "skill", "question", "lsp", "invalid", "batch", "codemode")
# The twelve `opencode debug agent` reports as this build's effective tool set
# (ten of which it will offer a model outright; `question` and `invalid` are
# conditional). Every one of them must be off, and the check says which is not.
OFFERED_BUILTIN_TOOLS = ("bash", "edit", "write", "read", "glob", "grep", "skill",
                         "task", "todowrite", "webfetch", "question", "invalid")
# Permission names, which are not quite the tool names: `write` is governed by
# `edit`, and `external_directory` has no tool of its own.
DENIED_PERMISSIONS = ("bash", "edit", "read", "glob", "grep", "list", "todowrite",
                      "todoread", "webfetch", "websearch", "task", "skill", "question",
                      "lsp", "external_directory")
# `doom_loop` is not a tool: it is OpenCode asking whether a model that looks
# stuck should carry on. `run` auto-rejects anything it has to ask about, which
# would end a segment early, so it is allowed outright.
ALLOWED_PERMISSIONS = {"doom_loop": "allow"}

# Environment that keeps one attempt off the host's OpenCode installation: no
# user config, no project config, no plugins, no skills discovered from the
# host, no auto-update, no sharing. The credential is deliberately *not* here;
# `cli_runner.launch_env` adds it, and the solver container never sees it.
ISOLATION_ENV = {
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "OPENCODE_DISABLE_EMBEDDED_WEB_UI": "1",
    "OPENCODE_DISABLE_MOUSE": "1",
}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def split_model(model: str) -> tuple[str, str]:
    """`openrouter/openai/gpt-5` -> ("openrouter", "openai/gpt-5").

    OpenCode addresses a model as `<provider>/<model>`; the provider id is the
    first segment and everything after it is the model id, verbatim. Nothing is
    normalised, mapped or defaulted: an id without a provider is an error, not a
    guess.
    """
    provider, _, rest = str(model).partition("/")
    if not provider or not rest:
        raise ValueError(
            "the opencode harness needs --model <provider>/<model-id>, e.g. "
            "openrouter/openai/gpt-5; got " + repr(model))
    return provider, rest


def config_document(*, model, server, agent, bridge, instructions, tool_timeout,
                    reasoning=None) -> dict:
    """The whole boundary, as one JSON document. Pure: no host state is read."""
    provider_id, model_id = split_model(model)
    disabled = {name: False for name in BUILTIN_TOOLS}
    permission = {name: "deny" for name in DENIED_PERMISSIONS} | dict(ALLOWED_PERMISSIONS)
    document = {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        # Title generation and other "small model" work would otherwise reach a
        # different model (OpenCode defaults it to its own choice). Pin it to the
        # model under test so nothing else is ever called on the operator's key.
        "small_model": model,
        "autoupdate": False,
        "share": "disabled",
        "snapshot": False,
        "formatter": False,
        "lsp": False,
        "instructions": [],
        "plugin": [],
        "subagent_depth": 0,
        "default_agent": agent,
        "tools": dict(disabled),
        "permission": dict(permission),
        "agent": {agent: {
            "mode": "primary",
            "description": f"{server} solver; tools only through the {server} MCP bridge",
            "prompt": instructions,
            "model": model,
            "tools": dict(disabled),
            "permission": dict(permission),
        }},
        "mcp": {server: {
            "type": "local",
            "command": [bridge["command"], *bridge["args"]],
            "cwd": bridge["cwd"],
            "environment": dict(bridge.get("env") or {}),
            "enabled": True,
            # OpenCode's default MCP request timeout is 5 s; grade_dev routinely
            # runs for minutes. Both knobs are set because the per-server one is
            # the documented path and the experimental one is what older builds
            # honoured.
            "timeout": int(tool_timeout) * 1000,
        }},
        "experimental": {"mcp_timeout": int(tool_timeout) * 1000},
    }
    if reasoning:
        # OpenRouter takes the effort on the request body; OpenCode passes a
        # per-model `options` block straight through to the provider.
        document["provider"] = {provider_id: {"models": {
            model_id: {"options": {"reasoning": {"effort": reasoning}}}}}}
    return document


def check_config(document: dict, *, server: str, agent: str) -> None:
    """Fail unless this document leaves the model the MCP tools and nothing else."""
    def fail(why):
        raise RuntimeError("the OpenCode tool boundary is not intact: " + why)

    tools = document.get("tools")
    if not isinstance(tools, dict):
        fail("no `tools` map in the resolved configuration")
    for name in OFFERED_BUILTIN_TOOLS:
        if tools.get(name) is not False:
            fail(f"built-in tool {name!r} is not disabled")
    enabled = sorted(name for name, value in tools.items() if value is not False)
    if enabled:
        fail("built-in tools left enabled: " + ", ".join(enabled))
    permission = document.get("permission")
    if not isinstance(permission, dict):
        fail("no `permission` map in the resolved configuration")
    for name in DENIED_PERMISSIONS:
        if name in permission and permission[name] != "deny":
            fail(f"permission {name!r} is {permission[name]!r}, not 'deny'")
    mcp = document.get("mcp")
    if not isinstance(mcp, dict) or set(mcp) != {server}:
        fail(f"the only MCP server must be {server!r}, got {sorted(mcp or {})}")
    if mcp[server].get("enabled") is not True:
        fail(f"the {server!r} MCP server is not enabled")
    if document.get("plugin"):
        fail("plugins are configured")
    if document.get("instructions"):
        fail("host instruction files are configured")
    if document.get("share") != "disabled":
        fail("session sharing is not disabled")
    if document.get("autoupdate") is not False:
        fail("auto-update is not disabled")
    agents = document.get("agent") or {}
    if agent not in agents:
        fail(f"the {agent!r} agent is missing")
    entry = agents[agent]
    if entry.get("mode") != "primary":
        fail(f"the {agent!r} agent is not a primary agent")
    for name in OFFERED_BUILTIN_TOOLS:
        if (entry.get("tools") or {}).get(name) is not False:
            fail(f"the {agent!r} agent leaves built-in tool {name!r} enabled")


def effective_action(rules, name: str) -> str | None:
    """What `opencode debug agent` says will happen to `name`.

    The resolved permission is a precedence-ordered rule list, not a map:
    OpenCode's own defaults come first (including a bare `* -> allow`) and the
    attempt-local rules come last, so the *last* matching rule is the one in
    force. Only catch-all patterns are considered; a narrower pattern decides
    only the paths it names.
    """
    action = None
    for rule in rules or ():
        if not isinstance(rule, dict) or rule.get("pattern") not in ("*", None):
            continue
        if rule.get("permission") in (name, "*"):
            action = rule.get("action")
    return action


def check_agent(document: dict, *, agent: str) -> None:
    """`opencode debug agent <name>` — the agent the run will actually use."""
    if document.get("name") != agent:
        raise RuntimeError(f"OpenCode resolved a different agent: {document.get('name')!r}")
    if document.get("mode") != "primary":
        raise RuntimeError(f"the {agent!r} agent is not primary; `run --agent` would fall back")
    # The resolved `tools` map is this build's effective tool set, already
    # filtered to the tools it really has. Every one of them must be off.
    tools = document.get("tools")
    if not isinstance(tools, dict) or not tools:
        raise RuntimeError("the resolved agent reports no tool set; the boundary is unverifiable")
    enabled = sorted(name for name, value in tools.items() if value is not False)
    if enabled:
        raise RuntimeError("the resolved agent still has built-in tools: " + ", ".join(enabled))
    permission = document.get("permission")
    if isinstance(permission, dict):
        offending = sorted(k for k, v in permission.items()
                           if k in DENIED_PERMISSIONS and v != "deny")
        if offending:
            raise RuntimeError("the resolved agent still allows: " + ", ".join(offending))
        return
    if not isinstance(permission, list):
        raise RuntimeError("the resolved agent reports no permissions; the boundary is unverifiable")
    named = {rule.get("permission") for rule in permission if isinstance(rule, dict)}
    offending = sorted(name for name in DENIED_PERMISSIONS
                       if name in named and effective_action(permission, name) != "deny")
    if offending:
        raise RuntimeError("the resolved agent still allows: " + ", ".join(offending))


def _debug_json(executable, args, cwd, env, what):
    result = subprocess.run([executable, *args], capture_output=True, text=True,
                            timeout=180, cwd=str(cwd), env=env)
    if result.returncode:
        raise RuntimeError(f"`opencode {' '.join(args)}` failed; the tool boundary was not "
                           f"qualified ({what})")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"`opencode {' '.join(args)}` did not return JSON; the tool "
                           f"boundary was not qualified ({what})") from exc


def prepare(executable, config_path, *, cwd, env, server, agent, cli_version=None) -> dict:
    """Write nothing; ask the installed binary what it resolved, and check it.

    Returns the record stored as `boundary-preflight.json`. Raises before any
    model is contacted if the configuration the CLI resolved is not the one the
    harness wrote, or leaves any host tool reachable.
    """
    config_path = Path(config_path)
    written = json.loads(config_path.read_text())
    check_config(written, server=server, agent=agent)
    resolved = _debug_json(executable, ["debug", "config"], cwd, env, "resolved configuration")
    check_config(resolved, server=server, agent=agent)
    resolved_agent = _debug_json(executable, ["debug", "agent", agent], cwd, env,
                                 "resolved agent")
    check_agent(resolved_agent, agent=agent)
    return {
        "boundary_version": BOUNDARY_VERSION,
        "qualified_cli_version": QUALIFIED_CLI_VERSION,
        "cli_version": cli_version,
        "source": ("attempt-local opencode.json via OPENCODE_CONFIG, verified against "
                   "`opencode debug config` and `opencode debug agent`"),
        "mcp_server": server,
        "agent": agent,
        "builtin_tools_disabled": list(BUILTIN_TOOLS),
        "permissions_denied": list(DENIED_PERMISSIONS),
        "written_config_sha256": digest(written),
        "resolved_config_sha256": digest(resolved),
        "resolved_agent_sha256": digest(resolved_agent),
        "config_file_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    }
