"""MD-120 in a real browser: Ready -> Connect Claude Code -> the exact change -> the first question -> "read your files"; Settings -> Agents."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import agents, autostart, notices  # noqa: E402
from mdpipeline.log import ActivityLog  # noqa: E402
from test_agents import bin_dir, calls, install  # noqa: E402,F401
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def notes(make_source):
    return make_source("team-wiki", {"roadmap.md": "# Roadmap\nOpen decision: pricing.\n", "b.md": "# B\n"})


def write_files(page, notes, dest):
    page.get_by_role("button", name="Folder on this computer").click()
    page.get_by_label("Folder path").fill(str(notes))
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("textbox", name="Output folder").fill(str(dest))
    expect(page.locator("#fr-dest-msg")).to_contain_text("New folder")
    page.get_by_role("button", name="Use this folder").click()
    page.get_by_role("button", name="Write 2 files").click()
    expect(page.locator("#fr-ready")).to_contain_text("2 files in")


def test_ready_offers_the_found_agent_with_the_exact_change_before_any_click(page, brain, notes, tmp_path, bin_dir):
    install(bin_dir, "claude")
    install(bin_dir, "codex")
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        open_app(page, ui)
        write_files(page, notes, dest)
        card = page.locator(".ag-primary")
        expect(card).to_contain_text("Claude Code")
        expect(card.get_by_role("button", name="Connect Claude Code")).to_be_visible()
        card.get_by_text("Exact change").click()
        expect(card.locator(".ag-exact")).to_contain_text("claude mcp add --scope user mdpipeline -- ")
        expect(card.locator(".ag-exact")).to_contain_text(f"# new file {dest / 'AGENTS.md'}")
        expect(page.get_by_role("button", name="Use Codex instead")).to_be_visible()
        shot(page, "agents-ready-found")
    assert calls(bin_dir) == [] and not (dest / "AGENTS.md").exists()             # nothing changed before the click


def test_one_click_connects_shows_the_change_and_the_first_question_that_has_an_answer(page, brain, notes, tmp_path, bin_dir):
    install(bin_dir, "claude")
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        open_app(page, ui)
        write_files(page, notes, dest)
        page.get_by_role("button", name="Connect Claude Code").click()
        row = page.locator(".ag-on")
        expect(row).to_contain_text("Claude Code connected")
        expect(row.locator(".ag-exact")).to_contain_text("claude mcp add")
        expect(row.locator(".ag-exact")).to_contain_text(f"created {dest / 'AGENTS.md'}")
        expect(page.locator("#ag-ask")).to_contain_text("Summarize team-wiki and list the open decisions.")
        expect(page.locator("#ag-cmd")).to_contain_text("claude ")
        expect(page.locator(".ag-wait")).to_contain_text("Waiting for Claude Code to read your files")
        shot(page, "agents-connected")
        ActivityLog(brain).mcp_call("search")                          # the agent reads through the MCP server
        ActivityLog(brain).mcp_call("read")
        expect(page.locator(".ag-read")).to_contain_text("Claude Code read your files · 2 reads", timeout=8000)
    assert (dest / "AGENTS.md").exists() and calls(bin_dir)[0][:3] == ["claude", "mcp", "add"]


def test_a_question_that_would_be_empty_shows_the_fallback_and_its_reason(page, brain, make_source, tmp_path, bin_dir):
    install(bin_dir, "claude")
    plain = make_source("plain", {"a.md": "# A\nnothing\n", "b.md": "# B\n"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        write_files(page, plain, tmp_path / "out")
        page.get_by_role("button", name="Connect Claude Code").click()
        expect(page.locator("#ag-ask")).to_contain_text("Which files changed most recently, and what do they say?")
        expect(page.locator("#ag-reason")).to_contain_text("No file in plain mentions a decision")


def test_an_existing_agents_md_asks_first_and_changes_nothing_until_chosen(page, brain, notes, tmp_path, bin_dir):
    install(bin_dir, "claude")
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        open_app(page, ui)
        write_files(page, notes, dest)
        (dest / "AGENTS.md").write_text("mine")
        page.get_by_role("button", name="Connect Claude Code").click()
        expect(page.locator("#ag-conflict")).to_contain_text("AGENTS.md already exists in this folder")
        assert calls(bin_dir) == [] and (dest / "AGENTS.md").read_text() == "mine"
        page.get_by_role("button", name="Keep yours, add ours as MARKDOWN-PIPELINE.md").click()
        expect(page.locator(".ag-on")).to_contain_text("created " + str(dest / "MARKDOWN-PIPELINE.md"))
    assert (dest / "AGENTS.md").read_text() == "mine"


def test_without_a_found_agent_ready_shows_the_copy_paste_instructions_and_check_again(page, brain, notes, tmp_path, bin_dir):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        open_app(page, ui)
        write_files(page, notes, dest)
        panel = page.locator("#ag-manual")
        expect(panel).to_contain_text("We didn't find Claude Code or Codex on this computer")
        expect(panel.locator("#snip")).to_contain_text("claude mcp add")
        install(bin_dir, "claude")
        panel.get_by_role("button", name="Installed it just now? Check again").click()
        expect(page.get_by_role("button", name="Connect Claude Code")).to_be_visible()


def test_a_failing_agent_command_says_why_and_leaves_no_file(page, brain, notes, tmp_path, bin_dir):
    install(bin_dir, "claude", exit_code=1, stderr="config is read-only")
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        open_app(page, ui)
        write_files(page, notes, dest)
        page.get_by_role("button", name="Connect Claude Code").click()
        expect(page.locator("#ag-error")).to_contain_text("config is read-only")
    assert not (dest / "AGENTS.md").exists()


def test_settings_agents_disconnects_exactly_what_was_added(page, brain, notes, tmp_path, bin_dir):
    install(bin_dir, "claude")
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        open_app(page, ui)
        write_files(page, notes, dest)
        page.get_by_role("button", name="Connect Claude Code").click()
        expect(page.locator(".ag-on")).to_be_visible()
        page.get_by_role("button", name="Go to Home").click()
        page.get_by_role("button", name="Settings").click()
        section = page.locator("#ag-settings")
        expect(section).to_contain_text("Claude Code connected")
        shot(page, "agents-settings")
        section.get_by_role("button", name="Disconnect").click()
        expect(section.get_by_role("button", name="Connect Claude Code")).to_be_visible()
    assert calls(bin_dir)[-1] == ["claude", "mcp", "remove", "--scope", "user", "mdpipeline"]
    assert not (dest / "AGENTS.md").exists() and agents.connected() == {}


def test_settings_agents_before_any_files_says_to_write_files_first(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Settings").click()
        expect(page.locator("#ag-settings")).to_contain_text("Connect an agent after your first files are written.")
