"""MD-88: a Jira source's email and API token are entered in the app (hidden prompt, stdin, UI fields) and kept in the vault."""
import io
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import cli, credentials, jira_api, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.secretimport import import_references, pending_imports
from secret_search import files_containing
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source
from uihelper import running_ui

JIRA = ["--jira-label", "jira", "--jira-site", SITE, "--jira-jql", "project = MD"]


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


def run(capsys, monkeypatch, *argv, brain, stdin=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    try:
        code = main(["--brain", str(brain.root), *argv])
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


def kinds():
    return sorted(s.kind for s in vault_mod.default_vault().list())


def test_add_with_an_email_and_a_token_from_stdin_validates_then_keeps_both_in_the_vault(brain, fake, capsys, monkeypatch, app_data):
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin=TOKEN + "\n")
    assert code == 0, err
    src = load_config(brain).get("p").sources[0]
    assert src.options["email_ref"].startswith("vault:") and src.options["token_ref"].startswith("vault:") and src.options["email_ref"] != src.options["token_ref"]
    assert kinds() == ["jira-email", "jira-token"] and TOKEN not in out + err
    assert files_containing(TOKEN, brain.root, app_data.dir) == [] and files_containing(EMAIL, brain.root, app_data.dir) == []
    assert sync_pipeline(brain, "p", trigger="cli").counts()["created"] == 3


def test_the_token_comes_from_a_hidden_prompt_when_interactive(brain, fake, capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": TOKEN)
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, brain=brain)
    assert code == 0, err and TOKEN not in out + err and kinds() == ["jira-email", "jira-token"]


def test_a_wrong_token_saves_nothing_stores_nothing_and_is_not_echoed(brain, fake, capsys, monkeypatch):
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin="ATATT-WRONG-TOKEN-VALUE\n")
    assert code == 2 and "rejected" in err and "ATATT-WRONG" not in out + err
    assert load_config(brain).get("p") is None and vault_mod.default_vault().list() == []


def test_a_failing_pipeline_save_leaves_no_secrets_behind(brain, fake, capsys, monkeypatch):
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "bad name!", *JIRA, "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin=TOKEN + "\n")
    assert code == 2 and vault_mod.default_vault().list() == []


def test_without_a_terminal_and_without_stdin_no_token_is_read(brain, fake, capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: False)
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, brain=brain)
    assert code == 2 and "--jira-stdin" in err and load_config(brain).get("p") is None


def test_a_token_given_as_an_argument_is_still_refused(brain, fake, capsys, monkeypatch):
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, "--jira-token", TOKEN, brain=brain)
    assert code == 2 and TOKEN not in out + err and load_config(brain).get("p") is None


def test_old_reference_flags_still_work(brain, fake, capsys, monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN", brain=brain)
    assert code == 0, err and load_config(brain).get("p").sources[0].options["token_ref"] == "env:JIRA_TOKEN" and vault_mod.default_vault().list() == []


def test_jira_test_accepts_typed_credentials_without_storing_them(brain, fake, capsys, monkeypatch):
    code, out, err = run(capsys, monkeypatch, "jira", "test", "--jira-site", SITE, "--jira-jql", "project = MD", "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin=TOKEN + "\n")
    assert code == 0, err and "Mia Owner" in out and "3 issues" in out and vault_mod.default_vault().list() == [] and TOKEN not in out + err


def test_replace_the_token_validates_first_and_keeps_the_old_one_when_refused(brain, fake, capsys, monkeypatch):
    run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin=TOKEN + "\n")
    token_id = load_config(brain).get("p").sources[0].options["token_ref"][len("vault:"):]
    code, out, err = run(capsys, monkeypatch, "pipeline", "edit", "p", "--jira-label", "jira", "--replace-jira-token", "--jira-stdin", brain=brain, stdin="ATATT-NOPE-NOPE\n")
    assert code == 2 and "ATATT-NOPE" not in out + err and vault_mod.default_vault().get(token_id) == TOKEN
    fake.token = "ATATT-ROTATED-TOKEN"
    code, out, err = run(capsys, monkeypatch, "pipeline", "edit", "p", "--jira-label", "jira", "--replace-jira-token", "--jira-stdin", brain=brain, stdin="ATATT-ROTATED-TOKEN\n")
    assert code == 0, err and vault_mod.default_vault().get(token_id) == "ATATT-ROTATED-TOKEN"


def test_a_locked_vault_stops_the_jira_sync_without_deleting_anything(brain, fake, capsys, monkeypatch, app_data):
    run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin=TOKEN + "\n")
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    before = sorted(str(p) for p in (brain.root / "raw").rglob("*.md"))
    app_data.keychain.lose()
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and res.counts()["deleted"] == 0
    assert sorted(str(p) for p in (brain.root / "raw").rglob("*.md")) == before


