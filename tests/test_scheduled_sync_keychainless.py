"""MD-163 AC4/AC9: no OS keychain -> first run (no passphrase step) -> the hourly scheduled sync (time faked) works; after a restart of every
process the next scheduled sync is ok with no user action. (MD-161 had it wait, amber, for a passphrase; that state no longer exists.)"""
import json
from pathlib import Path
import time

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, notices, usage, vault
from mdpipeline.log import ActivityLog
from mdpipeline.scheduler import run_due
from mdpipeline.status import locked_vault_run
from mdpipeline.usage import UsageStore
from test_jira_sync import EMAIL, TOKEN
from uihelper import running_ui
from vaultperm import is_user_only

HOUR = 3600


@pytest.fixture
def first_run(brain, app_data, tmp_path):
    """The real first run through the UI server with NO keychain: token tested, files written. No passphrase step."""
    app_data.keychain.available = False
    fake = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    fake.add_issue("ACME", "Issue number 1")
    jira_api.set_transport(fake, sleep=lambda s: None)
    with running_ui(brain) as ui:
        _, tested = ui.post("/api/first-run/jira-test", {"site": "acme", "email": EMAIL, "token": TOKEN})
        draft = {"source": "jira", "site": "acme", "email": EMAIL, "pending": tested["pending"], "dest": str(tmp_path / "out")}
        status, res = ui.post("/api/first-run/write", draft)
        assert status == 200 and res["ok"] is True
    yield brain
    jira_api.set_transport(None)


def schedule_runs(brain):
    return [r for r in ActivityLog(brain).read(types={"run"}) if r["trigger"] == "schedule"]


def test_the_first_run_needs_no_passphrase_and_keeps_the_token_in_the_user_only_file(first_run, app_data):
    path = app_data.dir / "vault.json"
    assert vault.default_vault().describe()["protection"] == "file" and is_user_only(path)
    assert TOKEN not in path.read_text() or json.loads(path.read_text())["kdf"] == "file"      # plain by design: the file is the protection


def test_the_scheduled_sync_works_in_a_background_process_that_was_never_told_anything(first_run):
    vault.reset_session()                       # the background process: a different process, it never saw the UI
    results = run_due(first_run, now=time.time() + HOUR + 5)
    assert [(r.trigger, r.result) for r in results] == [("schedule", "ok")]


def test_after_a_restart_of_everything_the_next_scheduled_sync_is_ok_with_no_user_action(first_run, app_data):
    vault.reset_session()
    run_due(first_run, now=time.time() + HOUR + 5)
    vault.set_keychain(type(app_data.keychain)(available=False))      # a reboot: new processes, nothing in memory, still no keychain
    vault.reset_session()
    UsageStore().set_consent("share")
    results = run_due(first_run, now=time.time() + 2 * HOUR + 10)
    assert [r.result for r in results] == ["ok"]
    runs = schedule_runs(first_run)
    assert len(runs) == 2 and not any(locked_vault_run(r) for r in runs)
    queue = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    assert [e for e in queue if e["event"] in ("sync_failed",) or (e["event"] == "notice_shown" and e["properties"]["kind"] == "vault_locked")] == []
    assert notices.check(first_run, now=time.time()) == []


def test_the_token_is_only_in_the_secrets_file_never_in_the_output_the_brain_the_logs_or_the_usage_queue(first_run, app_data, tmp_path):
    from secret_search import files_containing

    UsageStore().set_consent("share")
    vault.reset_session()
    run_due(first_run, now=time.time() + HOUR + 5)
    roots = [tmp_path / "out", first_run.root]
    assert files_containing(TOKEN, *roots) == []
    assert [Path(h).name for h in files_containing(TOKEN, app_data.dir)] == ["vault.json"]        # and nowhere else in the app data (usage queue, schedule, logs)
