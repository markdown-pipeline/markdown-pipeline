"""MD-10: removing a pipeline, or one folder from it, never loses brain files unless explicitly asked."""
import pytest

from conftest import tree_snapshot
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.locking import BusyError, brain_lock
from mdpipeline.log import ActivityLog
from mdpipeline.removal import ConfirmationRequired, remove_pipeline, remove_source
from mdpipeline.service import NotFound, ValidationFailed
from mdpipeline.state import State


@pytest.fixture
def two_folders(brain, add_pipeline, make_source):
    a = make_source("a", {f"a{i}.md": f"A{i}" for i in range(3)})
    b = make_source("b", {f"b{i}.md": f"B{i}" for i in range(2)})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    return a, b


def md_files(brain):
    return sorted(str(p.relative_to(brain.root)) for p in (brain.root / "raw").rglob("*.md"))


# pipeline removal -----------------------------------------------------------------------
def test_removing_a_pipeline_keeps_every_file_it_owned_and_reports_them_unowned(brain, two_folders):
    before = md_files(brain)
    res = remove_pipeline(brain, "p", trigger="cli")
    assert md_files(brain) == before and res.unowned == 5 and res.deleted == 0
    assert load_config(brain).get("p") is None
    assert "p" not in State.load(brain).pipelines
    ev = [e for e in ActivityLog(brain).read(types={"event"}) if e["action"] == "remove"][0]
    assert ev["pipeline"] == "p" and ev["unowned"] == 5 and "raw/p/A/a0.md" in ev["paths"]


def test_unowned_files_are_never_modified_or_deleted_by_any_pipeline_afterwards(brain, two_folders, make_source, add_pipeline):
    a, b = two_folders
    remove_pipeline(brain, "p", trigger="cli")
    snap = {f: (brain.root / f).read_bytes() for f in md_files(brain)}
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))   # same name re-created
    (a / "a0.md").write_text("changed")
    res = sync_pipeline(brain, "p", force=True)
    assert res.counts()["conflicts"] == 5 and res.counts()["created"] == 0
    for f, data in snap.items():
        assert (brain.root / f).read_bytes() == data
    (a / "a1.md").unlink()
    sync_pipeline(brain, "p", force=True)
    assert (brain.root / "raw/p/A/a1.md").exists()


def test_purge_requires_confirmation_with_the_count(brain, two_folders):
    with pytest.raises(ConfirmationRequired) as exc:
        remove_pipeline(brain, "p", trigger="cli", purge=True)
    assert exc.value.count == 5 and "5" in str(exc.value)
    assert len(md_files(brain)) == 5 and load_config(brain).get("p") is not None
    with pytest.raises(ConfirmationRequired):
        remove_pipeline(brain, "p", trigger="cli", purge=True, confirm_count=4)
    assert len(md_files(brain)) == 5


def test_purge_deletes_only_owned_unedited_files_and_leaves_the_rest(brain, two_folders):
    (brain.root / "raw/p/A/hand.md").write_text("mine")
    (brain.root / "raw/p/A/a0.md").write_text("edited by me")
    res = remove_pipeline(brain, "p", trigger="cli", purge=True, confirm_count=5)
    assert res.deleted == 4 and res.left_edited == ["raw/p/A/a0.md"]
    assert md_files(brain) == ["raw/p/A/a0.md", "raw/p/A/hand.md"]
    assert (brain.root / "raw/p/A/a0.md").read_text() == "edited by me"
    assert load_config(brain).get("p") is None


def test_remove_unknown_pipeline(brain):
    with pytest.raises(NotFound):
        remove_pipeline(brain, "nope", trigger="cli")


def test_remove_fails_with_busy_while_a_sync_runs_and_changes_nothing(brain, two_folders):
    with brain_lock(brain):
        with pytest.raises(BusyError):
            remove_pipeline(brain, "p", trigger="cli")
    assert load_config(brain).get("p") is not None


def test_removing_a_pipeline_never_touches_sources(brain, two_folders):
    a, b = two_folders
    snap = tree_snapshot(a, b)
    remove_pipeline(brain, "p", trigger="cli", purge=True, confirm_count=5)
    assert tree_snapshot(a, b) == snap


# folder removal -------------------------------------------------------------------------
def test_removing_one_folder_keeps_its_files_unowned_by_default(brain, two_folders):
    before = md_files(brain)
    res = remove_source(brain, "p", "A", trigger="cli")
    assert md_files(brain) == before and res.result == "ok" and res.unowned == 3
    assert [s.label for s in load_config(brain).get("p").sources] == ["B"]
    files = State.load(brain).pipeline("p").files
    assert sorted(files) == ["raw/p/B/b0.md", "raw/p/B/b1.md"]


