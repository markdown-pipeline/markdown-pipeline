"""MD-196 (registry R1): `mdpipeline mcp` before any output folder exists is a working MCP server that says how to finish setup, never exit 2."""
import asyncio
import json
import os
import sys

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import write
from mdpipeline import mcp_server, usage
from mdpipeline.brain import init_brain
from mdpipeline.schedulestore import register_brain
from mdpipeline.usage import UsageStore

TOOLS = {"search", "read", "list", "recent_changes", "sync_status", "sync"}
ARGS = {"search": {"query": "pricing"}, "read": {"path": "raw/a.md"}, "list": {}, "recent_changes": {}, "sync_status": {}, "sync": {}}


def stdio_params(cwd, *extra):
    return StdioServerParameters(command=sys.executable, args=["-m", "mdpipeline", *extra, "mcp"], env=dict(os.environ), cwd=str(cwd))


def run(coro):
    return asyncio.run(coro)


def text_of(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content) if result.content else json.dumps(result.structured_content)


@pytest.fixture
def nowhere(tmp_path):
    folder = tmp_path / "somewhere-else"
    folder.mkdir()
    return folder                                                    # a working directory with no output folder at or above it


# --- AC1: the handshake works with no output folder ------------------------------------------------------------------------
def test_with_no_output_folder_the_server_starts_and_lists_all_its_tools_over_stdio(nowhere):
    async def go():
        async with Client(stdio_params(nowhere)) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert run(go()) == TOOLS


def test_an_explicit_brain_that_is_not_set_up_also_starts_in_setup_mode(nowhere):
    async def go():
        async with Client(stdio_params(nowhere, "--brain", str(nowhere / "missing"))) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert run(go()) == TOOLS


# --- AC2: every tool answers with one message and one next step -------------------------------------------------------------
@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_tool_in_setup_mode_returns_one_message_naming_start(nowhere, tool):
    async def go():
        async with Client(stdio_params(nowhere)) as c:
            return await c.call_tool(tool, ARGS[tool])

    result = run(go())
    text = text_of(result)
    assert not result.is_error and "setup_needed" in text and "mdpipeline start" in text
    assert "init" not in text.lower().replace("initial", "")        # the old error told people to run `init`, a command they never need


def test_the_setup_message_is_a_single_next_step_and_leaks_no_path(nowhere):
    message = mcp_server.setup_message("uv")
    assert message.count("`mdpipeline start`") == 1 and str(nowhere) not in message and "/home/" not in message


# --- MD-196 (marketing MM-147, comment 11073): ONE command, chosen by the install method; the platform is named -----------------------
@pytest.mark.parametrize("method", ["uvx", "registry"])
def test_a_uvx_or_registry_user_sees_only_the_uvx_command(method):
    message = mcp_server.setup_message(method)
    assert message.count("`uvx mdpipeline start`") == 1 and message.count("mdpipeline start") == 1


@pytest.mark.parametrize("method", ["uv", "script", "pipx", "pip", "source", "other"])
def test_every_other_install_sees_only_the_plain_command(method):
    message = mcp_server.setup_message(method)
    assert message.count("`mdpipeline start`") == 1 and "uvx" not in message


@pytest.mark.parametrize("method", ["uvx", "uv"])
def test_the_setup_message_says_it_runs_on_linux_for_now(method):
    assert "It runs on Linux for now." in mcp_server.setup_message(method)


def test_the_setup_message_is_marketings_approved_text():
    assert mcp_server.setup_message("uvx") == (
        "Markdown Pipeline is installed but not set up yet, so there are no files to read. It runs on Linux for now. "
        "To finish, run `uvx mdpipeline start` in a terminal. It opens the app in your browser, where you choose a source and where the files go. "
        "When the first sync is done, ask again; no restart is needed.")


def test_the_server_uses_the_runtime_install_method_in_its_message(nowhere, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "registry")

    async def go():
        async with Client(stdio_params(nowhere)) as c:
            return text_of(await c.call_tool("list", {}))

    text = run(go())
    assert "`uvx mdpipeline start`" in text and "`mdpipeline start`" not in text


