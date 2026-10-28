"""Ready-to-paste MCP configuration for agent clients, with this brain's path filled in.

The server is launched with the same Python interpreter that runs this program, so the snippet works
wherever mdpipeline is installed (virtualenv, ``uv tool``, ...) without needing it on PATH.
"""
from __future__ import annotations

import json
import shlex
import sys

from .brain import Brain

SERVER_NAME = "mdpipeline"


def _args(brain: Brain) -> list[str]:
    return ["-m", "mdpipeline", "--brain", str(brain.root), "mcp"]


def claude_code_command(brain: Brain) -> str:
    """One shell command that registers the server with Claude Code."""
    return shlex.join(["claude", "mcp", "add", SERVER_NAME, "--", sys.executable, *_args(brain)])


def claude_code_json(brain: Brain) -> str:
    """The ``.mcp.json`` entry (project scope) for Claude Code."""
    return json.dumps({"mcpServers": {SERVER_NAME: {"command": sys.executable, "args": _args(brain)}}}, indent=2)


def codex_toml(brain: Brain) -> str:
    """The ``~/.codex/config.toml`` entry for Codex."""
    args = ", ".join(json.dumps(a) for a in _args(brain))
    return f"[mcp_servers.{SERVER_NAME}]\ncommand = {json.dumps(sys.executable)}\nargs = [{args}]\n"


def all_snippets(brain: Brain) -> dict[str, str]:
    return {"claude_code_command": claude_code_command(brain), "claude_code_json": claude_code_json(brain),
            "codex_toml": codex_toml(brain)}
