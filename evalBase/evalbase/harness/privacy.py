"""Redact host details at logging boundaries, without changing live task data.

Paths and key values are derived at run time. Only saved copies (transcripts,
attempt.json, audit.json) are passed through a Redactor; the workspace, the
graded source and the live provider state are never rewritten.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

POLICY = ("secret values and known host paths are replaced in saved transcripts, "
          "attempt.json and audit.json only; live state and graded source are untouched")

_TOKEN = re.compile(r"\b(?:sk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
_BEARER = re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/-]+=*")
_HOME = re.compile(r"/(?:Users|home)/[^/\s\"'<>]+")
_WINDOWS_HOME = re.compile(r"(?i)\b[A-Z]:\\+Users\\+[^\\/\s\"'<>]+")
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----", re.DOTALL)
_SECRET_FIELDS = {"authorization", "api_key", "openai_api_key", "openrouter_api_key",
                  "anthropic_api_key", "access_token", "refresh_token", "password", "secret"}
_SECRET_ENV = ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


class Redactor:
    def __init__(self, secrets=(), root=None, home=None):
        self.root = Path(root).resolve() if root else Path.cwd().resolve()
        self.home = Path.home() if home is None else Path(home)
        values = [*secrets, *(os.environ.get(name) for name in _SECRET_ENV)]
        self.secrets = sorted({v for v in values if v}, key=len, reverse=True)

    def text(self, value):
        text = str(value)
        for secret in self.secrets:
            if len(secret) < 8:
                text = re.sub(r"(?<!\w)" + re.escape(secret) + r"(?!\w)", "[REDACTED]", text)
            else:
                text = text.replace(secret, "[REDACTED]")
        text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
        text = _TOKEN.sub("[REDACTED]", text)
        text = _BEARER.sub("Bearer [REDACTED]", text)
        for path, replacement in ((self.root, "<project>"), (self.home, "<home>"),
                                  (Path(tempfile.gettempdir()), "<temp>")):
            if str(path) != path.anchor:
                text = text.replace(str(path), replacement)
        text = _HOME.sub("<home>", text)
        return _WINDOWS_HOME.sub("<home>", text)

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(k) if isinstance(k, str) else k:
                    "[REDACTED]" if isinstance(k, str) and k.lower() in _SECRET_FIELDS else self.clean(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(item) for item in value]
        return value

    def dumps(self, value, **kwargs):
        # jsonio: a saved transcript line may carry a tool result with a
        # non-finite number in it; `Infinity` would make the line unreadable.
        from ..grader import jsonio
        return jsonio.dumps(self.clean(value), **kwargs)

    def path(self, value):
        path = Path(value).resolve()
        try:
            return self.text(path.relative_to(self.root).as_posix())
        except ValueError:
            return self.text(path)
