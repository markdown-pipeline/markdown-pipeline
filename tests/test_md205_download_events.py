"""MD-205 AC3/AC7: a download of a release asset by `install.sh` (a first install, or `mdpipeline update` for a script install) is countable on its own.

Names pre-approved by marketing (MD-205 comment 11232): release_version, asset_name, download_source = github_release, install_method, is_internal.
The installer runs before the app exists, so it only tells the app (`install-note script --release-version V --asset-name A`); the app remembers it
and sends ONE event the next time it can: `install_completed` for a first install, `app_updated` for an update. Nothing is sent without consent."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mdpipeline import usage, usage_hooks
from mdpipeline.cli import main
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import sanitize

VERSION = usage.__version__
ASSET = f"mdpipeline-{VERSION}-py3-none-any.whl"
DOWNLOAD = {"release_version": VERSION, "asset_name": ASSET, "download_source": "github_release"}


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD", raising=False)


def queued(*names: str) -> list[dict]:
    path = usage.data_dir() / "usage.json"
    entries = json.loads(path.read_text()).get("queue", []) if path.exists() else []
    return [e for e in entries if not names or e["event"] in names]


def own(e: dict) -> dict:
    return {k: v for k, v in e["properties"].items() if k not in ("app_version", "deployment", "session_id", "is_internal")}


def install_note(*extra: str) -> int:
    return main(["install-note", "script", *extra])


# --- the allowlist ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("event", ["install_completed", "app_updated"])
def test_both_events_accept_the_three_download_properties(event):
    base = {"os": "linux", "install_method": "script"} if event == "install_completed" else {"previous_version": "0.1.14", "install_method": "script"}
    clean = sanitize(event, {**base, **DOWNLOAD})
    assert clean["release_version"] == VERSION and clean["asset_name"] == ASSET and clean["download_source"] == "github_release"


@pytest.mark.parametrize("name", ["../../etc/passwd", "mdpipeline-0.1.7.tar.gz", "C:\\Users\\me\\mdpipeline-0.1.7-py3-none-any.whl", "x" * 300, "", None])
def test_an_asset_name_that_is_not_our_wheel_is_dropped(name):
    assert "asset_name" not in sanitize("install_completed", {"os": "linux", "install_method": "script", "asset_name": name})


def test_a_download_source_other_than_github_release_is_dropped():
    assert "download_source" not in sanitize("install_completed", {"os": "linux", "install_method": "script", "download_source": "my-mirror.example"})


# --- the installer tells the app ---------------------------------------------------------------------------------------
def test_install_note_remembers_the_download(sharing):
    assert install_note("--release-version", VERSION, "--asset-name", ASSET) == 0
    assert UsageStore().pending_download == {"release_version": VERSION, "asset_name": ASSET}


def test_install_note_without_a_download_records_none(sharing):
    assert install_note() == 0 and UsageStore().pending_download is None


@pytest.mark.parametrize("version,asset", [("1.2", ASSET), (VERSION, "evil.whl"), ("x", "y")])
def test_install_note_ignores_values_that_are_not_ours(sharing, version, asset):
    assert install_note("--release-version", version, "--asset-name", asset) == 0       # the install itself is never failed by this
    assert UsageStore().pending_download is None


def test_install_note_alone_sends_nothing_even_with_consent(sharing):
    install_note("--release-version", VERSION, "--asset-name", ASSET)
    assert queued() == []


# --- AC7: first install: exactly one event, right version -------------------------------------------------------------
def test_a_first_install_sends_exactly_one_install_completed_with_the_download(sharing):
    install_note("--release-version", VERSION, "--asset-name", ASSET)
    assert usage.report_install_once() is True
    (event,) = queued("install_completed")
    assert own(event) == {"os": "linux" if sys.platform.startswith("linux") else own(event)["os"], "install_method": "script", **DOWNLOAD}
    assert "is_internal" in event["properties"]
    assert usage.report_install_once() is False and len(queued("install_completed")) == 1       # once per install
    assert UsageStore().pending_download is None


def test_a_first_install_without_a_download_has_no_download_properties(sharing):
    usage.report_install_once()
    (event,) = queued("install_completed")
    assert not {"release_version", "asset_name", "download_source"} & set(own(event))


# --- AC7: consent off: no event ---------------------------------------------------------------------------------------
def test_no_event_without_consent_and_the_record_is_dropped_when_consent_is_declined():
    install_note("--release-version", VERSION, "--asset-name", ASSET)
    assert usage.report_install_once() is False and queued() == []
    UsageStore().set_consent("decline")
    assert UsageStore().pending_download is None


def test_consent_given_later_still_sends_the_pending_first_install(sharing):
    # the person installs first and is asked afterwards: the record waits for the answer
    UsageStore().set_consent("unanswered")
    install_note("--release-version", VERSION, "--asset-name", ASSET)
    UsageStore().set_consent("share")
    usage.report_install_once()
    assert own(queued("install_completed")[0])["release_version"] == VERSION


# --- AC7: update: exactly one event, right version --------------------------------------------------------------------
def test_an_update_by_the_script_sends_one_app_updated_with_the_download(sharing, monkeypatch):
    import mdpipeline
    usage_hooks.app_opened()                                         # the old version is remembered
    new = "9.9.9"
    monkeypatch.setattr(mdpipeline, "__version__", new)
    monkeypatch.setattr(usage, "__version__", new)
    install_note("--release-version", new, "--asset-name", f"mdpipeline-{new}-py3-none-any.whl")
    usage_hooks.app_opened()
    (event,) = queued("app_updated")
    assert own(event) == {"previous_version": VERSION, "install_method": "script", "release_version": new,
                          "asset_name": f"mdpipeline-{new}-py3-none-any.whl", "download_source": "github_release"}
    usage_hooks.app_opened()
    assert len(queued("app_updated")) == 1 and UsageStore().pending_download is None


def test_an_update_by_pypi_does_not_claim_a_release_download(sharing, monkeypatch):
    import mdpipeline
    usage_hooks.app_opened()
    install_note("--release-version", "0.0.1", "--asset-name", "mdpipeline-0.0.1-py3-none-any.whl")        # an old download that is not this version
    monkeypatch.setattr(mdpipeline, "__version__", "9.9.9")
    monkeypatch.setattr(usage, "__version__", "9.9.9")
    usage_hooks.app_opened()
    (event,) = queued("app_updated")
    assert "release_version" not in own(event) and UsageStore().pending_download is None


def test_an_update_without_consent_sends_nothing():
    import mdpipeline
    usage_hooks.app_opened()
    install_note("--release-version", "9.9.9", "--asset-name", "mdpipeline-9.9.9-py3-none-any.whl")
    assert queued() == []


# --- the installer script passes it on -------------------------------------------------------------------------------
INSTALL_SH = Path(__file__).resolve().parent.parent / "scripts" / "install.sh"


def test_the_script_tells_the_app_about_a_release_it_downloaded():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "--release-version" in text and "--asset-name" in text


def test_the_script_does_not_claim_a_download_when_installing_from_a_given_source():
    text = INSTALL_SH.read_text(encoding="utf-8")
    # the flags are only set on the branch that looked up the latest release, never for MDPIPELINE_SOURCE
    assert text.index("DOWNLOADED") < text.index("install-note")
