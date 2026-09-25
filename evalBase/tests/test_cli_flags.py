"""Validate the launch flags against the installed CLIs' own --help.

No model call is
ever made; the tests skip when the CLI is not installed.
"""
import shutil
import subprocess

import pytest

from evalbase.harness import attempt as att
from evalbase.harness.cli_runner import OPENCODE_FALLBACK_BIN, launch_command


def opencode_bin():
    return shutil.which("opencode") or (str(OPENCODE_FALLBACK_BIN) if OPENCODE_FALLBACK_BIN.is_file() else None)


def help_text(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    return (result.stdout or "") + (result.stderr or "")


def emitted_flags(cmd):
    return [value for value in cmd if value.startswith("--") and value != "--"]


@pytest.mark.skipif(not shutil.which("claude"), reason="Claude Code CLI is not installed")
def test_claude_accepts_every_flag_we_emit(tmp_path, toy):
    text = help_text(["claude", "--help"])
    cmd = launch_command(toy, att.Options(harness="claude-code", model="claude-opus-5", reasoning="high"),
                         tmp_path, tmp_path, "claude")
    missing = [flag for flag in emitted_flags(cmd) if flag not in text]
    assert not missing, f"Claude Code does not document: {missing}"


@pytest.mark.skipif(not shutil.which("codex"), reason="Codex CLI is not installed")
def test_codex_accepts_every_flag_we_emit(tmp_path, toy):
    text = help_text(["codex", "exec", "--help"])
    cmd = launch_command(toy, att.Options(harness="codex", model="gpt-5.6-sol", reasoning="high"), tmp_path, tmp_path, "codex")
    missing = [flag for flag in emitted_flags(cmd) if flag not in text]
    assert not missing, f"codex exec does not document: {missing}"


@pytest.mark.skipif(not opencode_bin(), reason="OpenCode CLI is not installed")
def test_opencode_accepts_every_flag_we_emit(tmp_path, toy):
    binary = opencode_bin()
    text = help_text([binary, "run", "--help"]) + help_text([binary, "--help"])
    cmd = launch_command(toy, att.Options(harness="opencode", model="openrouter/moonshotai/kimi-k3", reasoning="high"),
                         tmp_path, tmp_path, binary)
    missing = [flag for flag in emitted_flags(cmd) if flag not in text]
    assert not missing, f"opencode run does not document: {missing}"