# --- AC3: after setup the next call works, no restart --------------------------------------------------------------------------
def test_after_the_person_finishes_setup_the_next_call_in_the_same_session_works(nowhere, tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()

    async def go():
        async with Client(stdio_params(nowhere)) as c:
            before = text_of(await c.call_tool("list", {}))
            brain = init_brain(folder)                                 # what `mdpipeline start` does, then the first sync registers the folder
            write(brain.root / "raw/p/pricing.md", "---\nprovenance:\n  pipeline: p\n  source_path: pricing.md\n---\n# Pricing\nusage based pricing\n")
            register_brain(brain)
            after = await c.call_tool("list", {})
            return before, after

    before, after = run(go())
    assert "setup_needed" in before
    assert not after.is_error and "setup_needed" not in text_of(after) and "raw" in text_of(after)


def test_two_registered_output_folders_ask_for_the_brain_option_not_a_guess(nowhere, tmp_path):
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        register_brain(init_brain(tmp_path / name))

    async def go():
        async with Client(stdio_params(nowhere)) as c:
            return text_of(await c.call_tool("list", {}))

    text = run(go())
    assert "setup_needed" in text and "--brain" in text


def test_a_registered_folder_that_was_deleted_is_setup_mode_not_a_crash(nowhere, tmp_path):
    (tmp_path / "gone").mkdir()
    register_brain(init_brain(tmp_path / "gone"))
    (tmp_path / "gone" / ".mdpipeline" / "config.yaml").unlink()

    async def go():
        async with Client(stdio_params(nowhere)) as c:
            return text_of(await c.call_tool("sync_status", {}))

    assert "setup_needed" in run(go())


def test_the_one_registered_output_folder_is_used_when_the_client_started_the_server_elsewhere(nowhere, tmp_path):
    (tmp_path / "only").mkdir()
    brain = init_brain(tmp_path / "only")
    write(brain.root / "wiki/x.md", "# X\n")
    register_brain(brain)

    async def go():
        async with Client(stdio_params(nowhere)) as c:
            return text_of(await c.call_tool("list", {}))

    assert "setup_needed" not in run(go())


# --- AC5: with a brain nothing changes (the existing tests in test_mcp_server.py stay green); and the cwd lookup still wins ---------------
def test_a_brain_at_the_working_directory_wins_over_the_registry(tmp_path):
    here, other = tmp_path / "here", tmp_path / "other"
    here.mkdir(), other.mkdir()
    write(init_brain(here).root / "wiki/here.md", "# Here\n")
    register_brain(init_brain(other))

    async def go():
        async with Client(stdio_params(here)) as c:
            return text_of(await c.call_tool("list", {"path_prefix": "wiki"}))

    assert "here.md" in run(go())


# --- AC6: counted, only with consent, once per server process ------------------------------------------------------------------
def queued(name):
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e["properties"] for e in entries if e["event"] == name]


def test_setup_mode_is_counted_once_per_process_as_a_notice_when_sharing(nowhere):
    UsageStore().set_consent("share")
    server = mcp_server.build_server(mcp_server.brain_provider(None, lambda: str(nowhere)))

    async def go():
        async with Client(server) as c:
            for _ in range(3):
                await c.call_tool("list", {})

    run(go())
    shown = [p for p in queued("notice_shown") if p.get("kind") == "setup_mode"]
    assert len(shown) == 1 and "surface" not in shown[0]


def test_setup_mode_sends_nothing_without_consent(nowhere):
    server = mcp_server.build_server(mcp_server.brain_provider(None, lambda: str(nowhere)))

    async def go():
        async with Client(server) as c:
            await c.call_tool("list", {})

    run(go())
    usage_file = usage.data_dir() / "usage.json"
    assert not usage_file.exists() or not [p for p in queued("notice_shown") if p.get("kind") == "setup_mode"]
