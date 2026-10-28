"""MD-77: the registered GitHub App ships as the app's default (a packaged defaults file) and stays replaceable; the user's value wins."""
import json
from importlib.resources import files

import pytest

from mdpipeline import github_signin as gs
from mdpipeline.cli import main
from uihelper import running_ui

REAL_SHIPPED = gs.shipped_github_app          # the real function, before the autouse fixture in conftest empties it for every test
DEFAULT = {"client_id": "Iv23li87elyNUpdxbmkx", "slug": "markdown-pipeline"}
MINE = {"client_id": "Iv1.aaaaaaaaaaaaaaaa", "slug": "my-own-app"}


@pytest.fixture
def shipped(monkeypatch):
    monkeypatch.setattr(gs, "shipped_github_app", lambda: dict(DEFAULT))


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def test_the_packaged_defaults_file_holds_the_registered_app():
    assert REAL_SHIPPED() == DEFAULT
    data = json.loads((files("mdpipeline") / "defaults.json").read_text(encoding="utf-8"))
    assert data["github_app"] == DEFAULT


def test_the_client_id_appears_in_one_place_only():
    from pathlib import Path

    src = Path(__file__).parent.parent / "src" / "mdpipeline"
    hits = [p.name for p in src.rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".json", ".md") and DEFAULT["client_id"] in p.read_text(encoding="utf-8")]
    assert hits == ["defaults.json"]


def test_without_a_saved_value_the_shipped_default_is_used(shipped, app_data):
    assert gs.app_settings() == DEFAULT and gs.require_client_id() == DEFAULT["client_id"]
    assert gs.app_source() == "default" and gs.install_url() == "https://github.com/apps/markdown-pipeline/installations/new"


def test_a_saved_value_wins_over_the_shipped_default(shipped, app_data):
    gs.save_app_settings(**MINE)
    assert gs.app_settings() == MINE and gs.app_source() == "user"


def test_reset_goes_back_to_the_shipped_default_and_keeps_other_settings(shipped, app_data):
    gs.save_app_settings(**MINE)
    gs.reset_app_settings()
    assert gs.app_settings() == DEFAULT and gs.app_source() == "default"


def test_without_a_default_and_without_a_saved_value_nothing_is_configured(app_data):
    assert gs.app_settings() == {"client_id": "", "slug": ""} and gs.app_source() == "none"


def test_cli_show_says_whether_it_is_the_default_or_your_own(shipped, app_data, capsys):
    code, out = run(capsys, "github-app", "show")
    assert code == 0 and DEFAULT["client_id"] in out and "shipped default" in out
    run(capsys, "github-app", "set", "--client-id", MINE["client_id"], "--slug", MINE["slug"])
    code, out = run(capsys, "github-app", "show")
    assert MINE["client_id"] in out and "your own" in out.lower() and "reset" in out


def test_cli_reset_returns_to_the_default(shipped, app_data, capsys):
    run(capsys, "github-app", "set", "--client-id", MINE["client_id"], "--slug", MINE["slug"])
    code, out = run(capsys, "github-app", "reset")
    assert code == 0 and DEFAULT["client_id"] in out and gs.app_source() == "default"
    assert run(capsys, "github-app", "reset")[0] == 0                       # nothing to reset: still fine


def test_the_ui_reports_the_source_and_does_not_ask_for_setup_when_a_default_exists(shipped, brain):
    with running_ui(brain) as ui:
        app = ui.get("/api/state")[1]["github_app"]
        assert app["configured"] and app["source"] == "default" and app["has_default"] is True and app["client_id"] == DEFAULT["client_id"]
        status, res = ui.post("/api/github-app", {"client_id": MINE["client_id"], "slug": MINE["slug"]})
        assert status == 200 and res["source"] == "user" and res["has_default"] is True
        status, res = ui.post("/api/github-app/reset", {})
        assert status == 200 and res["source"] == "default" and res["client_id"] == DEFAULT["client_id"]
        assert ui.post("/api/github-app/reset", {}, token=False)[0] == 403


def test_the_ui_without_a_default_still_says_setup_is_needed(brain):
    with running_ui(brain) as ui:
        app = ui.get("/api/state")[1]["github_app"]
    assert app["configured"] is False and app["source"] == "none" and app["has_default"] is False
