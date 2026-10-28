"""MD-142 in a real browser: one Browse component for the source and output folder fields; typing stays as the fallback."""
import json
import os
import re

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, folderbrowser, notices, usage  # noqa: E402
from mdpipeline.config import Pipeline, Source, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from mdpipeline.usage import UsageStore  # noqa: E402
from test_ui_browser import add_source, browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402
import winsupport


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)
    monkeypatch.setattr(folderbrowser, "wsl_drives", lambda: [])


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    for d in ("Documents", ".hidden-stuff", "projects/alpha", "projects/beta", "empty"):
        (h / d).mkdir(parents=True)
    (h / "projects" / "alpha" / "a1.md").write_text("# a1\n")
    (h / "projects" / "alpha" / "a2.md").write_text("# a2\n")
    monkeypatch.setenv("HOME", str(h))
    return h


def dialog(page):
    return page.get_by_role("dialog", name="Choose a folder")


def start_folder_run(page, ui):
    open_app(page, ui)
    page.get_by_role("button", name="Folder on this computer").click()


def test_browse_walks_into_folders_and_selecting_fills_the_field_and_checks_it(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_role("button", name="Browse for the folder").click()
        expect(dialog(page)).to_be_visible()
        expect(dialog(page).locator(".br-path")).to_have_text(str(home))                 # no current value: starts at home
        shot(page, "md142-browser")
        dialog(page).get_by_role("button", name="projects").click()
        dialog(page).get_by_role("button", name="alpha").click()
        expect(dialog(page).locator(".br-path")).to_have_text(str(home / "projects" / "alpha"))
        expect(dialog(page).get_by_role("navigation", name="Path")).to_contain_text("projects")
        dialog(page).get_by_role("button", name="Select this folder").click()
        expect(dialog(page)).to_have_count(0)
        expect(page.get_by_label("Folder path")).to_have_value(str(home / "projects" / "alpha"))
        expect(page.locator("#fr-folder-msg")).to_contain_text("Folder found · 2 Markdown files")
        page.get_by_role("button", name="Continue").click()
        expect(page.get_by_role("heading", name="Where should your files go?")).to_be_visible()


def test_the_browser_starts_at_the_current_value_when_it_is_a_folder(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_label("Folder path").fill(str(home / "projects"))
        page.get_by_role("button", name="Browse for the folder").click()
        expect(dialog(page).locator(".br-path")).to_have_text(str(home / "projects"))
        dialog(page).get_by_role("button", name="Up").click()
        expect(dialog(page).locator(".br-path")).to_have_text(str(home))
        expect(dialog(page).get_by_role("button", name="Up")).to_be_enabled()


def test_a_typed_path_that_does_not_exist_starts_at_home_with_a_calm_note(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_label("Folder path").fill(str(home / "nope"))
        page.get_by_role("button", name="Browse for the folder").click()
        expect(dialog(page).locator(".br-path")).to_have_text(str(home))
        expect(dialog(page).locator(".br-note[role=status]")).to_contain_text("We can't find that folder")
        expect(dialog(page).locator("[role=alert]")).to_have_count(0)


def test_cancel_and_escape_are_a_way_back_that_changes_nothing_and_returns_focus(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_label("Folder path").fill(str(home / "projects"))
        button = page.get_by_role("button", name="Browse for the folder")
        button.click()
        dialog(page).get_by_role("button", name="beta").click()
        dialog(page).get_by_role("button", name="Cancel", exact=True).click()
        expect(page.get_by_label("Folder path")).to_have_value(str(home / "projects"))
        button.click()
        page.keyboard.press("Escape")
        expect(dialog(page)).to_have_count(0)
        expect(button).to_be_focused()


def test_the_dialog_is_labelled_modal_and_keyboard_operable(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_role("button", name="Browse for the folder").click()
        expect(dialog(page)).to_have_attribute("aria-modal", "true")
        for _ in range(40):                                        # Tab never leaves the dialog
            page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.closest('[role=dialog]') !== null")
        page.get_by_role("button", name="projects").focus()
        page.keyboard.press("Enter")
        expect(dialog(page).locator(".br-path")).to_have_text(str(home / "projects"))


def test_hidden_folders_have_a_toggle_and_are_off_by_default(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_role("button", name="Browse for the folder").click()
        expect(dialog(page).get_by_role("button", name=".hidden-stuff")).to_have_count(0)
        dialog(page).get_by_label("Show hidden").check()
        expect(dialog(page).get_by_role("button", name=".hidden-stuff")).to_be_visible()


@pytest.mark.skipif(winsupport.is_root(), reason="root can open everything")
@pytest.mark.windows_unsupported("chmod makes a file or folder unreadable only on Unix; Windows ignores the permission bits")
def test_an_unreadable_folder_is_disabled_with_a_reason(page, brain, home):
    locked = home / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with running_ui(brain) as ui:
            start_folder_run(page, ui)
            page.get_by_role("button", name="Browse for the folder").click()
            entry = dialog(page).get_by_role("button", name=re.compile("^locked"))
            expect(entry).to_be_disabled()
            expect(dialog(page).locator(".br-list")).to_contain_text("doesn't let this app open it")
    finally:
        locked.chmod(0o755)


def test_an_empty_folder_says_so_and_can_still_be_selected_or_left(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_label("Folder path").fill(str(home / "empty"))
        page.get_by_role("button", name="Browse for the folder").click()
        expect(dialog(page).locator(".br-list")).to_contain_text("No folders in here")
        expect(dialog(page).get_by_role("button", name="Select this folder")).to_be_enabled()


def test_shortcuts_jump_to_home_documents_and_recent_folders(page, brain, home, add_pipeline):
    add_pipeline(Pipeline.new("p", [Source(str(home / "projects" / "alpha"), "alpha")]))
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="+ New pipeline").first.click()
        add_source(page)
        page.get_by_role("button", name=re.compile("Browse for folder 1")).click()
        dialog(page).get_by_role("button", name="Documents", exact=True).first.click()
        expect(dialog(page).locator(".br-path")).to_have_text(str(home / "Documents"))
        dialog(page).get_by_role("button", name="alpha", exact=True).click()                    # the recent entry
        expect(dialog(page).locator(".br-path")).to_have_text(str(home / "projects" / "alpha"))


def test_a_source_dialog_has_no_new_folder_and_an_output_dialog_does(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_role("button", name="Browse for the folder").click()
        expect(dialog(page).get_by_role("button", name="New folder")).to_have_count(0)
        dialog(page).get_by_role("button", name="Cancel", exact=True).click()
        page.get_by_label("Folder path").fill(str(home / "projects" / "alpha"))
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("button", name="Browse for the output folder").click()
        expect(dialog(page).get_by_role("button", name="New folder")).to_be_visible()


def test_new_folder_creates_and_selects_it_as_the_output(page, brain, home):
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_label("Folder path").fill(str(home / "projects" / "alpha"))
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("button", name="Browse for the output folder").click()
        dialog(page).get_by_role("button", name="New folder").click()
        dialog(page).get_by_label("New folder name").fill("a/b")
        dialog(page).get_by_role("button", name="Create", exact=True).click()
        expect(dialog(page).locator(".br-note")).to_contain_text("single folder name")
        dialog(page).get_by_label("New folder name").fill("my-brain")
        dialog(page).get_by_role("button", name="Create", exact=True).click()
        expect(dialog(page)).to_have_count(0)
        expect(page.locator("#fr-dest")).to_have_value(str(home / "my-brain"))
        assert (home / "my-brain").is_dir()
        page.get_by_role("button", name="Use this folder").click()
        page.get_by_role("button", name=re.compile("^Write")).click()
        expect(page.locator("#fr-ready")).to_contain_text("2 files in")


def test_the_editor_source_rows_and_the_target_folder_have_browse_buttons(page, brain, home):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        add_source(page)
        page.get_by_role("button", name=re.compile("Browse for folder 1")).click()
        dialog(page).get_by_role("button", name="projects").click()
        dialog(page).get_by_role("button", name="alpha").click()
        dialog(page).get_by_role("button", name="Select this folder").click()
        expect(page.get_by_label("Folder 1 path")).to_have_value(str(home / "projects" / "alpha"))
        expect(page.locator("#pv")).to_contain_text("a1.md")                                    # the input event drove the live preview
        page.get_by_role("button", name=re.compile("Browse for a folder to write to")).click()
        expect(dialog(page).get_by_role("button", name="New folder")).to_be_visible()


def test_choose_new_location_has_browse_too(page, brain, home, add_pipeline):
    src = home / "projects" / "alpha"
    add_pipeline(Pipeline.new("p", [Source(str(src), "alpha")], target="{relpath}", name_policy="original"))
    sync_pipeline(brain, "p")
    moved = home / "projects" / "beta"
    (moved / "a1.md").write_text("# a1\n")
    import shutil
    shutil.rmtree(src)
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        page.get_by_role("button", name="Choose new location").first.click()
        page.get_by_role("button", name=re.compile("Browse for the new folder")).click()
        dialog(page).get_by_role("button", name="projects").click()
        dialog(page).get_by_role("button", name="beta").click()
        dialog(page).get_by_role("button", name="Select this folder").click()
        expect(page.locator("input[data-msrc-path]")).to_have_value(str(moved))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#toast")).to_contain_text("new location saved")
        assert load_config(brain).get("p").sources[0].path == str(moved)


def test_picks_are_reported_as_a_method_only_never_a_path(page, brain, home):
    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        start_folder_run(page, ui)
        page.get_by_role("button", name="Browse for the folder").click()
        dialog(page).get_by_role("button", name="projects").click()
        dialog(page).get_by_role("button", name="Select this folder").click()
        page.get_by_label("Folder path").fill(str(home / "projects" / "alpha"))
        page.get_by_label("Folder path").blur()
        expect(page.locator("#fr-folder-msg")).to_contain_text("Folder found")
    entries = [e for e in json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", []) if e["event"] == "folder_pick"]
    assert {e["properties"]["method"] for e in entries} == {"browse", "typed"}
    assert str(home) not in json.dumps(entries) and "alpha" not in json.dumps(entries)
