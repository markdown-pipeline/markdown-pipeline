"""MD-134: remove a pipeline from the app (UI parity with `mdpipeline pipeline remove`); the files are kept unless the user chooses to delete."""
import json

import pytest

from conftest import write
from mdpipeline import usage, vault as vault_mod
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.usage import UsageStore
from uihelper import running_ui


@pytest.fixture
def world(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "# A\n", "b.md": "# B\n", "c.md": "# C\n"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], name_policy="original"))
    sync_pipeline(brain, "p")
    return brain, src


def _brain_files(brain):
    return sorted(p.name for p in (brain.root / "raw" / "p").rglob("*.md"))


def _edit_in_brain(brain, name):
    path = next((brain.root / "raw" / "p").rglob(name))
    path.write_text(path.read_text(encoding="utf-8") + "\nmy own note\n", encoding="utf-8")


def _queued(*names):
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


# --- the preview: exact numbers before anything is deleted -------------------------------------------------------------
def test_preview_gives_the_exact_number_of_files_the_pipeline_created(world):
    brain, _ = world
    with running_ui(brain) as ui:
        status, res = ui.get("/api/pipelines/p/remove-preview")
    assert status == 200 and res["owned"] == 3 and res["deletable"] == 3 and res["edited"] == []


def test_preview_lists_an_edited_file_as_kept_and_does_not_count_it_as_deletable(world):
    brain, _ = world
    _edit_in_brain(brain, "b.md")
    with running_ui(brain) as ui:
        _, res = ui.get("/api/pipelines/p/remove-preview")
    assert res["owned"] == 3 and res["deletable"] == 2 and len(res["edited"]) == 1 and res["edited"][0].endswith("b.md")


def test_preview_writes_nothing(world):
    brain, _ = world
    before = _brain_files(brain)
    with running_ui(brain) as ui:
        ui.get("/api/pipelines/p/remove-preview")
    assert _brain_files(brain) == before and load_config(brain).get("p") is not None


def test_preview_of_an_unknown_pipeline_is_404(brain):
    with running_ui(brain) as ui:
        status, _ = ui.get("/api/pipelines/nope/remove-preview")
    assert status == 404


# --- keep the files (the default) ---------------------------------------------------------------------------------------
def test_removing_without_choosing_keeps_every_file_and_removes_the_pipeline(world):
    brain, _ = world
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/remove", {})
    assert status == 200 and res["removed"] is True and res["deleted"] == 0 and res["kept"] == 3
    assert res["message"] == '"p" removed. 3 files kept in your folder.'
    assert _brain_files(brain) == ["a.md", "b.md", "c.md"] and load_config(brain).get("p") is None


def test_kept_files_are_unowned_a_new_pipeline_of_the_same_name_does_not_touch_them(world, add_pipeline, make_source):
    brain, src = world
    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/remove", {})
    (src / "a.md").unlink()
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], name_policy="original"))
    sync_pipeline(brain, "p")
    assert "a.md" in _brain_files(brain), "the new pipeline must not delete a file the removed one left behind"


# --- delete the files this pipeline created -----------------------------------------------------------------------------
def test_deleting_needs_the_exact_count_it_was_shown(world):
    brain, _ = world
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/remove", {"delete_files": True, "confirm_count": 2})
    assert status == 409 and "3" in res["error"] and res["count"] == 3
    assert _brain_files(brain) == ["a.md", "b.md", "c.md"] and load_config(brain).get("p") is not None


def test_deleting_without_any_count_is_refused(world):
    brain, _ = world
    with running_ui(brain) as ui:
        status, _ = ui.post("/api/pipelines/p/remove", {"delete_files": True})
    assert status == 409 and _brain_files(brain) == ["a.md", "b.md", "c.md"]


def test_deleting_removes_the_unedited_files_and_keeps_the_ones_the_user_edited(world):
    brain, _ = world
    _edit_in_brain(brain, "b.md")
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/remove", {"delete_files": True, "confirm_count": 3})
    assert status == 200 and res["deleted"] == 2 and res["kept"] == 1
    assert res["message"] == '"p" removed. 2 files deleted; 1 kept because you edited it.'
    assert _brain_files(brain) == ["b.md"] and len(res["edited"]) == 1


def test_the_message_when_nothing_was_edited_has_no_kept_part(world):
    brain, _ = world
    with running_ui(brain) as ui:
        _, res = ui.post("/api/pipelines/p/remove", {"delete_files": True, "confirm_count": 3})
    assert res["message"] == '"p" removed. 3 files deleted.'


def test_the_message_for_one_kept_file_is_singular(world):
    brain, src = world
    for f in ("b.md", "c.md"):
        (src / f).unlink()
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/pipelines/p/remove", {})
    assert res["message"] == '"p" removed. 1 file kept in your folder.'


