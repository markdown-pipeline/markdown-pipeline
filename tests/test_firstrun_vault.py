"""MD-155 -> MD-163: the first run on a machine with no OS keychain keeps the token in the user-only file; it never asks for a passphrase."""
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, vault
from mdpipeline.usage_events import EVENTS
from test_jira_sync import EMAIL, TOKEN
from uihelper import running_ui
from vaultperm import WINDOWS, is_user_only, widen


@pytest.fixture
def jira():
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_issue("ACME", "Issue number 1")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


@pytest.fixture
def no_keychain(app_data):
    app_data.keychain.available = False
    return app_data


def body(**kw):
    return {"site": "acme", "email": EMAIL, "token": TOKEN, **kw}


def test_readiness_with_a_working_keychain_is_ready(app_data):
    assert vault.readiness() == {"ready": True, "mode": None, "store": "keychain"}


def test_readiness_without_a_keychain_is_ready_no_passphrase(no_keychain):
    assert vault.readiness() == {"ready": True, "mode": None, "store": "file"}


def test_first_run_progress_tells_the_connect_step_which_store_is_used(brain, no_keychain):
    with running_ui(brain) as ui:
        _, res = ui.get("/api/first-run/progress")
    assert res["vault"] == {"ready": True, "mode": None, "store": "file"}


def test_jira_test_on_a_keychain_less_machine_passes_and_needs_no_passphrase(brain, jira, no_keychain):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", body())
    assert res["ok"] is True and res["pending"] and res["vault"] == {"ready": True, "mode": None, "store": "file"}
    assert TOKEN not in json.dumps(res)


def test_jira_test_with_a_keychain_says_the_vault_is_ready(brain, jira, app_data):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", body())
    assert res["vault"] == {"ready": True, "mode": None, "store": "keychain"}


def test_the_kept_token_reaches_written_files_with_no_passphrase_step(brain, jira, no_keychain, tmp_path):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        _, tested = ui.post("/api/first-run/jira-test", body())
        draft = {"source": "jira", "site": "acme", "email": EMAIL, "pending": tested["pending"], "dest": str(dest)}
        status, res = ui.post("/api/first-run/write", draft)
    assert status == 200 and res["ok"] is True and res["files"] >= 1
    stored = json.loads((no_keychain.dir / "vault.json").read_text())
    assert stored["kdf"] == "file" and is_user_only(no_keychain.dir / "vault.json")


def test_an_unsafe_secrets_file_is_reported_by_the_first_run_progress_with_the_fix(brain, jira, no_keychain):
    vault.default_vault().add("jira-token", "t", "x")
    widen(no_keychain.dir / "vault.json")
    with running_ui(brain) as ui:
        _, res = ui.get("/api/first-run/progress")
    assert res["vault"]["ready"] is False and res["vault"]["mode"] == "unsafe" and ("PowerShell" if WINDOWS else "chmod 600") in res["vault"]["message"]


def test_secret_store_used_replaces_the_passphrase_event_and_carries_only_the_store():
    assert "vault_passphrase_needed" not in EVENTS
    event = EVENTS["secret_store_used"]
    assert event.surfaces == ("ui",) and set(event.props) == {"store"}
    assert set(event.props["store"].values) == {"keychain", "file"}


# AC3: a sync that stops on a locked vault is "needs you" (amber), not an error --------------------------------------
from mdpipeline.status import only_missing_sources  # noqa: E402


def test_a_run_stopped_by_a_locked_vault_is_needs_you_not_an_error():
    run = {"result": "error", "category": "vault-locked", "errors": []}
    assert only_missing_sources(run) is True


def test_a_locked_vault_source_error_is_needs_you():
    run = {"result": "error", "errors": [{"source": "acme", "error": "locked", "category": "vault-locked"}]}
    assert only_missing_sources(run) is True


def test_a_locked_vault_next_to_a_real_failure_keeps_the_error():
    run = {"result": "error", "errors": [{"category": "vault-locked"}, {"category": "network"}]}
    assert only_missing_sources(run) is False


# AC7 / AC9: the words ---------------------------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

APP = Path(__file__).resolve().parent.parent


def test_readme_troubleshooting_names_secret_tool_as_the_keychain_option():
    text = (APP / "docs" / "reference.md").read_text()
    assert "libsecret-tools" in text and "secret-tool" in text and "0600" in text


def test_what_we_send_says_files_may_go_to_a_github_repo():
    assert "writes Markdown files to a folder on your computer, or to a GitHub repo you choose." in (APP / "docs" / "what-we-send.md").read_text().replace("\n", " ")
