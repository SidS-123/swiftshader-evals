"""Audit the native CLI event and diagnostic streams, including denied tools.

The MCP server name and
the tool names are parameters (`audit_events(..., server=, tools=)`), so the
allowed sets are derived per instance.

An attempt in which a native host tool ran is invalid, not low-scoring. This
reads the CLI's own machine-readable event stream plus its stderr; missing or
unreadable evidence fails closed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .tools import TOOL_NAMES

DEFAULT_SERVER = "evalbase"


def allowed_sets(server: str = DEFAULT_SERVER, tools=TOOL_NAMES) -> tuple[set, set, set]:
    """(mcp tool names, Claude Code tool names, OpenCode tool names) for one server.

    OpenCode names an MCP tool `<server>_<tool>`, flat, with no namespace marker;
    a built-in would appear under its bare name (`bash`, `edit`, ...).
    """
    mcp = set(tools)
    return mcp, {"mcp__" + server + "__" + name for name in mcp}, {server + "_" + name for name in mcp}
CODEX_HOST_ITEMS = {"command_execution", "file_change", "web_search",
                    "image_generation", "collab_tool_call"}
CODEX_PASSIVE_ITEMS = {"agent_message", "reasoning", "todo_list", "mcp_tool_call", "error"}
# `tool_progress` is a keepalive Claude Code 2.1.274 emits while one MCP tool
# call is still running (grade_dev regularly exceeds its 30 s heartbeat). It is
# passive, but it names the tool it is waiting on, so it is checked like a
# tool_use block rather than merely ignored.
CLAUDE_PASSIVE_TYPES = {"system", "assistant", "user", "result", "stream_event",
                        "control_response", "summary", "rate_limit_event",
                        "tool_progress"}
# The complete set `opencode run --format json` emits (OpenCode 1.18.32): the
# stream is a flat JSON line per event and carries nothing else, so anything
# outside this set fails the audit closed.
OPENCODE_PASSIVE_TYPES = {"step_start", "step_finish", "text", "reasoning", "error"}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# ------------------------------------------------------- provider rate limits
#
# Claude Code 2.1.274 reports a subscription usage limit two ways, both of which
# appear in `--output-format stream-json`:
#
#   {"type":"rate_limit_event","rate_limit_info":{"status":"rejected",
#    "rateLimitType":"five_hour","resetsAt":<unix seconds>,...},...}
#
# ("status" is one of allowed | allowed_warning | rejected; "rateLimitType" is
# one of five_hour | seven_day | seven_day_opus | seven_day_sonnet |
# seven_day_overage_included | overage), and, when the turn actually dies on it,
# an error result:
#
#   {"type":"result","subtype":"error_during_execution","is_error":true,
#    "api_error_status":429,...}
#
# with the upstream `{"type":"rate_limit_error"}` body in `result`/`errors`.
# Only error-context text is matched, never model prose.
RATE_LIMIT_TEXT = re.compile(
    r'"type"\s*:\s*"rate_limit_error"'
    r'|\busage limit reached\b'
    r'|\bapi error:\s*429\b'
    r'|\bhttp[ _]?429\b'
    r'|\bstatus(?:[ _]code)?\s*[=:]\s*429\b'
    r'|\brate[ _-]?limit(?:ed|_error)?\b[^\n]{0,60}?\b(?:reset|retry|try again|exceed(?:ed)?|reach(?:ed)?)\b',
    re.IGNORECASE)
RESETS_AT = re.compile(r'"resetsAt"\s*:\s*(\d{9,12})|\bresets_at[=: ]\s*(\d{9,12})')


def _resets_at(text: str) -> int | None:
    match = RESETS_AT.search(text or "")
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _api_error(event: dict) -> dict | None:
    """OpenCode's `{"type":"error","error":{"name":"APIError","data":{...}}}`."""
    if not isinstance(event, dict) or event.get("type") != "error":
        return None
    error = event.get("error")
    data = error.get("data") if isinstance(error, dict) else None
    return data if isinstance(data, dict) and "statusCode" in data else None


