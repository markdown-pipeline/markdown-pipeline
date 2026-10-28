"""MD-83 follow-up: the GitHub App's client ID and slug can be set from the UI (public values, same protections as other changes)."""
import json

import pytest

from mdpipeline import github_signin as gs
from uihelper import running_ui


def test_saving_valid_values_stores_them_and_sign_in_becomes_available_without_a_restart(brain, app_data):
    with running_ui(brain) as ui:
        assert ui.get("/api/state")[1]["github_app"]["configured"] is False
        status, res = ui.post("/api/github-app", {"client_id": " Iv23liAbCdEf123456 ", "slug": "My-App"})
        assert status == 200 and res["configured"] is True and res["client_id"] == "Iv23liAbCdEf123456" and res["slug"] == "my-app"
        state = ui.get("/api/state")[1]["github_app"]
    assert state["configured"] and state["client_id"] == "Iv23liAbCdEf123456" and state["install_url"] == "https://github.com/apps/my-app/installations/new"
    assert gs.app_settings() == {"client_id": "Iv23liAbCdEf123456", "slug": "my-app"}
    assert (app_data.dir / "settings.json").is_file() and not (app_data.dir / "vault.json").exists()


def test_the_values_can_be_changed_later(brain, app_data):
    gs.save_app_settings("Iv1.aaaaaaaaaaaaaaaa", "old-app")
    with running_ui(brain) as ui:
        status, res = ui.post("/api/github-app", {"client_id": "Iv1.bbbbbbbbbbbbbbbb", "slug": "new-app"})
    assert status == 200 and gs.app_settings() == {"client_id": "Iv1.bbbbbbbbbbbbbbbb", "slug": "new-app"}


@pytest.mark.parametrize("body,field", [
    ({"client_id": "", "slug": "app"}, "client_id"), ({"client_id": "abc123", "slug": "app"}, "client_id"),
    ({"client_id": "Iv1.x", "slug": "app"}, "client_id"), ({"client_id": "Iv1.aaaaaaaaaaaaaaaa", "slug": ""}, "slug"),
    ({"client_id": "Iv1.aaaaaaaaaaaaaaaa", "slug": "has space"}, "slug"), ({"client_id": "Iv1.aaaaaaaaaaaaaaaa", "slug": "https://github.com/apps/x"}, "slug"),
    ({"client_id": "Iv1.aaaaaaaaaaaaaaaa", "slug": "-bad"}, "slug")])
def test_bad_values_are_refused_with_the_field_and_save_nothing(brain, app_data, body, field):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/github-app", body)
    assert status == 422 and res["errors"][0]["field"] == field and gs.app_settings()["client_id"] == ""


def test_the_endpoint_needs_the_session_token_and_a_local_origin(brain, app_data):
    good = {"client_id": "Iv1.aaaaaaaaaaaaaaaa", "slug": "app"}
    with running_ui(brain) as ui:
        assert ui.post("/api/github-app", good, token=False)[0] == 403
        assert ui.post("/api/github-app", good, headers={"Origin": "https://evil.example.com"})[0] == 403
        assert ui.post("/api/github-app", good, headers={"Host": "evil.example.com"})[0] == 403
    assert gs.app_settings()["client_id"] == ""


def test_the_registration_steps_are_served_by_the_local_server_so_no_repo_path_is_needed(brain):
    with running_ui(brain) as ui:
        status, text = ui.get("/github-app-registration.md")
    assert status == 200 and "Enable Device Flow" in text and "client ID" in text


def test_the_steps_ship_inside_the_package():
    from importlib.resources import files

    assert "Enable Device Flow" in (files("mdpipeline") / "ui_static" / "github-app-registration.md").read_text()


def test_the_packaged_steps_match_the_documented_ones():
    from importlib.resources import files

    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    assert (files("mdpipeline") / "ui_static" / "github-app-registration.md").read_text() == (root / "docs" / "github-app-registration.md").read_text()
