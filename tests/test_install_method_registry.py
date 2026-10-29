"""MD-197 (registry R2): installs through the MCP Registry report `registry`, a uvx run reports `uvx`, and the choice survives to a later `start`."""
import json
import os
import sys
from pathlib import Path

import pytest

from mdpipeline import install_info, usage
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import INSTALL_METHOD


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD", raising=False)


# AC4: every method value is accepted by the enum, and nothing else is
@pytest.mark.parametrize("method", ["script", "pip", "pipx", "uv", "uvx", "registry", "source", "other"])
def test_each_install_method_value_is_allowed(method):
    assert method in INSTALL_METHOD.values


@pytest.mark.parametrize("method", ["script", "pip", "pipx", "uv", "uvx", "registry", "source", "other"])
def test_each_install_method_value_can_be_said_by_the_environment(method, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", method)
    assert install_info.install_method(prefix="/usr/lib/python3.12", source_checkout=False) == method


# AC2: a uvx run is not "pip"
@pytest.mark.parametrize("prefix", ["/home/a/.cache/uv/archive-v0/M0caAnbdjkuSMmV692Iqo", "C:\\Users\\a\\AppData\\Local\\uv\\cache\\archive-v0\\abc123",
                                    "/Users/a/Library/Caches/uv/archive-v0/xyz"])
def test_a_uvx_environment_is_uvx(prefix):
    assert install_info.install_method(prefix=prefix, source_checkout=False) == "uvx"


def test_a_plain_pip_install_is_still_pip():
    assert install_info.install_method(prefix="/usr/lib/python3.12", source_checkout=False) == "pip"


# AC1: the Registry's entry says `registry`; the environment variable is what server.json sets
def test_the_registry_entry_beats_the_uvx_location(monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "registry")
    assert install_info.install_method(prefix="/home/a/.cache/uv/archive-v0/abc", source_checkout=False) == "registry"


def test_a_registry_start_is_remembered_for_a_later_start_without_the_variable(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "registry")
    assert install_info.remember_registry_install() is True
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD")
    assert install_info.install_method(prefix="/home/a/.cache/uv/archive-v0/abc", source_checkout=False) == "registry"


def test_an_existing_marker_is_never_overwritten_by_a_registry_start(app_data, monkeypatch):
    install_info.record_install_method("script")
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "registry")
    assert install_info.remember_registry_install() is False
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD")
    assert install_info.install_method(prefix="/x", source_checkout=False) == "script"


def test_without_the_registry_variable_nothing_is_remembered(app_data):
    assert install_info.remember_registry_install() is False
    assert not (install_info.data_dir() / install_info.MARKER).exists()


# AC1 end to end: the MCP server started by the Registry entry leaves the marker; a later `start` and its consent report `registry`
def test_the_mcp_server_started_by_the_registry_records_it(tmp_path):
    import asyncio
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters

    data = tmp_path / "data"
    env = {**os.environ, "MDPIPELINE_INSTALL_METHOD": "registry", "MDPIPELINE_DATA_DIR": str(data)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mdpipeline", "mcp"], env=env, cwd=str(tmp_path))

    async def go():
        async with Client(params) as c:
            await c.list_tools()

    asyncio.run(go())
    assert (data / install_info.MARKER).read_text().strip() == "registry"


# AC3: nothing is sent before consent; after the consent in the UI/CLI the install is reported with the remembered method
def test_nothing_is_reported_before_consent(app_data):
    install_info.record_install_method("registry")
    assert usage.report_install_once() is False


def test_after_consent_the_install_is_reported_as_registry(app_data):
    install_info.record_install_method("registry")
    UsageStore().set_consent("share")
    assert usage.report_install_once() is True
    queue = json.loads((usage.data_dir() / "usage.json").read_text())["queue"]
    assert [e["properties"]["install_method"] for e in queue if e["event"] == "install_completed"] == ["registry"]


# lifecycle: the update/uninstall commands know the new values
def test_update_and_uninstall_commands_exist_for_uvx_and_registry():
    from mdpipeline import lifecycle

    for method in ("uvx", "registry"):
        assert lifecycle.update_command(method) is None or isinstance(lifecycle.update_command(method), list)
        assert "mdpipeline" in lifecycle.uninstall_command(method)


# --- the argument a Registry entry can really set (server.json packageArguments have a fixed value; environment variables have none) -------------
def run_cli(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if k != "MDPIPELINE_INSTALL_METHOD"} | {"MDPIPELINE_DATA_DIR": str(tmp_path / "data")}
    import subprocess

    return subprocess.run([sys.executable, "-m", "mdpipeline", *args], capture_output=True, text=True, env=env, cwd=str(tmp_path))


def test_the_install_method_argument_is_hidden_from_help(tmp_path):
    assert "--install-method" not in run_cli(tmp_path, "--help").stdout


@pytest.mark.parametrize("method", ["registry", "uvx", "pip"])
def test_the_argument_sets_the_install_method_for_this_run(method, monkeypatch):
    from mdpipeline import cli

    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "placeholder")      # so that monkeypatch removes whatever apply_install_method sets, afterwards
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD")
    args = cli.build_parser().parse_args(["--install-method", method, "mcp"])
    cli.apply_install_method(args)
    assert install_info.install_method(prefix="/usr/lib/python3.12", source_checkout=False) == method


