"""MD-37: changing a pipeline's file-name policy produces a rename plan that is guarded and crash-safe."""
from datetime import datetime, timezone

import pytest

from conftest import tree_snapshot, write
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.naming import SEPARATOR as S
from mdpipeline.state import State
from uihelper import running_ui

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()
FILES = {"2026-10-01_phase1-prd.md": "prd", "20260930-seed-idea.md": "seed", "Notes.md": "notes",
         "sub/Meeting Notes.md": "meeting"}


class Kill(BaseException):
    pass


@pytest.fixture
def p1(brain, add_pipeline, make_source):
    """A P1 brain: pipeline on the 'original' policy, already synced."""
    src = make_source("docs", FILES)
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", now=NOW - 86400)
    return src


def switch(brain, policy="date-name", **kw):
    cfg = load_config(brain)
    cfg.get("p").name_policy = policy
    for k, v in kw.items():
        setattr(cfg.get("p"), k, v)
    save_config(brain, cfg)


def converge(brain, **kw):
    """Re-run as the owner would: a changed plan is shown first, then approved. Returns the last result."""
    for _ in range(3):
        res = sync_pipeline(brain, "p", trigger="cli", approve=True, **kw)
        if res.result != "blocked":
            return res
    raise AssertionError("did not converge")


def raw_files(brain):
    return sorted(str(p.relative_to(brain.root)) for p in (brain.root / "raw").rglob("*.md"))


NEW = [f"raw/p/docs/2026-10-01{S}phase1-prd.md", f"raw/p/docs/2026-09-30{S}seed-idea.md",
       f"raw/p/docs/2026-10-03{S}notes.md", f"raw/p/docs/sub/2026-10-03{S}meeting-notes.md"]


OLD = ["raw/p/docs/2026-10-01_phase1-prd.md", "raw/p/docs/20260930-seed-idea.md", "raw/p/docs/Notes.md",
       "raw/p/docs/sub/Meeting Notes.md"]
MOVES = [(o, n) for o, n in zip(OLD, NEW) if o != n]      # a file whose name is already the new name is updated in place


def test_dry_run_lists_every_rename_old_to_new_and_writes_nothing(brain, p1):
    switch(brain)
    before = tree_snapshot(brain.root / "raw")
    res = sync_pipeline(brain, "p", dry_run=True, now=NOW)
    renames = [i for i in res.plan.items() if i.action == "rename"]
    assert sorted((i.from_target, i.target) for i in renames) == sorted(MOVES)
    assert len(MOVES) >= 3        # (with "_" as separator the already-dated PRD file keeps its name and is updated in place)
    assert res.counts()["renamed"] == len(MOVES) and res.counts()["updated"] == 4 - len(MOVES)
    assert res.counts()["created"] == 0 and res.counts()["deleted"] == 0
    assert tree_snapshot(brain.root / "raw") == before


def test_dry_run_says_links_are_not_rewritten(brain, p1, capsys):
    switch(brain)
    main(["--brain", str(brain.root), "sync", "p", "--dry-run"])
    out = capsys.readouterr().out
    assert "rename" in out and "not rewritten" in out


def test_applying_the_plan_renames_content_stays_and_nothing_is_duplicated(brain, p1):
    old_bodies = {rel: parse_document((brain.root / "raw/p/docs" / rel).read_text())[1] for rel in FILES}
    switch(brain)
    res = sync_pipeline(brain, "p", now=NOW, trigger="cli")     # 4 files: below the guard's 5-file minimum
    assert res.result == "ok"
    assert raw_files(brain) == sorted(NEW)
    new_body = parse_document((brain.root / NEW[3]).read_text())[1]
    assert new_body == old_bodies["sub/Meeting Notes.md"]
    files = State.load(brain).pipeline("p").files
    assert sorted(files) == sorted(NEW)
    assert files[NEW[0]].name_policy == "date-name" and files[NEW[0]].first_seen


