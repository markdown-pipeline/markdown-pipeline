"""MD-35 / MD-34 AC3: the assigned name and the first-seen date are computed once and stored in state."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import write
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.naming import SEPARATOR as S
from mdpipeline.state import State

DAY1 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def at(days: int) -> float:
    return (DAY1 + timedelta(days=days)).timestamp()


def raw_names(brain, folder="raw/p/docs"):
    return sorted(p.name for p in (brain.root / folder).glob("*.md"))


@pytest.fixture
def setup(brain, add_pipeline, make_source):
    src = make_source("docs", {"README.md": "v0\n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    return src


def test_first_seen_and_name_are_recorded_in_state(brain, setup):
    sync_pipeline(brain, "p", now=at(0))
    rec = State.load(brain).pipeline("p").files[f"raw/p/docs/2026-10-04{S}readme.md"]
    assert (rec.source_label, rec.source_path) == ("docs", "README.md")
    assert rec.first_seen == "2026-10-04" and rec.name_policy == "date-name" and rec.suffix is None


def test_editing_the_source_updates_in_place_and_keeps_name_and_date(brain, setup):
    sync_pipeline(brain, "p", now=at(0))
    write(setup / "README.md", "v1\n")
    res = sync_pipeline(brain, "p", now=at(5))
    assert res.counts()["updated"] == 1 and res.counts()["created"] == 0
    assert raw_names(brain) == [f"2026-10-04{S}readme.md"]
    assert State.load(brain).pipeline("p").files[f"raw/p/docs/2026-10-04{S}readme.md"].first_seen == "2026-10-04"


def test_ten_edits_keep_name_and_first_seen(brain, setup):
    sync_pipeline(brain, "p", now=at(0))
    for n in range(1, 11):
        write(setup / "README.md", f"edit {n}\n")
        assert sync_pipeline(brain, "p", now=at(n)).result == "ok"
    assert raw_names(brain) == [f"2026-10-04{S}readme.md"]
    files = State.load(brain).pipeline("p").files
    assert len(files) == 1 and next(iter(files.values())).first_seen == "2026-10-04"
    assert "edit 10" in (brain.root / f"raw/p/docs/2026-10-04{S}readme.md").read_text()


def test_resync_on_a_later_day_without_changes_is_unchanged(brain, setup):
    sync_pipeline(brain, "p", now=at(0))
    res = sync_pipeline(brain, "p", now=at(30))
    assert res.counts()["unchanged"] == 1 and res.counts()["created"] == 0 and res.counts()["deleted"] == 0


def test_source_rename_is_delete_plus_create_with_a_new_name(brain, setup):
    sync_pipeline(brain, "p", now=at(0))
    (setup / "README.md").unlink()
    write(setup / "Guide.md", "a different file\n")     # (a pure rename of identical content is MD-35 AC4: test_pure_rename)
    res = sync_pipeline(brain, "p", now=at(3))
    assert (res.counts()["created"], res.counts()["deleted"]) == (1, 1)
    assert raw_names(brain) == [f"2026-10-07{S}guide.md"]


def test_a_file_deleted_in_the_brain_is_recreated_under_the_same_name(brain, setup):
    sync_pipeline(brain, "p", now=at(0))
    (brain.root / f"raw/p/docs/2026-10-04{S}readme.md").unlink()
    sync_pipeline(brain, "p", now=at(9))
    assert raw_names(brain) == [f"2026-10-04{S}readme.md"]


def test_date_in_the_source_name_is_used_for_the_date(brain, add_pipeline, make_source):
    src = make_source("docs", {"20260930-seed-idea.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=at(0))
    assert raw_names(brain) == [f"2026-09-30{S}seed-idea.md"]
    rec = next(iter(State.load(brain).pipeline("p").files.values()))
    assert rec.first_seen == "2026-10-04"      # first_seen is when it was copied; the name date wins for {date}


def test_original_policy_records_no_naming_fields(brain, add_pipeline, make_source):
    src = make_source("docs", {"A b.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", now=at(0))
    rec = State.load(brain).pipeline("p").files["raw/p/docs/A b.md"]
    assert rec.first_seen is None and rec.name_policy is None