def _reset_epoch(value) -> int | None:
    """OpenRouter reports `X-RateLimit-Reset` in milliseconds; we keep seconds."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number > 10 ** 11:
        number //= 1000
    return number if 10 ** 9 < number < 10 ** 11 else None


def auth_failure_signal(events_text: str) -> dict | None:
    """A rejected credential reported by the CLI's own stream, or None.

    401/403 is not a rate limit and not a boundary breach: it is the operator's
    key. Retrying cannot fix it, so the runner stops on the first one.
    """
    for line in (events_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = _api_error(json.loads(line))
        except ValueError:
            continue
        if data and data.get("statusCode") in (401, 403):
            return {"status": data.get("statusCode"),
                    "evidence": str(data.get("message") or "")[:400]}
    return None


def rate_limit_from_event(event: dict) -> dict | None:
    """A usage/rate-limit stop reported by one CLI event, or None."""
    if not isinstance(event, dict):
        return None
    data = _api_error(event)
    if data is not None:
        if data.get("statusCode") != 429:
            return None
        headers = data.get("responseHeaders")
        reset = (headers or {}).get("x-ratelimit-reset") if isinstance(headers, dict) else None
        return {"signal": "opencode_api_error", "kind": "http_429",
                "resets_at": _reset_epoch(reset),
                "evidence": json.dumps({k: data.get(k) for k in ("message", "statusCode")},
                                       default=str)[:400]}
    info = event.get("rate_limit_info")
    if event.get("type") == "rate_limit_event" and isinstance(info, dict):
        if info.get("status") == "rejected":
            return {"signal": "rate_limit_event", "kind": info.get("rateLimitType"),
                    "resets_at": info.get("resetsAt") if isinstance(info.get("resetsAt"), int) else None,
                    "evidence": json.dumps(info, default=str)[:400]}
        return None
    if event.get("type") != "result" or not event.get("is_error"):
        return None
    if event.get("api_error_status") == 429 or "rate_limit" in str(event.get("api_error_code") or ""):
        text = json.dumps({k: event.get(k) for k in ("result", "errors", "api_error_code")},
                          default=str)
        return {"signal": "result_api_error", "kind": event.get("api_error_code") or "http_429",
                "resets_at": _resets_at(text), "evidence": text[:400]}
    text = json.dumps({k: event.get(k) for k in ("result", "errors", "subtype")}, default=str)
    if RATE_LIMIT_TEXT.search(text):
        return {"signal": "result_text", "kind": event.get("subtype"),
                "resets_at": _resets_at(text), "evidence": text[:400]}
    return None


def rate_limit_signal(events_text: str, stderr_text: str = "") -> dict | None:
    """Scan one CLI segment's own output for a usage/rate-limit stop.

    Pure text in, verdict out, so it is testable without a CLI or a provider.
    """
    found = None
    for line in (events_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        hit = rate_limit_from_event(event)
        if hit:
            # The last observation wins: it carries the freshest reset time.
            found = hit if found is None else {**found, **{k: v for k, v in hit.items()
                                                           if v is not None}}
    for number, raw in enumerate((stderr_text or "").splitlines(), 1):
        line = ANSI.sub("", raw)
        if RATE_LIMIT_TEXT.search(line):
            hit = {"signal": "stderr", "kind": None, "resets_at": _resets_at(line),
                   "evidence": line.strip()[:400], "stderr_line": number}
            found = hit if found is None else {**found, **{k: v for k, v in hit.items()
                                                           if v is not None}}
    return found


def _cost_basis(event: dict) -> str | None:
    """`modelUsage[*].costBasis` — "list" means a list-price estimate, not a charge."""
    model_usage = event.get("modelUsage")
    if not isinstance(model_usage, dict):
        return None
    bases = {entry.get("costBasis") for entry in model_usage.values()
             if isinstance(entry, dict) and entry.get("costBasis")}
    return sorted(bases)[0] if len(bases) == 1 else (",".join(sorted(bases)) or None)


def _usage_totals(entries: list) -> dict:
    """Token totals across every result event (each is one CLI segment's total)."""
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens")
    totals = {k: 0 for k in keys}
    seen = False
    for entry in entries:
        usage = entry.get("usage") if isinstance(entry, dict) else None
        if not isinstance(usage, dict):
            continue
        seen = True
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)):
                totals[key] += int(value)
    return totals if seen else {k: None for k in keys}