def test_the_message_for_several_edited_files_is_plural(world):
    brain, _ = world
    _edit_in_brain(brain, "a.md")
    _edit_in_brain(brain, "b.md")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/pipelines/p/remove", {"delete_files": True, "confirm_count": 3})
    assert res["message"] == '"p" removed. 1 file deleted; 2 kept because you edited them.'


def test_removing_never_touches_the_source_folder(world):
    brain, src = world
    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/remove", {"delete_files": True, "confirm_count": 3})
    assert sorted(p.name for p in src.iterdir()) == ["a.md", "b.md", "c.md"]


def test_removing_an_unknown_pipeline_is_404(brain):
    with running_ui(brain) as ui:
        status, _ = ui.post("/api/pipelines/nope/remove", {})
    assert status == 404


# --- the same logic as the CLI, scheduling, Activity ----------------------------------------------------------------------
def test_the_ui_goes_through_removal_remove_pipeline_and_not_a_second_implementation():
    import inspect

    from mdpipeline import uiremove

    assert "remove_pipeline(" in inspect.getsource(uiremove)


def test_the_scheduled_sync_stops_at_once(world):
    brain, _ = world

    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/remove", {})
        _, state = ui.get("/api/state")
    assert load_config(brain).pipelines == [] and state["pipelines"] == []


def test_the_removal_is_written_to_activity(world):
    brain, _ = world
    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/remove", {"delete_files": True, "confirm_count": 3})
    (event,) = [e for e in ActivityLog(brain).read() if e.get("action") == "remove"]
    assert event["pipeline"] == "p" and event["trigger"] == "ui" and event["deleted"] == 3


# --- credentials: removed from the vault only when nobody else uses them -----------------------------------------------
def _credential(name="my token"):
    return vault_mod.default_vault().add("jira-token", name, "secret-value")


def _pipeline_with_token(add_pipeline, make_source, name, secret_id):
    add_pipeline(Pipeline.new(name, [Source("https://x.atlassian.net", name, type="jira", options={"token_ref": f"vault:{secret_id}"})]))


def test_a_credential_only_this_pipeline_used_is_removed_from_the_vault_and_the_user_is_told(brain, add_pipeline, make_source):
    secret = _credential()
    _pipeline_with_token(add_pipeline, make_source, "p", secret)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/pipelines/p/remove", {})
    assert res["credentials_removed"] == ["my token"] and res["credentials_kept"] == []
    assert not vault_mod.default_vault().has(secret)


def test_the_preview_says_which_credentials_would_go_and_which_stay(brain, add_pipeline, make_source):
    shared, own = _credential("shared token"), _credential("own token")
    _pipeline_with_token(add_pipeline, make_source, "p", own)
    _pipeline_with_token(add_pipeline, make_source, "q", shared)
    cfg = load_config(brain)
    cfg.get("p").sources[0].options["email_ref"] = f"vault:{shared}"
    from mdpipeline.config import save_config

    save_config(brain, cfg)
    with running_ui(brain) as ui:
        _, res = ui.get("/api/pipelines/p/remove-preview")
    assert res["credentials_removed"] == ["own token"]
    assert res["credentials_kept"] == [{"name": "shared token", "used_by": ["q"]}]


def test_a_credential_another_pipeline_still_uses_stays_in_the_vault(brain, add_pipeline, make_source):
    secret = _credential("shared token")
    _pipeline_with_token(add_pipeline, make_source, "p", secret)
    _pipeline_with_token(add_pipeline, make_source, "q", secret)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/pipelines/p/remove", {})
    assert res["credentials_removed"] == [] and res["credentials_kept"] == [{"name": "shared token", "used_by": ["q"]}]
    assert vault_mod.default_vault().has(secret)


def test_a_locked_vault_does_not_block_the_removal(brain, add_pipeline, make_source, monkeypatch):
    secret = _credential()
    _pipeline_with_token(add_pipeline, make_source, "p", secret)

    def locked(*a, **k):
        raise vault_mod.VaultLocked("locked", needs_passphrase=True)

    monkeypatch.setattr(vault_mod.Vault, "remove", locked)
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/remove", {})
    assert status == 200 and load_config(brain).get("p") is None and res["credentials_removed"] == []


# --- usage: only the existing allowlisted events --------------------------------------------------------------------------
def test_removing_reports_source_removed_once_per_source_and_nothing_about_the_pipeline(world):
    brain, _ = world
    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/remove", {})
    events = _queued("source_removed")
    envelope = {"app_version", "deployment", "session_id", "is_internal", "platform", "os", "locale"}
    assert len(events) == 1 and set(events[0]["properties"]) - envelope == set()
    assert "docs" not in json.dumps(events) and '"p"' not in json.dumps(events)


def test_nothing_is_reported_when_sharing_is_off(world):
    brain, _ = world
    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/remove", {})
    assert _queued("source_removed") == []
