"""MD-39: state backups (last good generations) and restore, from the CLI and the UI."""
import json
import time

import pytest

from conftest import tree_snapshot, write
from mdpipeline.cli import main
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.recovery import RestoreError, apply_restore, preview_restore
from mdpipeline.state import FileRecord, State, StateError
from uihelper import running_ui


def save_n(brain, n):
    st = State.load(brain)
    for i in range(n):
        st.pipeline("p").files[f"raw/p/f{i}.md"] = FileRecord("l", f"f{i}.md", f"h{i}", f"w{i}")
        st.save()


def backups(brain):
    d = brain.config_dir / "backups"
    return sorted(d.glob("state-*.json")) if d.is_dir() else []


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": f"note {i} SECRET-BODY" for i in range(6)})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    assert sync_pipeline(brain, "p").result == "ok"
    return src


# backups -----------------------------------------------------------------------------------------------
def test_the_first_save_has_no_backup_and_later_saves_keep_the_previous_valid_state(brain):
    save_n(brain, 1)
    assert backups(brain) == []
    save_n(brain, 1)          # second save: the state written by the first becomes a backup
    newest = json.loads((brain.config_dir / "backups/state-1.json").read_text())
    assert "raw/p/f0.md" in newest["pipelines"]["p"]["files"]


def test_at_least_three_generations_are_kept_and_the_oldest_are_dropped(brain):
    save_n(brain, 12)
    names = [b.name for b in backups(brain)]
    assert len(names) >= 3 and len(names) <= 5
    newest = json.loads((brain.config_dir / "backups/state-1.json").read_text())
    older = json.loads((brain.config_dir / f"backups/state-{len(names)}.json").read_text())
    assert len(newest["pipelines"]["p"]["files"]) > len(older["pipelines"]["p"]["files"])


def test_a_corrupt_state_is_never_rotated_into_the_backups(brain):
    save_n(brain, 4)
    (brain.config_dir / "state.json").write_text("{broken")
    State(brain, {}).save()          # a save that does not read the old state first
    assert backups(brain) and all(b.read_bytes() != b"{broken" for b in backups(brain))
    assert all(json.loads(b.read_text()) for b in backups(brain))


def test_backups_are_git_ignored_even_in_a_brain_created_before_this_feature(brain):
    (brain.config_dir / ".gitignore").write_text("state.json\nlog.jsonl\nlock\n")      # a P1 brain
    save_n(brain, 3)
    assert "backups/" in (brain.config_dir / ".gitignore").read_text().split()


def test_backups_hold_no_document_content(brain, synced):
    sync_pipeline(brain, "p")
    write(synced / "n0.md", "changed SECRET-BODY two")
    sync_pipeline(brain, "p")
    assert backups(brain)
    assert all("SECRET-BODY" not in b.read_text() for b in backups(brain))


def test_no_temp_files_are_left_behind(brain):
    save_n(brain, 6)
    assert not [p for p in brain.config_dir.rglob("*.tmp")]


# corrupt state stops the sync and offers the restore ------------------------------------------------------
def test_unreadable_state_stops_the_sync_with_a_restore_hint_and_no_empty_fallback(brain, synced, capsys):
    sync_pipeline(brain, "p")
    (brain.config_dir / "state.json").write_text("{corrupt")
    before = tree_snapshot(brain.raw_dir)
    with pytest.raises(StateError) as exc:
        sync_pipeline(brain, "p")
    assert "mdpipeline state restore" in str(exc.value)
    assert tree_snapshot(brain.raw_dir) == before
    code = main(["--brain", str(brain.root), "sync", "p"])
    assert code == 1 and "state restore" in capsys.readouterr().err


# restore -------------------------------------------------------------------------------------------------
def corrupt(brain):
    (brain.config_dir / "state.json").write_text("{corrupt")


def test_restore_preview_runs_a_dry_run_and_writes_nothing(brain, synced):
    sync_pipeline(brain, "p")
    corrupt(brain)
    before = (tree_snapshot(brain.raw_dir), (brain.config_dir / "state.json").read_bytes())
    pv = preview_restore(brain)
    assert pv.owned_files == 6
    plan = pv.plans["p"]
    assert plan.counts()["unchanged"] == 6 and plan.counts()["conflicts"] == 0
    assert (tree_snapshot(brain.raw_dir), (brain.config_dir / "state.json").read_bytes()) == before


