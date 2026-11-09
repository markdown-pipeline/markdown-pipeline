"""MD-212 AC7/AC8/AC9 in a real browser: Home and Settings say, consequence first, that background sync isn't running, with one action; the action
starts it and the notice is gone once a process runs again."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, background  # noqa: E402
from mdpipeline.config import Pipeline  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_background_headless import own, queued  # noqa: E402
from test_md212_cli_and_events import down  # noqa: E402,F401
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from test_ui_browser_background_outdated import open_settings  # noqa: E402
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    sync_pipeline(brain, "p")


def test_home_says_the_files_may_be_out_of_date_with_one_action(page, brain, down, synced):
    with running_ui(brain) as ui:
        open_app(page, ui)
        notice = page.locator("#bg-not-running")
        expect(notice).to_contain_text("Your files may be out of date. Background sync isn't running. Last update: less than a minute ago (")
        expect(notice.get_by_role("button", name="Start sync again")).to_have_count(1)
        assert page.get_by_role("button", name="Start sync again").count() == 1
        for jargon in ("login item", "XDG", "systemd"):
            assert jargon not in notice.inner_text()
        shot(page, "home-background-not-running")


def test_settings_says_the_same_and_the_notice_is_counted_once_per_outage(page, brain, down, synced):
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#bg-not-running")).to_be_visible()
        open_settings(page)
        expect(page.locator("#bg-settings #bg-not-running")).to_contain_text("Your files may be out of date.")
        page.wait_for_timeout(500)
    assert [own(e) for e in queued("notice_shown")] == [{"kind": "background_not_running", "surface": "home"}]


def test_start_sync_again_starts_it_is_counted_and_the_notice_goes_when_it_runs(page, brain, down, synced, monkeypatch):
    started = []

    def start(**kw):
        started.append(kw)
        monkeypatch.setattr(background, "is_running", lambda: True)      # the new process holds the lock
        background.record_heartbeat()
        return True

    monkeypatch.setattr(autostart, "start_now", start)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Start sync again").click()
        expect(page.locator("#bg-not-running")).to_have_count(0)
    assert started
    assert [own(e) for e in queued("notice_acted")] == [{"kind": "background_not_running", "via": "app"}]


@pytest.mark.real_login_start
def test_settings_says_it_cannot_start_at_login_on_a_box_with_no_systemd_and_no_desktop(page, brain, app_data, monkeypatch):
    import sys

    from test_background_headless import FakeSystemd
    from test_md212_no_login_start import TEXT

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(available=False))
    monkeypatch.setattr(background, "is_running", lambda: True)
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#bg-no-login-start")).to_have_text(TEXT)
        shot(page, "settings-cannot-start-at-login")


def test_home_says_nothing_has_synced_yet_when_there_is_no_last_update(page, brain, down, synced, monkeypatch):
    from mdpipeline import uiapi
    real = uiapi.background_view
    monkeypatch.setattr(uiapi, "background_view", lambda brain=None: {**real(brain), "last_update": "never"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        notice = page.locator("#bg-not-running")
        expect(notice).to_have_text("Background sync isn't running. Nothing has synced yet. Start sync")
        assert "may be out of date" not in notice.inner_text()