# --- the one-time import of old references -------------------------------------------------------------------------------------
@pytest.fixture
def legacy(brain, fake, monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline("p", [jira_source()], name_policy="date-name"))
    save_config(brain, cfg)
    return brain


def test_old_jira_references_are_detected_and_keep_working_until_imported(legacy):
    refs = {i.ref: i for i in pending_imports(legacy)}
    assert set(refs) == {"env:JIRA_EMAIL", "env:JIRA_TOKEN"} and refs["env:JIRA_TOKEN"].secret_kind == "jira-token" and refs["env:JIRA_EMAIL"].secret_kind == "jira-email"
    assert sync_pipeline(legacy, "p", trigger="cli").result == "ok"


def test_import_moves_the_jira_email_and_token_into_the_vault_and_stops_reading_the_environment(legacy, monkeypatch):
    results = import_references(legacy)
    assert all(r.ok for r in results) and kinds() == ["jira-email", "jira-token"]
    opts = load_config(legacy).get("p").sources[0].options
    assert opts["email_ref"].startswith("vault:") and opts["token_ref"].startswith("vault:")
    monkeypatch.delenv("JIRA_TOKEN")
    monkeypatch.setenv("JIRA_EMAIL", "someone@else.example")
    assert sync_pipeline(legacy, "p", trigger="cli").result == "ok"


def test_after_a_full_life_the_jira_email_and_token_are_nowhere_but_encrypted_in_the_vault(brain, fake, capsys, monkeypatch, app_data):
    outputs = []
    run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin=TOKEN + "\n")
    for argv in (["sync", "p"], ["sync", "p", "--json"], ["status", "--json"], ["pipeline", "list", "--json"], ["log", "--json"], ["vault", "list", "--json"], ["vault", "status"]):
        outputs += list(run(capsys, monkeypatch, *argv, brain=brain)[1:])
    fake.fail("GET", "/search/jql", status=503, times=1)
    outputs += list(run(capsys, monkeypatch, "sync", "p", brain=brain)[1:])
    text = "\n".join(outputs)
    assert TOKEN not in text and EMAIL not in text
    assert files_containing(TOKEN, brain.root, app_data.dir) == [] and files_containing(EMAIL, brain.root, app_data.dir) == []


# --- the UI --------------------------------------------------------------------------------------------------------------------
def source_draft(**kw):
    return {"type": "jira", "label": "jira", "site": SITE, "jql": "project = MD", **kw}


def draft(*sources, name="p"):
    return {"name": name, "sources": list(sources), "name_policy": "date-name", "guard_threshold_percent": 100}


def test_ui_test_connection_with_typed_values_returns_a_handle_not_the_values_and_stores_nothing(brain, fake):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/jira-test", source_draft(email=EMAIL, token=TOKEN))
    assert status == 200 and res["ok"] is True and res["user"] == "Mia Owner" and res["pending"] and TOKEN not in json.dumps(res) and EMAIL not in json.dumps(res)
    assert vault_mod.default_vault().list() == []


def test_ui_wrong_token_is_a_plain_error(brain, fake):
    with running_ui(brain) as ui:
        res = ui.post("/api/jira-test", source_draft(email=EMAIL, token="ATATT-WRONG-TOKEN-VALUE"))[1]
    assert res["ok"] is False and "rejected" in res["errors"][0]["message"] and "pending" not in res and "ATATT-WRONG" not in json.dumps(res)


