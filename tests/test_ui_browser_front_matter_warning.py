"""MD-201 AC5: the pipeline row shows ONE amber note for the skipped file (path, line, one next step); no red error line."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline.config import Pipeline  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_front_matter_warning import BAD, GOOD  # noqa: E402
from test_ui_browser import browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "notes/bad.md": BAD})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    sync_pipeline(brain, "p")          # a second (scheduled) run changes nothing in what the person sees
    return src


def test_the_row_shows_one_warning_with_file_line_and_next_step(page, brain, synced):
    with running_ui(brain) as ui:
        open_app(page, ui)
        note = page.locator("[data-fmwarn]")
        expect(note).to_have_count(1)
        expect(note).to_contain_text("1 file skipped: its front matter (the --- block at the top) has a mistake")
        expect(note).to_contain_text("notes/bad.md, near line 3.")
        expect(note).to_contain_text("Your other files synced and this file was not changed.")
        expect(note).to_contain_text("Check near line 3 (the mistake can be on the line before), or exclude the file.")
        expect(page.locator(".errline")).to_have_count(0)


def test_no_note_when_the_front_matter_is_fine(page, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("[data-fmwarn]")).to_have_count(0)


# --- product comment 11328, point 3: the plural note text, and point 4: not-mapping ------------------------------------
BAD2 = '---\ntitle: Two\nframing: "Quoted" then more text\n---\nbody\n'


def test_two_files_give_a_plural_note_without_a_doubled_s(page, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "a/bad.md": BAD, "b/bad2.md": BAD2})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        note = page.locator("[data-fmwarn]")
        expect(note).to_contain_text("2 files skipped: their front matter (the --- block at the top) has a mistake")
        expect(note).to_contain_text("a/bad.md, near line 3")
        expect(note).to_contain_text("Your other files synced and these files were not changed. Check near those lines (a mistake can be on the line before), or exclude the files.")
        assert "filess" not in note.inner_text()


def test_a_not_mapping_file_is_not_called_invalid_yaml_or_given_a_line(page, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "list.md": "---\n- one\n- two\n---\nbody\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        note = page.locator("[data-fmwarn]")
        expect(note).to_contain_text("list.md")
        expect(note).to_contain_text("Its front matter is not made of key: value lines.")
        expect(note).to_contain_text("Rewrite it as key: value lines, or exclude the file.")
        assert "near line" not in note.inner_text()


def test_no_closing_line_note(page, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "open.md": "---\ntitle: x\nbody\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        note = page.locator("[data-fmwarn]")
        expect(note).to_contain_text("open.md. Your other files synced and this file was not changed. Add the closing --- line, or exclude the file.")


def test_mixed_kinds_get_the_generic_closing(page, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "a.md": BAD, "open.md": "---\ntitle: x\nbody\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("[data-fmwarn]")).to_contain_text("Check the front matter in each file, or exclude the files.")


def test_more_than_three_files_say_and_n_more(page, brain, add_pipeline, make_source):
    files = {"good.md": GOOD, **{f"b{i}.md": BAD for i in range(5)}}
    add_pipeline(Pipeline.new("p", [str(make_source("docs", files))]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        text = page.locator("[data-fmwarn]").inner_text()
        assert "5 files skipped" in text and "and 2 more" in text and text.count("near line 3") == 3


def test_old_copy_is_marked(page, brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": GOOD})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    (src / "a.md").write_text(BAD)
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("[data-fmwarn]")).to_contain_text("The copy in your output folder is from before your last edit.")
