"""MD-40 (Should): rebuild the ownership state from the provenance in raw/ when no backup is usable."""
from datetime import datetime, timezone

import pytest

from conftest import tree_snapshot, write
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, save_config, Config
from mdpipeline.engine import sync_pipeline
from mdpipeline.naming import SEPARATOR as S
from mdpipeline.rebuild import RebuildRefused, apply_rebuild, rebuild_preview
from mdpipeline.state import State
from uihelper import running_ui

PAST = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    src = make_source("docs", {"Notes.md": "notes\n", "sub/Plan.md": "plan\n", "other.md": "other\n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    assert sync_pipeline(brain, "p", now=PAST).result == "ok"
    return src


def lose_state(brain):
    (brain.config_dir / "state.json").unlink()
    for b in (brain.config_dir / "backups").glob("*"):
        b.unlink()


def test_rebuild_re_owns_every_exact_match_with_names_taken_from_provenance(brain, synced):
    files_before = {str(p): p.read_bytes() for p in (brain.root / "raw").rglob("*.md")}
    lose_state(brain)
    pv = apply_rebuild(brain)
    assert pv.applied and sum(len(c) for c in pv.claims.values()) == 3 and pv.unclaimed == []
    rec = State.load(brain).pipeline("p").files[f"raw/p/docs/2026-09-01{S}notes.md"]
    assert (rec.first_seen, rec.name_policy, rec.suffix) == ("2026-09-01", "date-name", None)
    res = sync_pipeline(brain, "p", now=PAST + 86400 * 40)       # much later: names must not be recomputed
    assert res.result == "ok" and res.counts()["unchanged"] == 3 and res.counts()["conflicts"] == 0
    assert {str(p): p.read_bytes() for p in (brain.root / "raw").rglob("*.md")} == files_before


def test_the_clash_suffix_is_taken_from_provenance_too(brain, add_pipeline, make_source):
    a, b = make_source("a", {"Notes.md": "a\n"}), make_source("b", {"notes.md": "b\n"})
    add_pipeline(Pipeline("p", [Source(str(a), "a"), Source(str(b), "b")], target="raw/p/{relpath}", name_policy="date-name"))
    sync_pipeline(brain, "p", now=PAST)
    names = sorted(p.name for p in (brain.root / "raw/p").glob("*.md"))
    lose_state(brain)
    apply_rebuild(brain)
    assert sorted(r.suffix is not None for r in State.load(brain).pipeline("p").files.values()) == [False, True]
    assert sync_pipeline(brain, "p", now=PAST + 86400).counts()["unchanged"] == 2
    assert sorted(p.name for p in (brain.root / "raw/p").glob("*.md")) == names


def test_preview_writes_nothing_and_reports_the_dry_run(brain, synced):
    lose_state(brain)
    before = (tree_snapshot(brain.raw_dir), sorted(p.name for p in brain.config_dir.iterdir()))
    pv = rebuild_preview(brain)
    assert not pv.applied and pv.plans["p"].counts()["unchanged"] == 3
    assert (tree_snapshot(brain.raw_dir), sorted(p.name for p in brain.config_dir.iterdir())) == before


def test_a_brain_copy_that_was_edited_is_not_claimed_and_is_listed(brain, synced):
    edited = brain.root / f"raw/p/docs/2026-09-01{S}notes.md"
    edited.write_text(edited.read_text() + "my edit\n")
    lose_state(brain)
    pv = apply_rebuild(brain)
    assert [u.path for u in pv.unclaimed] == [f"raw/p/docs/2026-09-01{S}notes.md"]
    assert "cannot tell whether the source or the copy in your output folder changed" in pv.unclaimed[0].reason
    assert f"raw/p/docs/2026-09-01{S}notes.md" not in State.load(brain).pipeline("p").files
    assert "my edit" in edited.read_text()


def test_the_next_sync_reports_the_unclaimed_file_as_a_conflict_with_that_message(brain, synced):
    edited = brain.root / f"raw/p/docs/2026-09-01{S}notes.md"
    edited.write_text(edited.read_text() + "my edit\n")
    lose_state(brain)
    apply_rebuild(brain)
    res = sync_pipeline(brain, "p", now=PAST + 86400)
    c = next(c for c in res.conflicts if c["path"].endswith("notes.md"))
    assert c["type"] == "a" and "cannot tell whether the source or the copy in your output folder changed" in c["reason"]
    assert "my edit" in edited.read_text()


def test_a_source_changed_since_the_last_sync_is_a_mismatch_too(brain, synced):
    write(synced / "Notes.md", "changed at the source\n")
    lose_state(brain)
    pv = apply_rebuild(brain)
    assert any(u.path.endswith("notes.md") and "cannot tell" in u.reason for u in pv.unclaimed)
    assert len(State.load(brain).pipeline("p").files) == 2


def test_a_hand_written_note_with_copied_provenance_is_never_claimed(brain, synced):
    real = (brain.root / f"raw/p/docs/2026-09-01{S}notes.md").read_text()
    forged = write(brain.root / "raw/p/docs/my-note.md", real.replace("notes\n", "I wrote this myself\n"))
    plain = write(brain.root / "raw/p/docs/plain.md", "no provenance at all\n")
    lose_state(brain)
    pv = apply_rebuild(brain)
    claimed = State.load(brain).pipeline("p").files
    assert "raw/p/docs/my-note.md" not in claimed and "raw/p/docs/plain.md" not in claimed
    assert forged.read_text().endswith("I wrote this myself\n") and plain.read_text() == "no provenance at all\n"
    assert any(u.path == "raw/p/docs/my-note.md" for u in pv.unclaimed)


def test_provenance_naming_a_pipeline_or_source_that_does_not_exist_is_not_claimed(brain, synced):
    text = (brain.root / f"raw/p/docs/2026-09-01{S}notes.md").read_text()
    write(brain.root / "raw/p/docs/ghost-pipeline.md", text.replace("pipeline: p", "pipeline: ghost"))
    write(brain.root / "raw/p/docs/ghost-source.md", text.replace("source_label: docs", "source_label: nowhere"))
    lose_state(brain)
    pv = apply_rebuild(brain)
    reasons = {u.path: u.reason for u in pv.unclaimed}
    assert "does not exist" in reasons["raw/p/docs/ghost-pipeline.md"]
    assert "not a source folder" in reasons["raw/p/docs/ghost-source.md"]
    assert len(State.load(brain).pipeline("p").files) == 3


def test_two_files_claiming_one_source_identity_are_both_left_unclaimed(brain, synced):
    path = brain.root / f"raw/p/docs/2026-09-01{S}notes.md"
    write(brain.root / "raw/p/docs/copy-of-notes.md", path.read_text())
    lose_state(brain)
    pv = apply_rebuild(brain)
    assert len(pv.unclaimed) == 2 and all("same source" in u.reason for u in pv.unclaimed)
    assert len(State.load(brain).pipeline("p").files) == 2


def test_a_file_outside_the_directory_the_pipeline_writes_to_is_not_claimed(brain, synced):
    text = (brain.root / f"raw/p/docs/2026-09-01{S}other.md").read_text()
    (brain.root / f"raw/p/docs/2026-09-01{S}other.md").unlink()
    write(brain.root / "raw/elsewhere/other.md", text)
    lose_state(brain)
    pv = apply_rebuild(brain)
    assert any(u.path == "raw/elsewhere/other.md" and "pipeline writes" in u.reason for u in pv.unclaimed)


def test_phase_1_files_without_name_fields_are_claimed_as_original(brain, add_pipeline, make_source):
    src = make_source("docs", {"A b.md": "x\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", now=PAST)
    lose_state(brain)
    apply_rebuild(brain)
    rec = State.load(brain).pipeline("p").files["raw/p/docs/A b.md"]
    assert rec.name_policy is None and sync_pipeline(brain, "p").counts()["unchanged"] == 1


def test_it_refuses_while_the_state_is_readable_and_not_empty(brain, synced):
    with pytest.raises(RebuildRefused) as exc:
        rebuild_preview(brain)
    assert "state restore" in str(exc.value)


def test_it_works_on_an_unreadable_state_and_keeps_that_file_aside(brain, synced):
    for b in (brain.config_dir / "backups").glob("*"):
        b.unlink()
    (brain.config_dir / "state.json").write_text("{corrupt")
    apply_rebuild(brain)
    assert (brain.config_dir / "backups/state-before-rebuild.json").read_text() == "{corrupt"
    assert sync_pipeline(brain, "p").counts()["unchanged"] == 3


def policy_drift(brain, add_pipeline, make_source):
    """10 files synced under date-name, then the pipeline is switched to `name` and the state is lost: the next
    sync would rename all 10 (above the guard), and the rebuild has to show and respect that."""
    from mdpipeline.config import load_config
    src = make_source("docs", {f"n{i}.md": f"{i}\n" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=PAST)
    cfg = load_config(brain)
    cfg.get("p").name_policy = "name"
    save_config(brain, cfg)
    lose_state(brain)


def test_the_guard_applies_to_the_rebuild(brain, add_pipeline, make_source):
    policy_drift(brain, add_pipeline, make_source)
    pv = rebuild_preview(brain)
    assert pv.claimed_total == 10 and pv.plans["p"].counts()["renamed"] == 10
    assert pv.plans["p"].guard.blocked and pv.needs_approval
    with pytest.raises(RebuildRefused) as exc:
        apply_rebuild(brain)
    assert "--approve" in str(exc.value)
    assert not (brain.config_dir / "state.json").exists()
    assert apply_rebuild(brain, approve=True).applied


def test_the_rebuild_never_writes_to_raw(brain, synced):
    lose_state(brain)
    before = tree_snapshot(brain.raw_dir)
    apply_rebuild(brain)
    assert tree_snapshot(brain.raw_dir) == before


def test_cli_rebuild_previews_then_applies(brain, synced, capsys):
    lose_state(brain)
    code = main(["--brain", str(brain.root), "state", "rebuild"])
    out = capsys.readouterr().out
    assert code == 0 and "3 files would be owned again" in out and not (brain.config_dir / "state.json").exists()
    code = main(["--brain", str(brain.root), "state", "rebuild", "--apply"])
    assert code == 0 and (brain.config_dir / "state.json").exists()
    assert sync_pipeline(brain, "p").counts()["unchanged"] == 3


def test_cli_rebuild_blocked_by_the_guard_exits_3(brain, add_pipeline, make_source, capsys):
    policy_drift(brain, add_pipeline, make_source)
    assert main(["--brain", str(brain.root), "state", "rebuild", "--apply"]) == 3
    assert main(["--brain", str(brain.root), "state", "rebuild", "--apply", "--approve"]) == 0


# MD-42: tell the owner how to resolve a copy the rebuild did not claim -------------------------------------------
from mdpipeline.planner import NEXT_STEP


def unclaimed_after_rebuild(brain, synced):
    edited = brain.root / f"raw/p/docs/2026-09-01{S}notes.md"
    edited.write_text(edited.read_text() + "my edit\n")
    lose_state(brain)
    apply_rebuild(brain)
    return edited


def test_next_step_text_says_move_or_delete_then_sync_and_recreated_from_the_source():
    assert "move your copy out of the output folder" in NEXT_STEP and "delete" in NEXT_STEP and "sync" in NEXT_STEP
    assert "recreated from the source" in NEXT_STEP


def test_cli_rebuild_output_tells_the_owner_what_to_do(brain, synced, capsys):
    edited = brain.root / f"raw/p/docs/2026-09-01{S}notes.md"
    edited.write_text(edited.read_text() + "my edit\n")
    lose_state(brain)
    main(["--brain", str(brain.root), "state", "rebuild"])
    assert NEXT_STEP in capsys.readouterr().out


def test_cli_sync_output_and_status_show_the_next_step(brain, synced, capsys):
    unclaimed_after_rebuild(brain, synced)
    main(["--brain", str(brain.root), "sync", "p"])
    assert NEXT_STEP in capsys.readouterr().out
    main(["--brain", str(brain.root), "status"])
    assert NEXT_STEP in capsys.readouterr().out


def test_following_the_steps_clears_the_item_and_the_pipeline_owns_it_again(brain, synced):
    edited = unclaimed_after_rebuild(brain, synced)
    keep = brain.root.parent / "kept-edit.md"
    edited.rename(keep)                                   # move the copy out (keeps the edit)
    res = sync_pipeline(brain, "p", now=PAST + 86400)
    assert res.result == "ok" and edited.exists() and "my edit" not in edited.read_text()
    assert "my edit" in keep.read_text()
    assert len(State.load(brain).pipeline("p").files) == 3
    assert sync_pipeline(brain, "p", now=PAST + 86400 * 2).counts()["unchanged"] == 3


def test_an_ordinary_conflict_a_whose_provenance_claims_this_source_gets_the_step_too(brain, synced):
    sync_pipeline(brain, "p", now=PAST)
    st = State.load(brain)
    st.pipeline("p").files.pop(f"raw/p/docs/2026-09-01{S}notes.md")      # ownership lost for one file only
    st.save()
    res = sync_pipeline(brain, "p", now=PAST)
    c = next(c for c in res.conflicts if c["path"].endswith("notes.md"))
    assert c["reason"].endswith(NEXT_STEP)


def test_a_hand_written_file_conflict_text_is_unchanged(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "x\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    write(brain.root / "raw/p/docs/a.md", "mine\n")
    res = sync_pipeline(brain, "p")
    assert res.conflicts[0]["reason"] == "target exists and is not owned by this pipeline"


def test_ui_conflict_card_data_carries_the_step_and_keeps_open_and_copy(brain, synced):
    unclaimed_after_rebuild(brain, synced)
    sync_pipeline(brain, "p", now=PAST + 86400)
    with running_ui(brain) as ui:
        _, res = ui.get("/api/activity")
    card = next(c for c in res["needs_you"] if c["kind"] == "conflicts")
    assert card["items"][0]["next_step"] == NEXT_STEP
