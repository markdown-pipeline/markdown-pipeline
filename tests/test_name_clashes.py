"""MD-36: name clashes get a deterministic 6-hex suffix, only when needed; names never swap."""
import hashlib
from datetime import datetime, timezone

import pytest

from conftest import tree_snapshot, write
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.naming import CLASH_SEPARATOR as C, SEPARATOR as S, clash_suffix
from mdpipeline.state import State

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()
CLEAN = f"2026-10-04{S}notes.md"


def hexsuffix(label, rel, n=6):
    return hashlib.sha256(f"{label}:{rel}".encode()).hexdigest()[:n]


def suffixed(label, rel):
    return f"2026-10-04{S}notes{C}{hexsuffix(label, rel)}.md"


def listing(brain, folder="raw/p"):
    return sorted(p.name for p in (brain.root / folder).glob("*.md"))


def two_sources(make_source, add_pipeline, order=("a", "b"), **kw):
    srcs = {"a": make_source("a", {"Notes.md": "from a"}), "b": make_source("b", {"notes.md": "from b"})}
    add_pipeline(Pipeline("p", [Source(str(srcs[k]), k) for k in order], target="raw/p/{relpath}",
                          name_policy=kw.pop("name_policy", "date-name"), **kw))
    return srcs


def test_clash_suffix_is_6_hex_of_label_and_path():
    assert clash_suffix("b", "notes.md") == hexsuffix("b", "notes.md")
    assert len(clash_suffix("b", "notes.md")) == 6


def test_case_only_clash_first_identity_keeps_the_clean_name_whatever_the_config_order(brain, add_pipeline, make_source):
    two_sources(make_source, add_pipeline, order=("b", "a"))
    res = sync_pipeline(brain, "p", now=NOW)
    assert res.result == "ok"
    assert listing(brain) == sorted([CLEAN, suffixed("b", "notes.md")])
    assert (brain.root / "raw/p" / CLEAN).read_text().endswith("from a")


def test_normalization_only_clash_is_detected(brain, add_pipeline, make_source):
    a = make_source("a", {"café.md": "composed"})
    b = make_source("b", {"café.md": "decomposed"})
    add_pipeline(Pipeline("p", [Source(str(a), "a"), Source(str(b), "b")], target="raw/p/{relpath}", name_policy="custom", name_pattern="{name}"))
    sync_pipeline(brain, "p", now=NOW)
    names = listing(brain)
    assert len(names) == 2 and len({n.casefold() for n in names}) == 2


def test_dry_run_shows_the_suffix_before_anything_is_written(brain, add_pipeline, make_source):
    two_sources(make_source, add_pipeline)
    before = tree_snapshot(brain.raw_dir)
    res = sync_pipeline(brain, "p", dry_run=True, now=NOW)
    targets = sorted(i.target for i in res.plan.items())
    assert targets == sorted([f"raw/p/{CLEAN}", f"raw/p/{suffixed('b', 'notes.md')}"])
    assert tree_snapshot(brain.raw_dir) == before


def test_no_suffix_when_there_is_no_clash(brain, add_pipeline, make_source):
    src = make_source("a", {"Notes.md": "x", "Other.md": "y"})
    add_pipeline(Pipeline("p", [Source(str(src), "a")], target="raw/p/{relpath}", name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    assert listing(brain) == [CLEAN, f"2026-10-04{S}other.md"]


def test_the_existing_owner_keeps_the_name_and_the_newcomer_is_suffixed(brain, add_pipeline, make_source):
    a = make_source("a", {"Notes.md": "from a"})
    add_pipeline(Pipeline("p", [Source(str(a), "a")], target="raw/p/{relpath}", name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    # a newcomer whose identity sorts BEFORE the owner still gets the suffix
    early = make_source("0", {"notes.md": "early"})
    from mdpipeline.config import load_config, save_config
    cfg = load_config(brain)
    cfg.get("p").sources.insert(0, Source(str(early), "0"))
    save_config(brain, cfg)
    sync_pipeline(brain, "p", now=NOW + 3600)     # same day: same {date}, so the names clash
    assert (brain.root / "raw/p" / CLEAN).read_text().endswith("from a")
    assert f"2026-10-04{S}notes{C}{hexsuffix('0', 'notes.md')}.md" in listing(brain)


def test_suffix_is_stored_in_state_and_never_changes_on_edits_or_resyncs(brain, add_pipeline, make_source):
    srcs = two_sources(make_source, add_pipeline)
    sync_pipeline(brain, "p", now=NOW)
    for n in range(3):
        write(srcs["b"] / "notes.md", f"edit {n}")
        assert sync_pipeline(brain, "p", now=NOW + 86400 * (n + 1)).result == "ok"
    assert listing(brain) == sorted([CLEAN, suffixed("b", "notes.md")])
    rec = State.load(brain).pipeline("p").files[f"raw/p/{suffixed('b', 'notes.md')}"]
    assert rec.suffix == hexsuffix("b", "notes.md")


def test_suffix_stays_when_the_other_file_is_deleted_and_names_never_swap(brain, add_pipeline, make_source):
    srcs = two_sources(make_source, add_pipeline)
    sync_pipeline(brain, "p", now=NOW)
    (srcs["a"] / "Notes.md").unlink()
    sync_pipeline(brain, "p", now=NOW + 86400)
    assert listing(brain) == [suffixed("b", "notes.md")]
    sync_pipeline(brain, "p", now=NOW + 86400 * 2)
    assert listing(brain) == [suffixed("b", "notes.md")]
    assert (brain.root / "raw/p" / suffixed("b", "notes.md")).read_text().endswith("from b")


def test_three_way_clash_gives_two_distinct_suffixes(brain, add_pipeline, make_source):
    srcs = [make_source(n, {"Notes.md": n}) for n in ("a", "b", "c")]
    add_pipeline(Pipeline("p", [Source(str(s), n) for s, n in zip(srcs, "abc")], target="raw/p/{relpath}", name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    names = listing(brain)
    assert len(names) == 3 and CLEAN in names


def test_clash_across_pipelines_of_the_brain(brain, add_pipeline, make_source):
    a = make_source("a", {"Notes.md": "from p"})
    b = make_source("b", {"notes.md": "from q"})
    add_pipeline(Pipeline("p", [Source(str(a), "a")], target="raw/shared/{relpath}", name_policy="date-name"))
    add_pipeline(Pipeline("q", [Source(str(b), "b")], target="raw/shared/{relpath}", name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    res = sync_pipeline(brain, "q", now=NOW)
    assert res.result == "ok"
    assert listing(brain, "raw/shared") == sorted([CLEAN, suffixed("b", "notes.md")])
    assert (brain.root / "raw/shared" / CLEAN).read_text().endswith("from p")


def test_a_hand_written_file_at_the_clean_name_is_never_overwritten(brain, add_pipeline, make_source):
    src = make_source("a", {"Notes.md": "from a"})
    add_pipeline(Pipeline("p", [Source(str(src), "a")], target="raw/p/{relpath}", name_policy="date-name"))
    mine = write(brain.root / "raw/p" / CLEAN, "my own note\n")
    res = sync_pipeline(brain, "p", now=NOW)
    assert res.result == "conflicts" and mine.read_text() == "my own note\n"
    assert listing(brain) == [CLEAN]


def test_original_policy_collision_behaviour_is_unchanged(brain, add_pipeline, make_source):
    two_sources(make_source, add_pipeline, name_policy="original")
    res = sync_pipeline(brain, "p", now=NOW)
    assert res.result == "error" and any("collision" in e["error"] for e in res.errors)
