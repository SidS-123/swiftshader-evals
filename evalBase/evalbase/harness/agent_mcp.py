"""Absolute-path entry point for CLI-hosted MCP clients.

The CLIs are given
this file's absolute path, so the bridge starts from any working directory
with evalbase importable from beside it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evalbase.harness.mcp_server import main  # noqa: E402

main()
