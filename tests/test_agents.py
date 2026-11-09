"""MD-120: connect the person's coding agent in one click. Detection, the exact change, connect, disconnect, AGENTS.md rules.

The agent CLIs are tiny fake executables on a private PATH: they record their arguments, so the tests see the real command that would run.
"""
import shlex
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
    body = (f"import json, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps([{name!r}] + sys.argv[1:]) + '\\n')\n"
            f"sys.stderr.write({stderr!r})\nsys.exit({exit_code})\n")
    if sys.platform == "win32":
        # npm installs the agents as `<name>.cmd` on Windows; a shebang file without an extension is not runnable there
        (bin_dir / f"{name}.py").write_text(body)
        (bin_dir / f"{name}.cmd").write_text(f'@"{sys.executable}" "%~dp0{name}.py" %*\n')
        return log
    script = bin_dir / name
    script.write_text(f"#!{sys.executable}\n" + body)
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
    assert f"--brain {shlex.quote(str(brain.root))}" in lines[0] and lines[0].endswith(" mcp")
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


# MD-211: on Windows an npm-installed agent is `claude.cmd`: detection (shutil.which) sees it, but CreateProcess cannot start a bare `claude` ------------
def _fake_run(seen, returncode=0, stderr=""):
    def fake_run(command, **kwargs):
        seen["command"] = command
        return type("Done", (), {"returncode": returncode, "stdout": "", "stderr": stderr})()
    return fake_run


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_the_agent_command_runs_by_the_full_path_that_the_search_found(monkeypatch, agent):
    seen = {}
    monkeypatch.setattr(agents.shutil, "which", lambda name: rf"C:\npm\{name}.cmd" if name == agent else None)
    monkeypatch.setattr(agents.subprocess, "run", _fake_run(seen))
    assert agents._run([agent, "mcp", "list"]) == ""
    assert seen["command"] == [rf"C:\npm\{agent}.cmd", "mcp", "list"]


def test_a_native_exe_is_started_by_its_path_too_and_the_arguments_are_untouched(monkeypatch):
    seen = {}
    monkeypatch.setattr(agents.shutil, "which", lambda name: r"C:\Users\me\.local\bin\claude.exe")
    monkeypatch.setattr(agents.subprocess, "run", _fake_run(seen))
    agents._run(["claude", "mcp", "add", "--", "x y", "--brain", "b"])
    assert seen["command"] == [r"C:\Users\me\.local\bin\claude.exe", "mcp", "add", "--", "x y", "--brain", "b"]


def test_an_agent_that_is_not_installed_keeps_the_existing_message(monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)

    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(agents.subprocess, "run", missing)
    assert agents._run(["claude", "mcp", "list"]) == "claude isn't installed or isn't on the PATH"


def test_a_failure_names_the_agent_not_the_path(monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda name: r"C:\npm\claude.cmd")
    monkeypatch.setattr(agents.subprocess, "run", _fake_run({}, returncode=3))
    assert agents._run(["claude", "mcp", "list"]) == "claude exited with 3"


# MD-211 AC7: a `.cmd` agent is run through cmd.exe's batch parsing, so a brain path with a character it can misread must never reach it -------------
CMD_AGENT = r"C:\npm\claude.cmd"
UNSAFE_PATHS = [r"C:\Users\me\R&D brain", r"C:\100% notes", r"C:\a^b", r"C:\a|b", r"C:\a(b)", r"C:\a<b>", 'C:\\say "hi"', r"C:\hey!", "C:\\two\nlines"]


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_a_cmd_agent_is_never_started_with_a_path_that_cmd_could_misread(monkeypatch, path):
    ran = []
    monkeypatch.setattr(agents.shutil, "which", lambda name: CMD_AGENT)
    monkeypatch.setattr(agents.subprocess, "run", lambda *a, **k: ran.append(a))
    reason = agents._run(["claude", "mcp", "add", "--", "mdpipeline", "--brain", path])
    assert ran == [] and reason.startswith("Claude Code can't be connected to this folder: its path has a character Windows can't pass to it (")
    assert reason.endswith('Move or rename the folder so the path has none of & | < > ^ % ! ( ) ", or choose another.')


@pytest.mark.parametrize("path, shown", [(r"C:\R&D", "(&)"), (r"C:\100% a&b", "(&, %)"), ("C:\\two\nlines", "(a line break)"), ("C:\\x\r\n(y)", "((, ), a line break)")])
def test_the_refusal_names_every_bad_character_found(monkeypatch, path, shown):
    monkeypatch.setattr(agents.shutil, "which", lambda name: CMD_AGENT)
    assert shown in agents._run(["claude", "--brain", path]).split("Windows can't pass to it ", 1)[1].split(". ")[0]


def test_codex_is_named_Codex(monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda name: r"C:\npm\codex.cmd")
    assert agents._run(["codex", "--brain", r"C:\R&D"]).startswith("Codex can't be connected to this folder")


@pytest.mark.parametrize("agent_path", [CMD_AGENT, r"C:\npm\codex.BAT"])
def test_both_batch_suffixes_are_checked(monkeypatch, agent_path):
    ran = []
    monkeypatch.setattr(agents.shutil, "which", lambda name: agent_path)
    monkeypatch.setattr(agents.subprocess, "run", lambda *a, **k: ran.append(a))
    assert agents._run(["claude", "--brain", r"C:\R&D"]) != "" and ran == []


@pytest.mark.parametrize("path", [r"C:\Users\me\my brain", r"C:\Users\me\brain-1_x.y", r"D:\notes'n things"])
def test_a_cmd_agent_gets_a_plain_path_with_spaces_unchanged(monkeypatch, path):
    seen = {}
    monkeypatch.setattr(agents.shutil, "which", lambda name: CMD_AGENT)
    monkeypatch.setattr(agents.subprocess, "run", _fake_run(seen))
    assert agents._run(["claude", "mcp", "add", "--brain", path]) == ""
    assert seen["command"] == [CMD_AGENT, "mcp", "add", "--brain", path]


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_a_native_exe_still_gets_any_path_unchanged(monkeypatch, path):
    seen = {}
    monkeypatch.setattr(agents.shutil, "which", lambda name: r"C:\Users\me\.local\bin\claude.exe")
    monkeypatch.setattr(agents.subprocess, "run", _fake_run(seen))
    assert agents._run(["claude", "--brain", path]) == "" and seen["command"][-1] == path


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe's real parsing: runs on the Windows runner")
@pytest.mark.parametrize("path", ["my brain", "plain", "it's here"])
def test_on_windows_a_real_cmd_file_receives_the_arguments_unchanged(tmp_path, monkeypatch, path):
    echo, out = tmp_path / "echoer.cmd", tmp_path / "args.txt"
    echo.write_text(f'@echo off\r\n(echo [%~1]& echo [%~2])> "{out}"\r\n', encoding="utf-8")
    monkeypatch.setattr(agents.shutil, "which", lambda name: str(echo))
    assert agents._run(["echoer", "first", path]) == ""
    assert out.read_text().split() == ["[first]", *f"[{path}]".split()]
