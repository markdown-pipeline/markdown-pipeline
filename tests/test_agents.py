"""MD-120: connect the person's coding agent in one click. Detection, the exact change, connect, disconnect, AGENTS.md rules.

The agent CLIs are tiny fake executables on a private PATH: they record their arguments, so the tests see the real command that would run.
"""
import json
import os
import stat
import sys

import pytest

from mdpipeline import agents


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    """A private PATH holding only what a test installs; calls of the fake agents are recorded in calls.jsonl."""
    d = tmp_path / "fakebin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    return d


def install(bin_dir, name, exit_code=0, stderr=""):
    """Put a fake `claude` or `codex` on the PATH: it appends its argv to calls.jsonl and exits with exit_code."""
    log = bin_dir.parent / "calls.jsonl"
    script = bin_dir / name
    script.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps([{name!r}] + sys.argv[1:]) + '\\n')\n"
        f"sys.stderr.write({stderr!r})\nsys.exit({exit_code})\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return log


def calls(bin_dir):
    log = bin_dir.parent / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# detection -----------------------------------------------------------------------------------------------------
def test_nothing_is_found_when_neither_agent_is_installed(bin_dir):
    assert agents.detect() == []


def test_claude_code_and_codex_are_found_on_the_path_claude_first(bin_dir):
    install(bin_dir, "codex")
    install(bin_dir, "claude")
    assert agents.detect() == ["claude_code", "codex"]


def test_only_codex_is_found_when_only_codex_is_installed(bin_dir):
    install(bin_dir, "codex")
    assert agents.detect() == ["codex"]


# the exact change, shown before the click ---------------------------------------------------------------------
@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "out" / "acme-jira"
    f.mkdir(parents=True)
    return f


def test_the_exact_change_for_claude_code_is_the_command_and_the_new_file(brain, folder):
    lines = agents.exact_change(brain, "claude_code", str(folder))
    assert lines[0].startswith("claude mcp add --scope user mdpipeline -- ")
    assert f"--brain {brain.root}" in lines[0] and lines[0].endswith(" mcp")
    assert lines[1] == f"# new file {folder / 'AGENTS.md'}"


def test_the_exact_change_for_codex_uses_codex_mcp_add(brain, folder):
    assert agents.exact_change(brain, "codex", str(folder))[0].startswith("codex mcp add mdpipeline -- ")


def test_the_exact_change_names_the_replaced_file_when_the_folder_already_has_an_agents_file(brain, folder):
    (folder / "AGENTS.md").write_text("mine")
    assert agents.exact_change(brain, "claude_code", str(folder))[1] == f"# AGENTS.md already exists in {folder}: yours is kept unless you choose otherwise"


# connect ---------------------------------------------------------------------------------------------------------
def test_connect_runs_the_agents_own_command_and_writes_agents_md(bin_dir, brain, folder):
    install(bin_dir, "claude")
    res = agents.connect(brain, "claude_code", str(folder))
    assert res["ok"] is True and res["agent"] == "claude_code"
    [call] = calls(bin_dir)
    assert call[:6] == ["claude", "mcp", "add", "--scope", "user", "mdpipeline"] and call[6] == "--" and call[-1] == "mcp"
    assert (folder / "AGENTS.md").exists()


def test_connect_reports_what_changed_after_the_click(bin_dir, brain, folder):
    install(bin_dir, "claude")
    res = agents.connect(brain, "claude_code", str(folder))
    assert res["changes"] == agents.exact_change(brain, "claude_code", str(folder))[:1] + [f"created {folder / 'AGENTS.md'}"]


def test_connect_does_nothing_for_an_agent_that_is_not_installed(bin_dir, brain, folder):
    res = agents.connect(brain, "claude_code", str(folder))
    assert res["ok"] is False and "isn't installed" in res["error"]
    assert not (folder / "AGENTS.md").exists()


def test_a_failing_agent_command_stops_before_any_file_is_written_and_says_why(bin_dir, brain, folder):
    install(bin_dir, "claude", exit_code=1, stderr="permission denied")
    res = agents.connect(brain, "claude_code", str(folder))
    assert res["ok"] is False and "permission denied" in res["error"]
    assert not (folder / "AGENTS.md").exists() and agents.connected() == {}


def test_connect_is_remembered_so_it_can_be_undone(bin_dir, brain, folder):
    install(bin_dir, "claude")
    agents.connect(brain, "claude_code", str(folder))
    assert list(agents.connected()) == ["claude_code"]


def test_an_unknown_agent_is_refused(bin_dir, brain, folder):
    with pytest.raises(ValueError):
        agents.connect(brain, "emacs", str(folder))


