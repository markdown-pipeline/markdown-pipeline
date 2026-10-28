"""MD-35 AC4 (Should): a pure rename (same content, new path, one sync, one pipeline) keeps the first-seen date."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import write
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.naming import SEPARATOR as S
from mdpipeline.state import State

DAY1 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def at(days):
    return (DAY1 + timedelta(days=days)).timestamp()


def names(brain, folder="raw/p/docs"):
    return sorted(p.name for p in (brain.root / folder).glob("*.md"))


@pytest.fixture
def one(brain, add_pipeline, make_source):
    src = make_source("docs", {"Old Title.md": "same body\n", "other.md": "other\n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=at(0))
    return src


def test_dry_run_shows_a_pure_rename_as_a_rename_not_delete_plus_create(brain, one):
    (one / "Old Title.md").rename(one / "New Title.md")
    res = sync_pipeline(brain, "p", dry_run=True, now=at(9))
    c = res.counts()
    assert (c["renamed"], c["created"], c["deleted"]) == (1, 0, 0)
    item = next(i for i in res.plan.items() if i.action == "rename")
    assert item.from_target == f"raw/p/docs/2026-10-04{S}old-title.md"
    assert item.target == f"raw/p/docs/2026-10-04{S}new-title.md"      # the original first-seen date, not today's


def test_applying_keeps_first_seen_content_and_leaves_no_old_file(brain, one):
    (one / "Old Title.md").rename(one / "New Title.md")
    res = sync_pipeline(brain, "p", now=at(9))
    assert res.result == "ok"
    assert names(brain) == [f"2026-10-04{S}new-title.md", f"2026-10-04{S}other.md"]
    rec = State.load(brain).pipeline("p").files[f"raw/p/docs/2026-10-04{S}new-title.md"]
    assert rec.first_seen == "2026-10-04" and rec.source_path == "New Title.md"
    meta, body = parse_document((brain.root / f"raw/p/docs/2026-10-04{S}new-title.md").read_text())
    assert body == "same body\n" and meta["provenance"]["original_name"] == "New Title.md"
    assert meta["provenance"]["first_seen"] == "2026-10-04"


def test_a_move_to_another_folder_is_also_a_pure_rename(brain, one):
    (one / "sub").mkdir()
    (one / "Old Title.md").rename(one / "sub" / "Old Title.md")
    res = sync_pipeline(brain, "p", now=at(9))
    assert res.counts()["renamed"] == 1
    assert (brain.root / f"raw/p/docs/sub/2026-10-04{S}old-title.md").exists()


def test_renamed_and_edited_is_delete_plus_create_with_a_new_date(brain, one):
    (one / "Old Title.md").unlink()
    write(one / "New Title.md", "different body\n")
    res = sync_pipeline(brain, "p", now=at(9))
    assert (res.counts()["renamed"], res.counts()["created"], res.counts()["deleted"]) == (0, 1, 1)
    assert f"2026-10-13{S}new-title.md" in names(brain)


def test_ambiguous_identical_files_are_never_paired(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "same\n", "b.md": "same\n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=at(0))
    (src / "a.md").rename(src / "c.md")
    (src / "b.md").rename(src / "d.md")
    res = sync_pipeline(brain, "p", dry_run=True, now=at(5))
    assert res.counts()["renamed"] == 0 and res.counts()["created"] == 2 and res.counts()["deleted"] == 2


def test_original_policy_keeps_the_phase_1_delete_plus_create(brain, add_pipeline, make_source):
    src = make_source("docs", {"Old.md": "body\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", now=at(0))
    (src / "Old.md").rename(src / "New.md")
    res = sync_pipeline(brain, "p", dry_run=True, now=at(5))
    assert (res.counts()["renamed"], res.counts()["created"], res.counts()["deleted"]) == (0, 1, 1)


def test_an_edited_brain_copy_is_not_renamed_silently(brain, one):
    old = brain.root / f"raw/p/docs/2026-10-04{S}old-title.md"
    old.write_text(old.read_text() + "my edit\n")
    (one / "Old Title.md").rename(one / "New Title.md")
    res = sync_pipeline(brain, "p", now=at(9))
    assert old.exists() and "my edit" in old.read_text()
    assert any(c["type"] == "b" for c in res.conflicts)
    assert not (brain.root / f"raw/p/docs/2026-10-04{S}new-title.md").exists()
