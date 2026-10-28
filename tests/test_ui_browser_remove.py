"""MD-134 in a real browser: Remove pipeline from view mode and the editor; one confirmation, keep the files by default, exact numbers."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline.config import Pipeline, Source, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_ui_browser import browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def two(brain, add_pipeline, make_source):
    a = make_source("docs", {"a.md": "# A\n", "b.md": "# B\n", "c.md": "# C\n"})
    b = make_source("other", {"o.md": "# O\n"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "docs")], name_policy="original"))
    add_pipeline(Pipeline.new("q", [Source(str(b), "other")], name_policy="original"))
    sync_pipeline(brain, "p")
    sync_pipeline(brain, "q")
    return brain


def open_view(page, name="p"):
    page.locator("li.prow", has_text=name).get_by_role("link", name=name).click()
    expect(page.locator("#view")).to_be_visible()


def files(brain, name):
    return sorted(p.name for p in (brain.root / "raw" / name).rglob("*.md"))


def test_view_mode_has_a_remove_action_that_opens_one_confirmation_with_keep_as_the_default(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Remove pipeline").click()
        card = page.locator("#remove-card")
        expect(card).to_contain_text("3 files stay where they are. They become yours. Nothing will update or delete them again.")
        expect(card).to_contain_text("Delete the 3 files this pipeline created")
        expect(card.get_by_label("Keep the files")).to_be_checked()
        assert files(two, "p") == ["a.md", "b.md", "c.md"]          # nothing happened yet


def test_the_button_is_neutral_on_keep_and_red_only_on_delete_and_says_what_it_deletes(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Remove pipeline").click()
        card = page.locator("#remove-card")
        confirm = card.locator("[data-rm=confirm]")
        expect(confirm).to_have_text("Remove pipeline")
        assert "danger" not in confirm.get_attribute("class")
        card.get_by_label("Delete the 3 files").check()
        expect(confirm).to_have_text("Remove and delete 3 files")
        assert "danger" in confirm.get_attribute("class")
        expect(card).to_contain_text("Only files you did not edit. They are deleted for good.")


def test_a_long_edited_list_shows_five_paths_and_how_many_more(page, two):
    root = two.root / "raw" / "p"
    for f in list(root.rglob("*.md")):
        f.write_text(f.read_text(encoding="utf-8") + "\nmine\n", encoding="utf-8")
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Remove pipeline").click()
        expect(page.locator("#remove-edited")).to_contain_text("3 files you edited are always kept")


def test_cancel_closes_the_confirmation_and_changes_nothing(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Remove pipeline").click()
        page.locator("#remove-card").get_by_role("button", name="Cancel").click()
        expect(page.locator("#remove-card")).to_have_count(0)
    assert load_config(two).get("p") is not None


def test_removing_with_the_default_keeps_the_files_and_says_so(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Remove pipeline").click()
        page.locator("#remove-card").get_by_role("button", name="Remove pipeline").click()
        expect(page.locator("li.prow", has_text="p ")).to_have_count(0)
        expect(page.get_by_text('"p" removed. 3 files kept in your folder.')).to_be_visible()
    assert files(two, "p") == ["a.md", "b.md", "c.md"] and load_config(two).get("p") is None and load_config(two).get("q") is not None


def test_removing_with_delete_lists_the_edited_file_as_kept(page, two):
    edited = next((two.root / "raw" / "p").rglob("b.md"))
    edited.write_text(edited.read_text(encoding="utf-8") + "\nmine\n", encoding="utf-8")
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Remove pipeline").click()
        card = page.locator("#remove-card")
        expect(card).to_contain_text("Delete the 2 files this pipeline created")
        expect(card.locator("#remove-edited")).to_contain_text("b.md")
        card.get_by_label("Delete the 2 files").check()
        card.get_by_role("button", name="Remove and delete 2 files").click()
        expect(page.get_by_text('"p" removed. 2 files deleted; 1 kept because you edited it.')).to_be_visible()
    assert files(two, "p") == ["b.md"]


def test_the_editor_has_the_same_action(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        page.locator("li.prow", has_text="p").get_by_role("button", name="Edit").first.click()
        page.get_by_role("button", name="Remove pipeline").click()
        expect(page.locator("#remove-card")).to_be_visible()
