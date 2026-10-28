"""The UI in a real browser (Playwright + Chromium) against the real server and a real brain.

Set MDP_SHOTS=/some/dir to also save screenshots of every screen state."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from conftest import write  # noqa: E402
from mdpipeline.config import Pipeline, Source, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from mdpipeline.log import ActivityLog  # noqa: E402
from uihelper import running_ui  # noqa: E402

SHOTS = os.environ.get("MDP_SHOTS")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # no browser installed
            pytest.skip(f"no Chromium available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    # script errors must never happen; HTTP 4xx responses (blocked/validation) are expected app flows
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.errors = errors
    yield pg
    ctx.close()
    assert errors == [], errors


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def open_activity(page):
    """Click the Activity tab. Not ``get_by_role("button", name="Activity")``: that is a substring match, and once a card on screen says
    "See all in Activity" or a "needs you" pill that opens it, it matches two buttons and Playwright refuses to click (MD-169 flaky test)."""
    page.get_by_role("navigation", name="Screens").locator('[data-screen="activity"]').click()


def open_app(page, ui, screen=None):
    page.goto(ui.base)
    expect(page.locator("#main")).not_to_be_empty()
    if screen == "activity":
        open_activity(page)
        expect(page.locator("h2").first).to_be_visible()


@pytest.fixture
def two(brain, add_pipeline, make_source):
    a = make_source("raw", {"1.md": "one", "2.md": "two"})
    b = make_source("wiki", {"index.md": "idx", "concepts/c.md": "c"})
    c = make_source("single", {"s.md": "s"})
    add_pipeline(Pipeline.new("biz", [Source(str(a), "raw"), Source(str(b), "wiki")]))
    add_pipeline(Pipeline.new("solo", [str(c)], target="raw/projects/solo/{relpath}"))
    return brain


def test_empty_state_is_the_guided_start_with_advanced_setup_one_click_away(page, brain, brain_dir):
    write(brain_dir / "raw/n.md", "x")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.get_by_role("heading", name="Connect your first source")).to_be_visible()
        expect(page.get_by_role("button", name="Advanced setup")).to_be_visible()
        # MD-145: before the first sync the header shows neither the brain path nor "no pipelines yet"
        expect(page.locator(".brainpath")).to_have_count(0)
        expect(page.locator("#summary")).not_to_contain_text("no pipelines yet")
        shot(page, "01-empty")


def test_the_header_shows_the_brain_path_only_after_a_pipeline_has_synced(page, two):
    from mdpipeline.engine import sync_pipeline
    with running_ui(two) as ui:
        open_app(page, ui)
        expect(page.locator(".brainpath")).to_have_count(0)
        sync_pipeline(two, "solo", trigger="ui")
        page.reload()
        expect(page.locator(".brainpath")).to_contain_text(str(two.root))


def test_home_lists_pipelines_with_folders_target_status_and_syncs_from_the_ui(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        biz = page.locator("li.prow", has_text="biz")
        expect(biz).to_contain_text("2 folders: raw, wiki")
        expect(biz).to_contain_text("raw/{pipeline}/{source}/{relpath}")
        expect(biz).to_contain_text("not synced yet")
        expect(page.locator("li.prow", has_text="solo")).to_contain_text("/single")
        biz.get_by_role("button", name="Sync now").click()
        expect(biz).to_contain_text("ok")
        expect(biz).to_contain_text("4 created")
        expect(page.locator("#toast")).to_contain_text("biz synced")
        assert (two.root / "raw/biz/wiki/concepts/c.md").exists()
        assert ActivityLog(two).read(types={"run"})[-1]["trigger"] == "ui"
        shot(page, "02-home")
        page.get_by_role("button", name="Sync all").click()
        expect(page.locator("#toast")).to_contain_text("Synced 2 pipelines")
        assert {r["pipeline"] for r in ActivityLog(two).read(types={"run"}) if r["trigger"] == "ui"} == {"biz", "solo"}


def test_pause_and_resume_buttons(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        solo = page.locator("li.prow", has_text="solo")
        solo.get_by_role("button", name="Pause").click()
        expect(solo).to_contain_text("paused")
        expect(solo.get_by_role("button", name="Sync now")).to_be_disabled()
        expect(page.get_by_role("button", name="Sync all")).to_contain_text("1 active")
        shot(page, "03-paused")
        solo.get_by_role("button", name="Resume").click()
        expect(solo.get_by_role("button", name="Sync now")).to_be_enabled()


def show_manual_agent_command(page):
    """MD-147: on Home the raw command sits under 'Another agent' when an agent was found; open it if it is folded."""
    expect(page.locator("#ag-panel")).to_be_visible()
    folded = page.get_by_text("Another agent (Cursor, …)")
    if folded.count():
        folded.click()


def add_source(page, kind="Folder"):
    """MD-99: a new pipeline has no source row; the 'Add source' menu offers Folder and Jira."""
    page.get_by_text("+ Add source").click()
    page.get_by_role("group", name="Kind of source").get_by_role("button", name=kind).click()


def test_editor_new_pipeline_with_two_folders_live_preview_and_save_and_sync(page, brain, make_source):
    a = make_source("proj/raw", {"a1.md": "1", "a2.md": "2"})
    b = make_source("proj/wiki", {"index.md": "i"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.locator("#f-policy").select_option("original")      # new pipelines default to date-name (see below)
        page.get_by_label("Folder 1 path").fill(str(a))
        expect(page.locator("#m-src0")).to_contain_text("Folder found · 2 .md files")
        expect(page.get_by_label("Folder 1 label")).to_have_value("raw")
        expect(page.locator("#f-name")).to_have_value("raw")          # suggested from the folder name
        add_source(page)
        page.get_by_label("Folder 2 path").fill(str(b))
        expect(page.locator("#f-name")).to_have_value("proj")         # suggested from the common parent
        expect(page.locator("#pv")).to_contain_text("create")
        expect(page.locator("#pv")).to_contain_text("raw/index.md")    # MD-140: raw/ by default
        expect(page.locator("#pv .counts")).to_contain_text("3 all")
        expect(page.locator("#ex")).to_contain_text("→ raw/a1.md")
        shot(page, "04-editor-preview")
        page.get_by_role("button", name="create", exact=False).first.click()   # the count pill filters
        page.get_by_role("button", name="Save & sync").click()
        expect(page.locator("#toast")).to_contain_text("proj saved")
        expect(page.locator("li.prow", has_text="proj")).to_contain_text("3 created")
        assert (brain.root / "raw" / "a1.md").exists() and (brain.root / "raw" / "index.md").exists()
        assert load_config(brain).get("proj") is not None


def test_editor_chip_inserts_a_variable_and_template_errors_block_save(page, brain, make_source):
    a = make_source("p", {"a.md": "1"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.get_by_label("Folder 1 path").fill(str(a))
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()
        t = page.locator("#f-template")
        t.fill("raw/../wiki/{relpath}")
        expect(page.locator("#m-target")).to_contain_text("'..'")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Save & sync")).to_be_disabled()
        expect(page.locator("#pv")).to_contain_text("Fix the target path to see the preview")
        shot(page, "05-editor-outside-raw")
        t.fill("raw/{pipeline}/")
        t.press("End")
        page.get_by_role("button", name="{relpath}").click()
        expect(t).to_have_value("raw/{pipeline}/{relpath}")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()


def test_editor_missing_folder_warns_but_allows_save_and_zero_matches_disable_save_and_sync(page, brain, tmp_path, make_source):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.get_by_label("Folder 1 path").fill(str(tmp_path / "unmounted"))
        expect(page.locator("#m-src0")).to_contain_text("Folder not found")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()
        expect(page.get_by_role("button", name="Save & sync")).to_be_disabled()
        shot(page, "06-editor-missing")
        empty = make_source("empty", {"readme.txt": "x"})
        page.get_by_label("Folder 1 path").fill(str(empty))
        expect(page.locator("#pv")).to_contain_text("0 files match these rules")
        expect(page.get_by_role("button", name="Save & sync")).to_be_disabled()


def test_editor_shows_validation_next_to_the_offending_row(page, brain, brain_dir, make_source):
    a = make_source("p", {"inner/x.md": "1"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.get_by_label("Folder 1 path").fill(str(a))
        add_source(page)
        page.get_by_label("Folder 2 path").fill(str(a / "inner"))
        expect(page.locator("#m-src1")).to_contain_text("nested")
        page.get_by_label("Folder 2 path").fill(str(brain_dir / "raw"))
        expect(page.locator("#m-src1")).to_contain_text("inside the folder the files are written to")
        page.get_by_label("Folder 2 path").fill(str(brain_dir.parent))
        expect(page.locator("#m-src1")).to_contain_text("contains your output folder")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        shot(page, "07-editor-inside-brain")


def test_editor_file_name_policy_defaults_to_date_name_and_previews_the_names(page, brain, make_source):
    from datetime import datetime, timezone
    from mdpipeline.naming import SEPARATOR
    today = datetime.now(timezone.utc).date().isoformat()
    a = make_source("proj/docs", {"Meeting Notes.md": "1"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.get_by_label("Folder 1 path").fill(str(a))
        expect(page.locator("#f-policy")).to_have_value("date-name")
        expect(page.locator("#pv")).to_contain_text(f"{today}{SEPARATOR}meeting-notes.md")
        expect(page.locator("#f-pattern")).to_be_hidden()
        page.locator("#f-policy").select_option("custom")
        expect(page.locator("#f-pattern")).to_be_visible()
        page.locator("#f-pattern").fill("{date}")
        expect(page.locator("#m-name_policy")).to_contain_text("{slug} or {name}")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        page.locator("#f-pattern").fill("{slug}.{date}")
        expect(page.locator("#pv")).to_contain_text(f"meeting-notes.{today}.md")
        shot(page, "04b-editor-name-policy")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("saved")
    assert load_config(brain).pipelines[0].name_pattern == "{slug}.{date}"


def test_editor_collision_banner_and_fix_with_the_source_chip(page, brain, make_source):
    a = make_source("p/a", {"index.md": "1"})
    b = make_source("p/b", {"index.md": "2"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.locator("#f-policy").select_option("original")      # with a name policy a clash gets a suffix instead
        page.get_by_label("Folder 1 path").fill(str(a))
        add_source(page)
        page.get_by_label("Folder 2 path").fill(str(b))
        page.locator("#f-template").fill("raw/{pipeline}/{relpath}")
        expect(page.locator("#pv .banner.err")).to_contain_text("both resolve to")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        shot(page, "08-editor-collision")
        page.locator("#f-template").fill("raw/{pipeline}/")
        page.locator("#f-template").press("End")
        page.get_by_role("button", name="{source}").click()
        page.get_by_role("button", name="{relpath}").click()
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()


def test_editing_an_existing_pipeline_removing_a_folder_keeps_its_files_by_default(page, two):
    sync_pipeline(two, "biz")
    with running_ui(two) as ui:
        open_app(page, ui)
        page.locator("li.prow", has_text="biz").get_by_role("button", name="Edit").click()
        expect(page.get_by_label("Folder 2 label")).to_have_value("wiki")
        page.get_by_role("button", name="Remove folder 2").click()
        expect(page.locator("#m-removed")).to_contain_text("are kept in your output folder")
        expect(page.locator("#m-removed")).to_contain_text("Also delete its 2 files")
        shot(page, "09-editor-remove-folder")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("biz saved")
    assert (two.root / "raw/biz/wiki/index.md").exists()
    assert [s.label for s in load_config(two).get("biz").sources] == ["raw"]


def test_activity_conflicts_with_confirmed_force_and_copy_path(page, two):
    sync_pipeline(two, "solo")
    (two.root / "raw/projects/solo/s.md").write_text("my edit")
    sync_pipeline(two, "solo", trigger="mcp")
    with running_ui(two) as ui:
        open_app(page, ui)
        expect(page.locator(".badge")).to_have_text("1")
        expect(page.locator("li.prow", has_text="solo")).to_contain_text("conflicts")
        page.locator("li.prow", has_text="solo").get_by_role("button", name="conflicts").click()
        card = page.locator("article.ccard")
        expect(card).to_contain_text("Edited in your output folder since the last sync")
        expect(card).to_contain_text("raw/projects/solo/s.md")
        shot(page, "10-activity-conflict")
        card.get_by_role("button", name="Force…").click()
        expect(card.locator(".confirm")).to_contain_text("Overwrite your edits")
        card.get_by_role("button", name="Keep my edits").click()
        assert (two.root / "raw/projects/solo/s.md").read_text() == "my edit"
        card.get_by_role("button", name="Force…").click()
        card.get_by_role("button", name="Overwrite").click()
        expect(page.locator("article.ccard")).to_have_count(0)
        assert "my edit" not in (two.root / "raw/projects/solo/s.md").read_text()
        ev = [e for e in ActivityLog(two).read(types={"event"}) if e["action"] == "force"][-1]
        assert ev["trigger"] == "ui"


def test_activity_guard_card_approve_and_cancel(page, brain, add_pipeline, make_source):
    src = make_source("big", {f"n{i}.md": "x" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    for i in range(6):
        (src / f"n{i}.md").unlink()
    sync_pipeline(brain, "p", trigger="mcp")
    with running_ui(brain) as ui:
        open_app(page, ui)
        row = page.locator("li.prow", has_text="p")
        expect(row.get_by_role("button", name="Sync now")).to_be_disabled()
        row.get_by_role("button", name="blocked").click()
        card = page.locator("article.gcard")
        expect(card).to_contain_text("6 of 10 files (60%)")
        expect(card).to_contain_text("Will delete (6)")
        expect(card).to_contain_text("An agent (MCP) cannot approve")
        shot(page, "11-activity-guard")
        card.get_by_role("button", name="Cancel").click()
        expect(page.locator("article.gcard")).to_have_count(0)
        assert (brain.root / "raw/p/big/n0.md").exists()
        # next sync re-checks and blocks again; then approve
        sync_pipeline(brain, "p", trigger="cli")
        page.reload()
        open_activity(page)
        page.locator("article.gcard").get_by_role("button", name="Approve & sync").click()
        expect(page.locator("article.gcard")).to_have_count(0)
    assert not (brain.root / "raw/p/big/n0.md").exists() and (brain.root / "raw/p/big/n9.md").exists()


def test_activity_error_card_and_history_rows_expand(page, brain, add_pipeline, make_source, tmp_path):
    a = make_source("a", {"1.md": "1", "2.md": "2"})
    b = make_source("b", {"3.md": "3"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    (a / "1.md").write_text("changed")
    b.rename(tmp_path / "moved")
    sync_pipeline(brain, "p", trigger="mcp")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("li.prow", has_text="p")).to_contain_text("error")
        open_activity(page)
        card = page.locator("article.ecard")
        expect(card).to_contain_text("Source folder not found")                   # MD-136: a calm notice, not an error card
        expect(card).to_contain_text("Nothing was deleted; your files are kept")
        expect(card.get_by_role("button", name="Choose new location")).to_be_visible()
        shot(page, "12-activity-error")
        row = page.locator("tr.r").first
        expect(row).to_contain_text("MCP")
        row.click()
        expect(page.locator("tr.files").first).to_contain_text("raw/p/A/1.md")
        shot(page, "13-activity-history")


def test_open_button_uses_the_os_opener_and_falls_back_to_copy_path(page, two):
    sync_pipeline(two, "solo")
    (two.root / "raw/projects/solo/s.md").write_text("edited")
    sync_pipeline(two, "solo")
    opened = []
    with running_ui(two, opener=lambda p: opened.append(p)) as ui:
        open_app(page, ui, "activity")
        page.get_by_role("button", name="Open", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Opened")
        assert opened == [str(two.root / "raw/projects/solo/s.md")]


def test_the_open_ui_updates_after_a_cli_sync_within_three_seconds(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        solo = page.locator("li.prow", has_text="solo")
        expect(solo).to_contain_text("not synced yet")
        started = time.monotonic()
        subprocess.run([sys.executable, "-m", "mdpipeline", "--brain", str(two.root), "sync", "solo"], check=True, capture_output=True)
        expect(solo).to_contain_text("1 created", timeout=3000)
        assert time.monotonic() - started < 3.5
        # and the badge: an external conflict shows up without reload
        (two.root / "raw/projects/solo/s.md").write_text("edited")
        subprocess.run([sys.executable, "-m", "mdpipeline", "--brain", str(two.root), "sync", "solo"], capture_output=True)
        expect(page.locator(".badge")).to_have_text("1", timeout=3000)


def test_mobile_width_has_no_horizontal_overflow(browser, two):
    ctx = browser.new_context(viewport={"width": 390, "height": 800})
    pg = ctx.new_page()
    with running_ui(two) as ui:
        pg.goto(ui.base)
        expect(pg.locator("li.prow").first).to_be_visible()
        shot(pg, "14-mobile-home")
        offenders = pg.evaluate("""() => { const W = window.innerWidth; return [...document.querySelectorAll('body *')]
            .filter(e => e.getBoundingClientRect().right > W + 1).slice(0, 6).map(e => e.tagName + '.' + e.className); }""")
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), offenders
    ctx.close()


def test_dark_theme_renders(browser, two):
    ctx = browser.new_context(viewport={"width": 1100, "height": 800}, color_scheme="dark")
    pg = ctx.new_page()
    with running_ui(two) as ui:
        pg.goto(ui.base)
        expect(pg.locator("li.prow").first).to_be_visible()
        assert pg.evaluate("getComputedStyle(document.body).backgroundColor") != "rgb(244, 242, 236)"
        shot(pg, "15-dark-home")
    ctx.close()


def test_connect_panel_copy_button_copies_the_snippet(browser, two):
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    with running_ui(two) as ui:
        pg.goto(ui.base)
        show_manual_agent_command(pg)
        expect(pg.locator("#snip")).to_contain_text("claude mcp add")
        pg.get_by_role("button", name="Copy").click()
        expect(pg.locator("#toast")).to_contain_text("Copied MCP config")
        copied = pg.evaluate("navigator.clipboard.readText()")
        assert copied.startswith("claude mcp add mdpipeline") and str(two.root) in copied
        pg.get_by_role("button", name="Codex", exact=True).click()
        pg.get_by_role("button", name="Copy").click()
        assert "[mcp_servers.mdpipeline]" in pg.evaluate("navigator.clipboard.readText()")
    ctx.close()


def test_editor_guard_banner_when_an_edit_would_trip_the_guard_and_filter_pills(page, brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": "x" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow", has_text="p").get_by_role("button", name="Edit").click()
        page.locator("#f-template").fill("raw/moved/{relpath}")
        expect(page.locator("#pv .banner.warn")).to_contain_text("delete 10")
        expect(page.locator("#pv .banner.warn")).to_contain_text("100%")
        shot(page, "16-editor-guard")
        page.locator("#pv .counts").get_by_role("button", name="create").click()
        expect(page.locator("#pv tbody .op.delete")).to_have_count(0)
        expect(page.locator("#pv tbody .op.create")).to_have_count(10)
        page.locator("#pv .counts").get_by_role("button", name="delete").click()
        expect(page.locator("#pv tbody .op.create")).to_have_count(0)
        expect(page.locator("#pv tbody .op.delete")).to_have_count(10)


def test_editor_save_of_a_guard_tripping_folder_deletion_asks_for_approval_inline(page, brain, add_pipeline, make_source):
    a = make_source("a", {"1.md": "1"})
    b = make_source("b", {f"b{i}.md": "x" for i in range(8)})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow", has_text="p").get_by_role("button", name="Edit").click()
        page.get_by_role("button", name="Remove folder 2").click()
        page.get_by_label("Also delete its 8 files from your output folder").check()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator(".confirm")).to_contain_text("8 of 9 owned files")
        assert (brain.root / "raw/p/B/b0.md").exists()
        shot(page, "17-editor-blocked-delete")
        page.get_by_role("button", name="Approve and delete").click()
        expect(page.locator("#toast")).to_contain_text("p saved")
    assert not list((brain.root / "raw/p/B").glob("*.md"))


def test_needs_you_offers_the_state_restore_with_a_preview_first(page, brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": "x" for i in range(3)})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p")
    sync_pipeline(brain, "p")
    (brain.config_dir / "state.json").write_text("{corrupt")
    with running_ui(brain) as ui:
        open_app(page, ui, "activity")
        card = page.locator("article.ecard")
        expect(card).to_contain_text("cannot be read")
        card.get_by_role("button", name="Show what a restore would do").click()
        expect(card).to_contain_text("3 owned files")
        expect(card).to_contain_text("3 unchanged")
        assert (brain.config_dir / "state.json").read_text() == "{corrupt"
        shot(page, "11-state-restore")
        card.get_by_role("button", name="Restore this backup").click()
        expect(page.locator("article.ecard")).to_have_count(0)
    assert sync_pipeline(brain, "p").counts()["unchanged"] == 3


def test_needs_you_conflict_card_for_an_unclaimed_copy_shows_the_next_step(page, brain, add_pipeline, make_source):
    from mdpipeline.planner import NEXT_STEP
    from mdpipeline.rebuild import apply_rebuild
    src = make_source("docs", {"n.md": "x\n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p")
    copy = next((brain.root / "raw/p/docs").glob("*.md"))
    copy.write_text(copy.read_text() + "my edit\n")
    (brain.config_dir / "state.json").unlink()
    for b in (brain.config_dir / "backups").glob("*"):
        b.unlink()
    apply_rebuild(brain)
    sync_pipeline(brain, "p", trigger="mcp")
    with running_ui(brain) as ui:
        open_app(page, ui, "activity")
        card = page.locator("article.ccard")
        expect(card).to_contain_text("cannot tell whether the source or the copy in your output folder changed")
        expect(card).to_contain_text(NEXT_STEP)
        expect(card.get_by_role("button", name="Open")).to_be_visible()
        expect(card.get_by_role("button", name="Copy path")).to_be_visible()
        shot(page, "12-unclaimed-next-step")