# AGENTS.md is never overwritten without asking ------------------------------------------------------------------
def test_an_existing_agents_md_is_never_overwritten_and_nothing_is_registered_until_the_person_chooses(bin_dir, brain, folder):
    install(bin_dir, "claude")
    (folder / "AGENTS.md").write_text("mine")
    res = agents.connect(brain, "claude_code", str(folder))
    assert res["ok"] is False and res["conflict"] == {"file": "AGENTS.md", "alternative": "MARKDOWN-PIPELINE.md"}
    assert (folder / "AGENTS.md").read_text() == "mine" and calls(bin_dir) == []


def test_keeping_the_existing_file_adds_ours_under_another_name(bin_dir, brain, folder):
    install(bin_dir, "claude")
    (folder / "AGENTS.md").write_text("mine")
    res = agents.connect(brain, "claude_code", str(folder), agents_md="keep")
    assert res["ok"] is True
    assert (folder / "AGENTS.md").read_text() == "mine" and (folder / "MARKDOWN-PIPELINE.md").exists()


def test_replacing_keeps_a_copy_of_the_original_so_nothing_is_lost(bin_dir, brain, folder):
    install(bin_dir, "claude")
    (folder / "AGENTS.md").write_text("mine")
    agents.connect(brain, "claude_code", str(folder), agents_md="replace")
    assert "Markdown Pipeline" in (folder / "AGENTS.md").read_text()
    assert (folder / "AGENTS.md.before-markdown-pipeline").read_text() == "mine"


def test_our_own_agents_md_is_refreshed_without_asking(bin_dir, brain, folder):
    install(bin_dir, "claude")
    agents.connect(brain, "claude_code", str(folder))
    res = agents.connect(brain, "claude_code", str(folder))
    assert res["ok"] is True and "conflict" not in res


def test_agents_md_says_what_the_files_are_the_provenance_fields_and_how_to_search(bin_dir, brain, folder):
    install(bin_dir, "claude")
    agents.connect(brain, "claude_code", str(folder))
    text = (folder / "AGENTS.md").read_text()
    for needle in ("provenance", "source_path", "synced_at", "search", "read", "list"):
        assert needle in text, needle


# disconnect ------------------------------------------------------------------------------------------------------
def test_disconnect_removes_the_registration_and_the_file_it_created(bin_dir, brain, folder):
    install(bin_dir, "claude")
    agents.connect(brain, "claude_code", str(folder))
    res = agents.disconnect(brain, "claude_code")
    assert res["ok"] is True and calls(bin_dir)[-1] == ["claude", "mcp", "remove", "--scope", "user", "mdpipeline"]
    assert not (folder / "AGENTS.md").exists() and agents.connected() == {}


def test_disconnect_restores_the_file_that_was_replaced(bin_dir, brain, folder):
    install(bin_dir, "claude")
    (folder / "AGENTS.md").write_text("mine")
    agents.connect(brain, "claude_code", str(folder), agents_md="replace")
    agents.disconnect(brain, "claude_code")
    assert (folder / "AGENTS.md").read_text() == "mine" and not (folder / "AGENTS.md.before-markdown-pipeline").exists()


def test_disconnect_leaves_a_file_the_person_edited_since(bin_dir, brain, folder):
    install(bin_dir, "claude")
    agents.connect(brain, "claude_code", str(folder))
    (folder / "AGENTS.md").write_text("# my own notes now\n")
    agents.disconnect(brain, "claude_code")
    assert (folder / "AGENTS.md").read_text() == "# my own notes now\n"


def test_disconnect_never_touches_a_file_that_was_not_ours(bin_dir, brain, folder):
    install(bin_dir, "claude")
    (folder / "AGENTS.md").write_text("mine")
    agents.connect(brain, "claude_code", str(folder), agents_md="keep")
    agents.disconnect(brain, "claude_code")
    assert (folder / "AGENTS.md").read_text() == "mine" and not (folder / "MARKDOWN-PIPELINE.md").exists()


def test_disconnect_of_an_agent_that_was_never_connected_is_a_quiet_no_op(bin_dir, brain):
    install(bin_dir, "claude")
    assert agents.disconnect(brain, "claude_code") == {"ok": True, "agent": "claude_code", "changes": []}
    assert calls(bin_dir) == []


def test_a_failed_removal_keeps_the_record_so_the_person_can_try_again(bin_dir, brain, folder):
    install(bin_dir, "claude")
    agents.connect(brain, "claude_code", str(folder))
    install(bin_dir, "claude", exit_code=1, stderr="boom")
    res = agents.disconnect(brain, "claude_code")
    assert res["ok"] is False and "boom" in res["error"] and "claude_code" in agents.connected()
