"""MD-157: the header names where the files go with the agreed terms (MM-111): "Output folder" (local), "Your GitHub repo" (GitHub target),
"App data folder" when it can only show the app's own base folder."""
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_ui_browser import browser, open_app, page, two  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

JS = (Path(__file__).parent.parent / "src" / "mdpipeline" / "ui_static" / "app.js").read_text()


def header(page, state):
    return page.evaluate("(st) => headerWhere(st)", state)


def row(destination, last_run=True, ts="2026-10-19T10:00:00Z"):
    return {"last_run": {"ts": ts} if last_run else None, "destination": destination}


BRAIN = {"path": "/home/u/app-base"}


def test_a_local_target_reads_output_folder_with_its_path(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        got = header(page, {"brain": BRAIN, "pipelines": [row({"type": "directory", "path": "/home/u/markdown-pipeline/acme"})]})
    assert got == {"label": "Output folder", "value": "/home/u/markdown-pipeline/acme"}


def test_a_github_target_reads_your_github_repo_with_owner_slash_repo(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        got = header(page, {"brain": BRAIN, "pipelines": [row({"type": "github", "repo": "acme/wiki", "branch": "main"})]})
    assert got == {"label": "Your GitHub repo", "value": "acme/wiki"}


def test_the_most_recently_synced_pipeline_decides_and_other_targets_are_counted(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        got = header(page, {"brain": BRAIN, "pipelines": [
            row({"type": "directory", "path": "/old"}, ts="2026-10-18T10:00:00Z"),
            row({"type": "github", "repo": "acme/wiki", "branch": "main"}, ts="2026-10-19T10:00:00Z")]})
    assert got == {"label": "Your GitHub repo", "value": "acme/wiki · +1 more"}


def test_without_a_known_target_the_header_shows_the_app_data_folder(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        got = header(page, {"brain": BRAIN, "pipelines": [row(None)]})
    assert got == {"label": "App data folder", "value": "/home/u/app-base"}


def test_the_old_label_is_gone_from_the_header():
    assert "<span>your files</span>" not in JS


def test_the_real_header_uses_the_new_label_after_a_sync(page, two):
    from mdpipeline.engine import sync_pipeline

    with running_ui(two) as ui:
        sync_pipeline(two, "solo", trigger="ui")
        open_app(page, ui)
        expect(page.locator(".brainpath")).to_contain_text("Output folder")
        expect(page.locator(".brainpath")).to_contain_text(str(two.root))