def test_new_provenance_reflects_the_new_policy(brain, p1):
    switch(brain)
    sync_pipeline(brain, "p", now=NOW)
    prov = parse_document((brain.root / NEW[0]).read_text())[0]["provenance"]
    assert prov["name_policy"] == "date-name" and prov["original_name"] == "2026-10-01_phase1-prd.md"
    assert prov["source_path"] == "2026-10-01_phase1-prd.md"


@pytest.fixture
def guarded(brain, add_pipeline, make_source):
    src = make_source("docs", {f"Note {i}.md": f"n{i}" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", now=NOW - 86400)
    switch(brain)
    return src


def test_above_the_threshold_nothing_is_written_until_that_exact_plan_is_approved(brain, guarded):
    before = tree_snapshot(brain.root / "raw")
    res = sync_pipeline(brain, "p", now=NOW, trigger="cli")
    assert res.result == "blocked" and tree_snapshot(brain.root / "raw") == before
    assert res.to_dict()["guard"]["count"] == 10
    done = sync_pipeline(brain, "p", now=NOW, trigger="cli", approve=True)
    assert done.result == "ok" and done.counts()["renamed"] == 10
    assert all(f"2026-10-03{S}note-{i}.md" in " ".join(raw_files(brain)) for i in range(10))


def test_the_mcp_cannot_approve_a_rename_plan(brain, guarded):
    sync_pipeline(brain, "p", now=NOW, trigger="cli")        # shown
    res = sync_pipeline(brain, "p", now=NOW, trigger="mcp", approve=True)
    assert res.result == "blocked"
    assert not any("2026-10-03" in f for f in raw_files(brain))


def test_approval_applies_only_to_the_plan_that_was_shown(brain, guarded):
    sync_pipeline(brain, "p", now=NOW, trigger="cli")
    write(guarded / "Note 0.md", "changed after the plan was shown")     # the change set changes
    res = sync_pipeline(brain, "p", now=NOW, trigger="cli", approve=True)
    assert res.result == "blocked"
    assert not any("2026-10-03" in f for f in raw_files(brain))


def test_a_crash_after_the_new_file_is_written_converges_without_duplicates(brain, guarded):
    sync_pipeline(brain, "p", now=NOW, trigger="cli")
    seen = []

    def die(item):
        seen.append(item.target)
        if len(seen) == 3:
            raise Kill()

    with pytest.raises(Kill):
        sync_pipeline(brain, "p", now=NOW, trigger="cli", approve=True, after_write=die)
    res = converge(brain, now=NOW)
    assert res.result == "ok"
    names = raw_files(brain)
    assert len(names) == 10 and all("2026-10-03" in n for n in names)
    assert sorted(State.load(brain).pipeline("p").files) == names
    again = sync_pipeline(brain, "p", now=NOW + 1)
    assert again.counts()["unchanged"] == 10


def test_a_crash_leaves_no_duplicate_even_when_sources_are_kept_on_delete(brain, add_pipeline, make_source):
    src = make_source("docs", {f"Note {i}.md": f"n{i}" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)], on_source_delete="keep"))
    sync_pipeline(brain, "p", now=NOW - 86400)
    switch(brain)
    sync_pipeline(brain, "p", now=NOW, trigger="cli")

    def die(item):
        raise Kill()

    with pytest.raises(Kill):
        sync_pipeline(brain, "p", now=NOW, trigger="cli", approve=True, after_write=die)
    converge(brain, now=NOW)
    assert len(raw_files(brain)) == 10 and all("2026-10-03" in n for n in raw_files(brain))


def test_hand_written_and_unowned_files_are_never_touched(brain, p1):
    mine = write(brain.root / "raw/p/docs/my-own-note.md", "hand written\n")
    other = write(brain.root / "raw/elsewhere/x.md", "x\n")
    switch(brain)
    sync_pipeline(brain, "p", now=NOW)
    assert mine.read_text() == "hand written\n" and other.read_text() == "x\n"
    assert sorted(State.load(brain).pipeline("p").files) == sorted(NEW)