def test_ui_preview_uses_a_tested_handle_and_saving_with_it_keeps_the_values_in_the_vault_only(brain, fake, app_data):
    with running_ui(brain) as ui:
        _, tested = ui.post("/api/jira-test", source_draft(email=EMAIL, token=TOKEN))
        src = source_draft(pending=tested["pending"])
        _, pv = ui.post("/api/preview", {"draft": draft(src), "editing": None})
        assert pv["preview"]["counts"]["create"] == 3
        status, res = ui.post("/api/pipelines", {"draft": draft(src), "sync": True})
        assert status == 200 and res["sync"]["counts"]["created"] == 3 and TOKEN not in json.dumps(res)
        _, view = ui.get("/api/pipelines/p")
        _, state = ui.get("/api/state")
    opts = load_config(brain).get("p").sources[0].options
    assert opts["token_ref"].startswith("vault:") and "pending" not in opts and "_token" not in opts
    assert kinds() == ["jira-email", "jira-token"]
    assert TOKEN not in json.dumps(view) + json.dumps(state) and EMAIL not in json.dumps(view) + json.dumps(state)
    assert files_containing(TOKEN, brain.root, app_data.dir) == [] and files_containing(EMAIL, brain.root, app_data.dir) == []


def test_ui_saving_with_typed_values_and_a_wrong_token_saves_nothing(brain, fake):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft(source_draft(email=EMAIL, token="ATATT-WRONG-TOKEN-VALUE"))})
    assert status == 422 and "rejected" in json.dumps(res) and load_config(brain).get("p") is None and vault_mod.default_vault().list() == []


def test_ui_editing_a_saved_jira_source_can_replace_the_token_and_keeps_the_old_one_if_refused(brain, fake):
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(source_draft(email=EMAIL, token=TOKEN))})
        view = ui.get("/api/pipelines/p")[1]["draft"]
        token_id = view["sources"][0]["token_ref"][len("vault:"):]
        status, res = ui.put("/api/pipelines/p", {"draft": {**view, "sources": [{**view["sources"][0], "token": "ATATT-NOPE-NOPE"}]}})
        assert status == 422 and vault_mod.default_vault().get(token_id) == TOKEN
        fake.token = "ATATT-ROTATED-TOKEN"
        status, res = ui.put("/api/pipelines/p", {"draft": {**view, "sources": [{**view["sources"][0], "token": "ATATT-ROTATED-TOKEN"}]}})
        assert status == 200, res
    assert vault_mod.default_vault().get(token_id) == "ATATT-ROTATED-TOKEN"


def test_ui_legacy_reference_sources_keep_working_and_the_import_card_lists_them(legacy):
    with running_ui(legacy) as ui:
        _, act = ui.get("/api/activity")
        card = next(c for c in act["needs_you"] if c["kind"] == "import")
        assert "env:JIRA_TOKEN" in json.dumps(card)
        status, res = ui.post("/api/vault/import", {})
    assert status == 200 and all(r["ok"] for r in res["results"]) and kinds() == ["jira-email", "jira-token"]


def test_a_rejected_vault_token_tells_the_person_to_enter_a_new_one_in_edit(brain, fake, capsys, monkeypatch):
    from mdpipeline.uiactivity import activity_view

    run(capsys, monkeypatch, "pipeline", "add", "p", *JIRA, "--jira-email", EMAIL, "--jira-stdin", brain=brain, stdin=TOKEN + "\n")
    sync_pipeline(brain, "p", trigger="cli")
    fake.token = "ATATT-REVOKED-NOW"
    assert sync_pipeline(brain, "p", trigger="ui").result == "error"
    [card] = [c for c in activity_view(brain)["needs_you"] if c["kind"] == "error"]
    step = card["connectors"][0]["next_step"]
    assert "new API token in Edit" in step and "vault:" not in step
