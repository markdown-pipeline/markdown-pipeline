"""MD-24: the exact MCP config snippet for Claude Code and Codex, with the brain path filled in."""
import os
import asyncio
import json
import shlex
import tomllib

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import write
from mdpipeline.cli import main
from mdpipeline.snippets import claude_code_json, claude_code_command, codex_toml


def test_claude_code_json_snippet_contains_the_brain_path(brain):
    data = json.loads(claude_code_json(brain))
    server = data["mcpServers"]["mdpipeline"]
    assert str(brain.root) in server["args"] and server["args"][-1] == "mcp"


def test_codex_toml_snippet_parses_and_contains_the_brain_path(brain):
    data = tomllib.loads(codex_toml(brain))
    server = data["mcp_servers"]["mdpipeline"]
    assert str(brain.root) in server["args"] and server["args"][-1] == "mcp"


def test_claude_code_command_is_a_ready_to_paste_cli_call(brain):
    parts = shlex.split(claude_code_command(brain))
    assert parts[:3] == ["claude", "mcp", "add"] and str(brain.root) in parts and parts[-1] == "mcp"


def test_cli_connect_prints_both_snippets(brain, capsys):
    code = main(["--brain", str(brain.root), "connect"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Claude Code" in out and "Codex" in out and out.count(str(brain.root)) >= 3
    assert "claude mcp add" in out and "[mcp_servers.mdpipeline]" in out


def test_cli_connect_for_one_agent(brain, capsys):
    main(["--brain", str(brain.root), "connect", "--agent", "codex"])
    out = capsys.readouterr().out
    assert "[mcp_servers.mdpipeline]" in out and "claude mcp add" not in out


def _search_via(command: str, args: list[str]):
    async def go():
        async with Client(StdioServerParameters(command=command, args=args, env=dict(os.environ))) as c:
            res = await c.call_tool("search", {"query": "pricing"})
            return res

    return asyncio.run(go())


def test_following_the_claude_code_snippet_makes_search_work_on_the_first_try(brain, brain_dir):
    write(brain_dir / "raw/p/pricing.md", "# Pricing\nusage based pricing\n")
    server = json.loads(claude_code_json(brain))["mcpServers"]["mdpipeline"]
    res = _search_via(server["command"], server["args"])
    assert not res.is_error and "raw/p/pricing.md" in json.dumps(res.structured_content or res.content[0].text)


def test_following_the_codex_snippet_makes_search_work_on_the_first_try(brain, brain_dir):
    write(brain_dir / "raw/p/pricing.md", "# Pricing\nusage based pricing\n")
    server = tomllib.loads(codex_toml(brain))["mcp_servers"]["mdpipeline"]
    res = _search_via(server["command"], server["args"])
    assert not res.is_error and "raw/p/pricing.md" in json.dumps(res.structured_content or res.content[0].text)