def _cost_record(entries: list, note: str | None = None) -> dict:
    """What the CLI said about dollars — never what we guess it spent.

    A claude.ai subscription run still emits `total_cost_usd`, but its
    `modelUsage[*].costBasis` is "list": a list-price estimate of what the same
    tokens would have cost on the API, not a charge anyone was billed. Reports
    must not present it as a measured cost.
    """
    if not entries:
        return {"reported": False, "total_usd": None, "basis": None,
                "is_billed_charge": False,
                "note": "the CLI reported no dollar cost for this attempt"}
    bases = sorted({e["basis"] for e in entries if e.get("basis")})
    basis = bases[0] if len(bases) == 1 else (",".join(bases) or None)
    return {"reported": True, "total_usd": round(sum(e["total_cost_usd"] for e in entries), 6),
            "basis": basis, "is_billed_charge": False,
            "note": note or ("the CLI reports total_cost_usd as a list-price estimate "
                             "(modelUsage.costBasis=%s); on a subscription login nobody was "
                             "billed this amount" % (basis or "unknown"))}


def stderr_findings(path, harness):
    violations, errors = [], []
    if path is None or not Path(path).is_file():
        return [], [{"type": "missing_cli_stderr"}]
    if harness == "codex":
        for number, raw in enumerate(Path(path).open(errors="replace"), 1):
            line = ANSI.sub("", raw)
            # Failed patch validation never reaches the file_change JSON event in
            # Codex 0.154.0. Match the logger, not quoted candidate output.
            if "codex_core::tools::router:" not in line:
                continue
            if re.search(r"\berror=apply_patch\b", line):
                violations.append({"kind": "native_apply_patch_attempt", "stream": "stderr", "line": number})
            elif re.search(r"\berror=", line):
                errors.append({"type": "native_tool_router_error", "stream": "stderr", "line": number})
    return violations, errors