def test_files_of_a_removed_folder_are_never_touched_by_later_syncs(brain, two_folders):
    a, b = two_folders
    remove_source(brain, "p", "A", trigger="cli")
    snap = (brain.root / "raw/p/A/a0.md").read_bytes()
    (b / "b0.md").write_text("b changed")
    sync_pipeline(brain, "p")
    assert (brain.root / "raw/p/A/a0.md").read_bytes() == snap
    assert sync_pipeline(brain, "p").counts()["unchanged"] == 2


def test_removing_a_folder_with_delete_files_deletes_its_files_through_the_guard(brain, two_folders):
    res = remove_source(brain, "p", "B", trigger="cli", delete_files=True)    # 2 files: below the minimum
    assert res.result == "ok" and res.deleted == 2
    assert md_files(brain) == ["raw/p/A/a0.md", "raw/p/A/a1.md", "raw/p/A/a2.md"]


def test_deleting_a_folders_files_that_trips_the_guard_is_blocked_until_approved(brain, add_pipeline, make_source):
    a = make_source("a", {f"a{i}.md": "x" for i in range(8)})
    b = make_source("b", {"b0.md": "x"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    snap = tree_snapshot(brain.raw_dir)
    res = remove_source(brain, "p", "A", trigger="cli", delete_files=True)
    assert res.result == "blocked" and res.guard.count == 8 and res.guard.total == 9
    assert tree_snapshot(brain.raw_dir) == snap
    assert [s.label for s in load_config(brain).get("p").sources] == ["A", "B"]        # config unchanged
    mcp = remove_source(brain, "p", "A", trigger="mcp", delete_files=True, approve=True)
    assert mcp.result == "blocked"
    res = remove_source(brain, "p", "A", trigger="cli", delete_files=True, approve=True)
    assert res.result == "ok" and res.deleted == 8
    assert md_files(brain) == ["raw/p/B/b0.md"]
    assert [s.label for s in load_config(brain).get("p").sources] == ["B"]


def test_folder_deletion_leaves_brain_edited_files(brain, two_folders):
    (brain.root / "raw/p/B/b0.md").write_text("my edit")
    res = remove_source(brain, "p", "B", trigger="cli", delete_files=True)
    assert res.deleted == 1 and res.left_edited == ["raw/p/B/b0.md"]
    assert (brain.root / "raw/p/B/b0.md").read_text() == "my edit"


def test_cannot_remove_the_only_folder_use_pipeline_remove(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"a.md": "x"}))]))
    with pytest.raises(ValidationFailed, match="at least one"):
        remove_source(brain, "p", "a", trigger="cli")


def test_unknown_folder_label(brain, two_folders):
    with pytest.raises(NotFound):
        remove_source(brain, "p", "Z", trigger="cli")


def test_folder_removal_is_logged(brain, two_folders):
    remove_source(brain, "p", "A", trigger="ui")
    ev = [e for e in ActivityLog(brain).read(types={"event"}) if e["action"] == "remove_source"][0]
    assert ev["trigger"] == "ui" and ev["source_label"] == "A" and ev["unowned"] == 3


# CLI --------------------------------------------------------------------------------------
def run(capsys, brain, *argv, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("builtins.input", lambda *_: stdin)
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_remove_keeps_files_and_says_so(brain, two_folders, capsys):
    code, out, _ = run(capsys, brain, "pipeline", "remove", "p")
    assert code == 0 and "5 files kept" in out and "unowned" in out and len(md_files(brain)) == 5


def test_cli_purge_asks_to_confirm_with_the_count_and_aborts_on_no(brain, two_folders, capsys, monkeypatch):
    code, out, err = run(capsys, brain, "pipeline", "remove", "p", "--purge", stdin="no", monkeypatch=monkeypatch)
    assert code == 1 and "5 files" in out + err and len(md_files(brain)) == 5
    assert load_config(brain).get("p") is not None


def test_cli_purge_with_typed_confirmation_or_yes_flag(brain, two_folders, capsys, monkeypatch):
    code, out, _ = run(capsys, brain, "pipeline", "remove", "p", "--purge", stdin="p", monkeypatch=monkeypatch)
    assert code == 0 and md_files(brain) == []


def test_cli_purge_yes_flag(brain, two_folders, capsys):
    code, out, _ = run(capsys, brain, "pipeline", "remove", "p", "--purge", "--yes")
    assert code == 0 and md_files(brain) == []


def test_cli_remove_source_default_keeps_and_delete_files_option(brain, two_folders, capsys):
    code, out, _ = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "A")
    assert code == 0 and "3 files kept" in out and len(md_files(brain)) == 5
    code, out, _ = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "B", "--delete-files")
    assert code == 2 and "at least one" in (out + _)