def test_an_unknown_method_is_refused_and_never_becomes_a_value(tmp_path):
    done = run_cli(tmp_path, "--install-method", "my secret path", "mcp")
    assert done.returncode == 2 and "my secret path" not in done.stdout


def test_without_the_argument_nothing_changes(monkeypatch):
    from mdpipeline import cli

    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "placeholder")
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD")
    cli.apply_install_method(cli.build_parser().parse_args(["mcp"]))
    assert "MDPIPELINE_INSTALL_METHOD" not in os.environ


def test_the_exact_command_a_registry_client_runs_leaves_the_registry_marker(tmp_path):
    """`uvx mdpipeline --install-method registry mcp`: no environment variable, only the argument server.json can fix."""
    import asyncio
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters

    data = tmp_path / "data"
    env = {k: v for k, v in os.environ.items() if k != "MDPIPELINE_INSTALL_METHOD"} | {"MDPIPELINE_DATA_DIR": str(data)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mdpipeline", "--install-method", "registry", "mcp"], env=env, cwd=str(tmp_path))

    async def go():
        async with Client(params) as c:
            await c.list_tools()

    asyncio.run(go())
    assert (data / install_info.MARKER).read_text().strip() == "registry"


# --- MD-196 AC8: the uvx uninstall words are true (marketing MM-147) -------------------------------------------------------------------
def test_the_uvx_uninstall_text_does_not_claim_that_uvx_installs_nothing_else():
    from mdpipeline import lifecycle

    for method in ("uvx", "registry"):
        text = lifecycle.uninstall_command(method)
        assert "installs nothing else" not in text and "uv cache clean mdpipeline" in text


def test_uvx_uninstall_turns_background_sync_off_and_says_so(tmp_path, monkeypatch):
    """`start` (through the first sync) turns background sync on, so `uvx mdpipeline uninstall` must turn it off and the output must say it did."""
    import subprocess

    env = {k: v for k, v in os.environ.items()} | {"MDPIPELINE_DATA_DIR": str(tmp_path / "data"), "MDPIPELINE_INSTALL_METHOD": "uvx",
                                                   "MDPIPELINE_NO_BACKGROUND_START": "1", "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, "-m", "mdpipeline", "uninstall"], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "Background sync is off and its login item is removed." in done.stdout
    assert "uv cache clean mdpipeline" in done.stdout and "installs nothing else" not in done.stdout


def test_the_install_troubleshooting_page_tells_a_uvx_user_how_to_turn_background_sync_off():
    page = (Path(__file__).resolve().parent.parent / "docs" / "troubleshooting-install.md").read_text(encoding="utf-8")
    assert "uvx mdpipeline uninstall" in page and "uv cache clean mdpipeline" in page
    assert "does not stop background sync" in page            # the AC7 finding: clearing uv's cache alone leaves the background process and its login item