def audit_events(path, harness, stderr_path=None, server: str = DEFAULT_SERVER, tools=TOOL_NAMES):
    """Return an eligibility verdict. Numeric grading is a separate question."""
    harness = {"claude-code": "claude", "codex": "codex"}.get(harness, harness)
    ALLOWED_MCP_TOOLS, ALLOWED_CLAUDE_TOOLS, ALLOWED_OPENCODE_TOOLS = allowed_sets(server, tools)
    SERVER = server
    violations, errors, evidence, served = [], [], [], []
    tool_errors = []
    usage = None
    usage_entries = []
    rate_limits = []
    cost_entries = []
    events = 0
    mcp_calls = 0
    for number, line in enumerate(Path(path).open(errors="replace"), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("not an event object")
        except ValueError:
            errors.append({"type": "malformed_cli_event", "line": number})
            continue
        events += 1
        if harness == "opencode":
            kind = event.get("type")
            if kind not in OPENCODE_PASSIVE_TYPES and kind != "tool_use":
                errors.append({"type": "unsupported_cli_event", "kind": kind, "line": number})
                continue
            part = event.get("part")
            if kind in ("tool_use", "step_finish") and not isinstance(part, dict):
                errors.append({"type": "malformed_cli_part", "line": number})
                continue
            if kind == "tool_use":
                mcp_calls += 1
                name = part.get("tool")
                if name not in ALLOWED_OPENCODE_TOOLS:
                    violations.append("unexpected OpenCode tool " + str(name))
                    evidence.append({"kind": "unexpected_opencode_tool", "tool": name,
                                     "line": number})
                state = part.get("state") or {}
                if isinstance(state, dict) and state.get("status") == "error":
                    # A tool that rejected its arguments. Recorded, but not a
                    # boundary breach and not an invalid measurement: the model
                    # is allowed to call a tool wrongly and try again.
                    tool_errors.append({"tool": name, "line": number,
                                        "error": str(state.get("error") or "")[:400]})
            elif kind == "step_finish":
                counts = part.get("tokens") or {}
                cache = counts.get("cache") or {} if isinstance(counts, dict) else {}
                if isinstance(counts, dict) and counts:
                    usage = {"input_tokens": counts.get("input"),
                             "output_tokens": counts.get("output"),
                             "cache_creation_input_tokens": (cache or {}).get("write"),
                             "cache_read_input_tokens": (cache or {}).get("read")}
                    usage_entries.append({"usage": usage,
                                          "total_cost_usd": part.get("cost")})
                if isinstance(part.get("cost"), (int, float)):
                    cost_entries.append({"total_cost_usd": float(part["cost"]),
                                         "basis": "list"})
            elif kind == "error":
                hit = rate_limit_from_event(event)
                if hit:
                    rate_limits.append({**hit, "line": number})
                else:
                    data = _api_error(event) or {}
                    errors.append({"type": "cli_result_error",
                                   "subtype": (event.get("error") or {}).get("name")
                                   if isinstance(event.get("error"), dict) else None,
                                   "status": data.get("statusCode")})
        elif harness == "codex":
            item = event.get("item") or {}
            if not isinstance(item, dict):
                errors.append({"type": "malformed_cli_item", "line": number})
                continue
            kind = item.get("type")
            if kind in CODEX_HOST_ITEMS:
                violations.append(kind)
                evidence.append({"kind": kind, "stream": "events", "line": number})
            elif item and kind not in CODEX_PASSIVE_ITEMS:
                errors.append({"type": "unsupported_cli_item", "kind": kind, "line": number})
            if kind == "mcp_tool_call":
                mcp_calls += 1
                if item.get("server") != SERVER or item.get("tool") not in ALLOWED_MCP_TOOLS:
                    violations.append("unexpected MCP tool")
                    evidence.append({"kind": "unexpected_mcp_tool", "server": item.get("server"),
                                     "tool": item.get("tool"), "line": number})
                if item.get("error"):
                    errors.append({"type": "mcp_transport_error", "error": item["error"]})
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
                usage_entries.append(event.get("usage"))
            if event.get("type") in ("error", "turn.failed"):
                errors.append(event)
        elif harness == "claude":
            kind = event.get("type")
            if kind and kind not in CLAUDE_PASSIVE_TYPES:
                errors.append({"type": "unsupported_cli_event", "kind": kind, "line": number})
            if kind == "tool_progress":
                name = event.get("tool_name")
                if name is not None and name not in ALLOWED_CLAUDE_TOOLS:
                    violations.append("unexpected Claude tool " + str(name))
                    evidence.append({"kind": "unexpected_claude_tool", "tool": name,
                                     "stream": "tool_progress", "line": number})
            message = event.get("message") or {}
            if not isinstance(message, dict):
                errors.append({"type": "malformed_cli_message", "line": number})
                continue
            if message.get("model") and message["model"] not in served:
                served.append(message["model"])
            content = message.get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    mcp_calls += 1
                    if block.get("name") not in ALLOWED_CLAUDE_TOOLS:
                        violations.append("unexpected Claude tool " + str(block.get("name")))
                        evidence.append({"kind": "unexpected_claude_tool", "tool": block.get("name"),
                                         "line": number})
            if kind == "rate_limit_event":
                hit = rate_limit_from_event(event)
                if hit:
                    rate_limits.append({**hit, "line": number})
            if kind == "result":
                usage = event.get("usage")
                usage_entries.append({"usage": event.get("usage"),
                                      "total_cost_usd": event.get("total_cost_usd")})
                if isinstance(event.get("total_cost_usd"), (int, float)):
                    cost_entries.append({"total_cost_usd": float(event["total_cost_usd"]),
                                         "basis": _cost_basis(event)})
                if event.get("is_error"):
                    hit = rate_limit_from_event(event)
                    if hit:
                        # A provider usage limit is a pause the runner waits out,
                        # not evidence that the tool boundary leaked.
                        rate_limits.append({**hit, "line": number})
                    else:
                        errors.append({"type": "cli_result_error", "subtype": event.get("subtype")})
        else:
            raise ValueError("unsupported CLI harness: " + str(harness))
    extra, stderr_errors = stderr_findings(stderr_path, harness)
    evidence.extend(extra)
    violations.extend(entry["kind"] for entry in extra)
    errors.extend(stderr_errors)
    if not events:
        errors.append({"type": "empty_cli_events"})
    cost_note = None
    if harness == "opencode":
        # The other CLIs report one usage object per segment; OpenCode reports
        # one per step, so the last step's is what `usage` means here.
        usage = (usage_entries[-1] or {}).get("usage") if usage_entries else None
        cost_note = ("OpenCode prices the run itself from its model catalogue; this is a "
                     "list-price estimate of the tokens, not a charge read back from the "
                     "provider")
    return {"valid_tool_boundary": not violations and not errors,
            "tool_errors": tool_errors,
            "violations": sorted(set(violations)), "boundary_evidence": evidence,
            "audit_streams": ["events", "stderr"], "mcp_tool_calls": mcp_calls,
            "usage": usage, "usage_entries": usage_entries,
            "usage_source": "CLI event stream; cost not inferred",
            "usage_totals": _usage_totals(usage_entries),
            "cost": _cost_record(cost_entries, cost_note),
            "rate_limit_events": rate_limits,
            "served_models": served or None, "errors": errors, "events": events}