def test_apply_restore_gives_back_ownership_of_every_file_and_loses_nothing(brain, synced):
    sync_pipeline(brain, "p")
    files_before = {p.name: p.read_bytes() for p in (brain.root / "raw/p/docs").glob("*.md")}
    corrupt(brain)
    apply_restore(brain)
    res = sync_pipeline(brain, "p")
    assert res.result == "ok" and res.counts()["unchanged"] == 6 and res.counts()["conflicts"] == 0
    assert {p.name: p.read_bytes() for p in (brain.root / "raw/p/docs").glob("*.md")} == files_before
    assert len(State.load(brain).pipeline("p").files) == 6


def test_without_the_restore_deleting_the_state_orphans_every_file(brain, synced):
    """The reason the feature exists (P1 behaviour): no state -> every copied file becomes a conflict (a)."""
    (brain.config_dir / "state.json").unlink()
    assert sync_pipeline(brain, "p").counts()["conflicts"] == 6


def test_the_corrupt_file_is_kept_aside_not_destroyed(brain, synced):
    sync_pipeline(brain, "p")
    corrupt(brain)
    apply_restore(brain)
    assert (brain.config_dir / "backups/state-before-restore.json").read_text() == "{corrupt"


def test_restore_can_pick_an_older_generation(brain, synced):
    for i in range(3):
        write(synced / "n0.md", f"edit {i}")
        sync_pipeline(brain, "p")
    pv = preview_restore(brain, generation=2)
    assert pv.generation == 2


def test_restore_without_any_valid_backup_is_a_clear_error(brain):
    corrupt(brain)
    with pytest.raises(RestoreError) as exc:
        preview_restore(brain)
    assert "no usable backup" in str(exc.value).lower()


def test_a_corrupt_newest_backup_falls_back_to_the_next_valid_one(brain, synced):
    sync_pipeline(brain, "p")
    sync_pipeline(brain, "p")
    (brain.config_dir / "backups/state-1.json").write_text("{bad")
    corrupt(brain)
    assert preview_restore(brain).generation == 2


# CLI -----------------------------------------------------------------------------------------------------
def test_cli_state_restore_shows_the_dry_run_first_and_applies_only_with_apply(brain, synced, capsys):
    sync_pipeline(brain, "p")
    corrupt(brain)
    code = main(["--brain", str(brain.root), "state", "restore"])
    out = capsys.readouterr().out
    assert code == 0 and "unchanged 6" in out and "--apply" in out
    assert (brain.config_dir / "state.json").read_text() == "{corrupt"
    code = main(["--brain", str(brain.root), "state", "restore", "--apply"])
    assert code == 0 and "restored" in capsys.readouterr().out.lower()
    assert sync_pipeline(brain, "p").counts()["unchanged"] == 6


def test_cli_state_restore_reports_a_missing_backup_with_exit_2(brain, capsys):
    corrupt(brain)
    assert main(["--brain", str(brain.root), "state", "restore"]) == 2
    assert "no usable backup" in capsys.readouterr().err.lower()


# UI "Needs you" ---------------------------------------------------------------------------------------------
def test_ui_needs_you_offers_the_restore_when_the_state_is_unreadable(brain, synced):
    sync_pipeline(brain, "p")
    corrupt(brain)
    with running_ui(brain) as ui:
        status, res = ui.get("/api/activity")
        assert status == 200
        card = res["needs_you"][0]
        assert card["kind"] == "state" and card["backups"] >= 1 and "state.json" in card["message"]
        status, pv = ui.post("/api/state/restore", {})
        assert status == 200 and pv["applied"] is False and pv["owned_files"] == 6
        assert (brain.config_dir / "state.json").read_text() == "{corrupt"
        status, done = ui.post("/api/state/restore", {"apply": True})
        assert status == 200 and done["applied"] is True
        _, again = ui.get("/api/activity")
    assert not [c for c in again["needs_you"] if c["kind"] == "state"]
    assert sync_pipeline(brain, "p").counts()["unchanged"] == 6


def test_ui_state_endpoint_still_answers_with_an_unreadable_state(brain, synced):
    corrupt(brain)
    with running_ui(brain) as ui:
        status, res = ui.get("/api/state")
    assert status == 200 and res["state_error"] and res["badge"] == 1