def test_cli_remove_source_blocked_exit_3_then_approve(brain, add_pipeline, make_source, capsys):
    a = make_source("a", {f"a{i}.md": "x" for i in range(8)})
    b = make_source("b", {"b0.md": "x"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    code, out, err = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "A", "--delete-files")
    assert code == 3 and "--approve" in out + err
    code, out, err = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "A", "--delete-files", "--approve")
    assert code == 0 and len(md_files(brain)) == 1


# MD-139: the CLI says which brain it acted on and never prints a bare identifier ---------------------------------------------
def test_remove_of_a_missing_pipeline_fails_before_any_prompt_and_names_the_brain_and_the_pipelines_there(brain, two_folders, capsys, monkeypatch):
    def no_prompt(*_):
        raise AssertionError("asked for confirmation before checking the pipeline exists")

    monkeypatch.setattr("builtins.input", no_prompt)
    code, out, err = run(capsys, brain, "pipeline", "remove", "vm-test", "--purge")
    assert code != 0 and f"No pipeline named 'vm-test' in the output folder at {brain.root}." in err
    assert "p" in err and "--brain" in err and "--purge will delete" not in out + err
    assert load_config(brain).get("p") is not None


def test_remove_of_a_missing_pipeline_with_yes_is_just_as_clear_and_logs_no_removal(brain, two_folders, capsys):
    code, out, err = run(capsys, brain, "pipeline", "remove", "vm-test", "--purge", "--yes")
    assert code != 0 and "No pipeline named 'vm-test'" in err and str(brain.root) in err
    assert "remove" not in [e.get("action") for e in ActivityLog(brain).read()]


def test_remove_of_a_missing_pipeline_in_an_empty_brain_says_there_are_none(brain, capsys):
    code, out, err = run(capsys, brain, "pipeline", "remove", "vm-test")
    assert code != 0 and "no pipelines" in err.lower() and str(brain.root) in err


def test_a_successful_remove_names_the_brain(brain, two_folders, capsys):
    code, out, _ = run(capsys, brain, "pipeline", "remove", "p")
    assert code == 0 and f"Pipeline 'p' removed from {brain.root}" in out


def test_the_purge_prompt_names_the_brain_and_the_count(brain, two_folders, capsys, monkeypatch):
    code, out, err = run(capsys, brain, "pipeline", "remove", "p", "--purge", stdin="no", monkeypatch=monkeypatch)
    assert str(brain.root) in out and "5 files" in out


@pytest.mark.parametrize("argv", [
    ("pipeline", "remove", "ghost"), ("pipeline", "pause", "ghost"), ("pipeline", "resume", "ghost"), ("pipeline", "show", "ghost"),
    ("pipeline", "edit", "ghost", "--remove-source", "A"), ("sync", "ghost"), ("schedule", "set", "ghost", "off"),
])
def test_no_cli_error_is_only_an_identifier(brain, two_folders, capsys, argv):
    """AC2: a missing pipeline always gets a full sentence naming the pipeline and the brain."""
    code, out, err = run(capsys, brain, *argv)
    lines = [l for l in err.splitlines() if l.strip()]
    assert code != 0 and lines
    assert "ghost" in err and str(brain.root) in err and err.strip() != "Error: ghost"


def test_a_notfound_reaching_the_top_level_handler_is_a_full_sentence(brain, capsys, monkeypatch):
    monkeypatch.setattr("mdpipeline.cli.cmd_status", lambda args: (_ for _ in ()).throw(NotFound("ghost")))
    code, _, err = run(capsys, brain, "status")
    assert code == 2 and "No pipeline named 'ghost'" in err and str(brain.root) in err and err.strip() != "Error: ghost"


def test_a_pipeline_removed_from_the_cli_is_gone_from_a_running_ui_without_a_restart(brain, two_folders, capsys):
    """MD-139 AC5."""
    from uihelper import running_ui

    with running_ui(brain) as ui:
        _, before = ui.get("/api/state")
        assert [r["name"] for r in before["pipelines"]] == ["p"]
        code, _, err = run(capsys, brain, "pipeline", "remove", "p")
        assert code == 0, err
        _, after = ui.get("/api/state")
    assert after["pipelines"] == []