def test_a_hand_written_file_at_the_new_name_is_never_overwritten_and_the_old_file_stays(brain, p1):
    mine = write(brain.root / f"raw/p/docs/2026-10-03{S}notes.md", "hand written\n")
    switch(brain)
    res = sync_pipeline(brain, "p", now=NOW)
    assert mine.read_text() == "hand written\n" and (brain.root / "raw/p/docs/Notes.md").exists()
    assert res.counts()["conflicts"] >= 1


def test_an_owned_file_edited_in_the_brain_is_not_renamed_without_force(brain, p1):
    edited = brain.root / "raw/p/docs/Notes.md"
    edited.write_text(edited.read_text() + "my edit\n")
    switch(brain)
    res = sync_pipeline(brain, "p", now=NOW)
    assert edited.exists() and "my edit" in edited.read_text()
    assert any(c["type"] == "b" for c in res.conflicts)


def test_switching_between_two_policies_keeps_the_first_seen_date(brain, add_pipeline, make_source):
    src = make_source("docs", {"Notes.md": "n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    switch(brain, "date-source-name")
    sync_pipeline(brain, "p", now=NOW + 86400 * 9)
    assert raw_files(brain) == [f"raw/p/docs/2026-10-04{S}docs{S}notes.md"]
    assert next(iter(State.load(brain).pipeline("p").files.values())).first_seen == "2026-10-04"


def test_a_policy_change_that_yields_the_same_name_updates_in_place(brain, add_pipeline, make_source):
    src = make_source("docs", {"notes.md": "n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="original"))
    sync_pipeline(brain, "p", now=NOW)
    switch(brain, "name")
    res = sync_pipeline(brain, "p", now=NOW)
    assert res.counts()["renamed"] == 0 and res.counts()["updated"] == 1
    assert raw_files(brain) == ["raw/p/docs/notes.md"]
    assert parse_document((brain.root / "raw/p/docs/notes.md").read_text())[0]["provenance"]["name_policy"] == "name"


def test_cli_edit_changes_the_policy_without_renaming_anything(brain, p1, capsys):
    before = tree_snapshot(brain.root / "raw")
    code = main(["--brain", str(brain.root), "pipeline", "edit", "p", "--name-policy", "date-name"])
    assert code == 0 and tree_snapshot(brain.root / "raw") == before
    assert load_config(brain).get("p").name_policy == "date-name"
    assert "dry-run" in capsys.readouterr().out.lower()


def test_ui_preview_shows_renames_for_a_policy_change(brain, p1):
    d = {"name": "p", "sources": [{"path": str(p1), "label": "docs"}], "target": "raw/{pipeline}/{source}/{relpath}",
         "include": ["**/*.md"], "exclude": [], "on_source_delete": "delete", "guard_threshold_percent": 40,
         "guard_min_files": 5, "name_policy": "date-name"}
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": d, "editing": "p"})
    pv = res["preview"]
    assert pv["counts"]["rename"] == len(MOVES) and pv["counts"]["create"] == 0 and pv["counts"]["delete"] == 0
    items = [i for g in pv["groups"] for i in g["items"] if i["op"] == "rename"]
    assert any(i["note"].startswith("renamed from raw/p/docs/Notes.md") for i in items)
    assert "not rewritten" in pv["rename_note"]


def test_ui_needs_you_guard_card_lists_the_renames(brain, guarded):
    sync_pipeline(brain, "p", now=NOW, trigger="mcp")
    with running_ui(brain) as ui:
        _, res = ui.get("/api/activity")
    card = next(c for c in res["needs_you"] if c["kind"] == "guard")
    assert len(card["will_rename"]) == 10
    assert card["will_rename"][0]["from"].startswith("raw/p/docs/Note ") and "-" in card["will_rename"][0]["to"]
