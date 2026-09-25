"""OpenRouter transport. Keys stay on the host; uncertain POSTs are never retried.
"""
from __future__ import annotations

import copy
import json
import math
import os
import ssl
import sys
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")


def request(payload, key, timeout):
    context = ssl.create_default_context()
    if sys.platform == "darwin" and not context.cert_store_stats()["x509_ca"] and os.path.isfile("/etc/ssl/cert.pem"):
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "X-Title": "evalbase"})
    try:
        with urllib.request.urlopen(req, context=context, timeout=timeout) as response:
            data = response.read(16_000_001)
            if len(data) > 16_000_000:
                raise RuntimeError("provider response exceeded 16 MB")
            return json.loads(data)
    except urllib.error.HTTPError as exc:
        message = exc.read(4000).decode(errors="replace")
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {message}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("provider request failed; no automatic retry was made and billing may "
                           "be uncertain: " + str(exc)) from None


def usage(response):
    """Provider-reported usage is authoritative. Missing usage stops the run."""
    u = response.get("usage") or {}
    a, b, c = u.get("prompt_tokens"), u.get("completion_tokens"), u.get("cost")
    if type(a) is not int or type(b) is not int or a < 0 or b < 0:
        raise RuntimeError("provider omitted valid token usage; stopping as an accounting failure")
    if type(c) not in (int, float) or not math.isfinite(c) or c < 0:
        raise RuntimeError("provider omitted valid usage.cost; stopping instead of spending "
                           "without accounting")
    return a, b, float(c)


def assistant_message(response):
    if response.get("error"):
        raise RuntimeError("provider error: " + str(response["error"]))
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("provider returned no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("provider returned no assistant message")
    if choices[0].get("finish_reason") in ("error", "content_filter"):
        raise RuntimeError("provider stopped: " + str(choices[0]["finish_reason"]))
    # Preserve signed/encrypted reasoning and other provider fields exactly.
    message = copy.deepcopy(message)
    message["role"] = "assistant"
    message.setdefault("content", None)
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or len(calls) > 32:
        raise RuntimeError("invalid number of tool calls")
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("id"), str):
            raise RuntimeError("malformed tool call")
        function = call.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) \
                or not isinstance(function.get("arguments"), str):
            raise RuntimeError("malformed function arguments")
    if len({c["id"] for c in calls}) != len(calls):
        raise RuntimeError("duplicate tool-call ids")
    return message, choices[0].get("finish_reason") == "length"
