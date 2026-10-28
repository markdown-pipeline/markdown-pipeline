"""MD-38: provenance records the original name, the first-seen date, the policy and any clash suffix."""
from datetime import datetime, timezone

from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import build_provenance, parse_document
from mdpipeline.naming import SEPARATOR as S

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()


def prov_of(brain, rel):
    meta, _ = parse_document((brain.root / rel).read_text(encoding="utf-8"))
    return meta, meta["provenance"]


def test_build_provenance_adds_name_fields_after_source_path():
    p = build_provenance(pipeline="p", source_label="docs", source_path="a/B.md", source_root="/s", source_mtime=1.0,
                         synced_at=2.0, source_bytes=b"x", names={"original_name": "B.md", "first_seen": "2026-10-04",
                                                                    "name_policy": "date-name", "name_suffix": "3f9a1c"})
    keys = list(p)
    assert keys[keys.index("source_path") + 1:keys.index("source_path") + 5] == ["original_name", "first_seen", "name_policy", "name_suffix"]
    assert p["original_name"] == "B.md" and p["name_suffix"] == "3f9a1c"


def test_build_provenance_without_names_is_exactly_p1():
    p = build_provenance(pipeline="p", source_label="docs", source_path="a.md", source_root="/s", source_mtime=1.0,
                         synced_at=2.0, source_bytes=b"x")
    assert not {"original_name", "first_seen", "name_policy", "name_suffix"} & set(p)


def test_synced_file_records_name_fields(brain, add_pipeline, make_source):
    src = make_source("docs", {"sub/Meeting Notes.md": "body\n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    _, prov = prov_of(brain, f"raw/p/docs/sub/2026-10-04{S}meeting-notes.md")
    assert prov["source_path"] == "sub/Meeting Notes.md" and prov["original_name"] == "Meeting Notes.md"
    assert prov["first_seen"] == "2026-10-04" and prov["name_policy"] == "date-name" and "name_suffix" not in prov


def test_custom_policy_records_the_pattern(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="custom", name_pattern="{slug}.{date}"))
    sync_pipeline(brain, "p", now=NOW)
    _, prov = prov_of(brain, "raw/p/docs/a.2026-10-04.md")
    assert prov["name_policy"] == "{slug}.{date}"


def test_hebrew_original_name_is_kept_readable(brain, add_pipeline, make_source):
    src = make_source("docs", {"סיכום פגישה.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    text = (brain.root / f"raw/p/docs/2026-10-04{S}סיכום-פגישה.md").read_text(encoding="utf-8")
    assert "original_name: סיכום פגישה.md" in text


def test_original_policy_provenance_is_unchanged(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", now=NOW)
    _, prov = prov_of(brain, "raw/p/docs/a.md")
    assert not {"original_name", "first_seen", "name_policy", "name_suffix"} & set(prov)


def test_authors_own_front_matter_keys_are_preserved(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "---\ntitle: Mine\nfirst_seen: long ago\nname_policy: mine\n---\nbody\n"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    meta, prov = prov_of(brain, f"raw/p/docs/2026-10-04{S}a.md")
    assert meta["title"] == "Mine" and meta["first_seen"] == "long ago" and meta["name_policy"] == "mine"
    assert prov["first_seen"] == "2026-10-04"


def test_first_seen_in_provenance_survives_later_updates(brain, add_pipeline, make_source):
    from conftest import write
    src = make_source("docs", {"a.md": "v0"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", now=NOW)
    write(src / "a.md", "v1")
    sync_pipeline(brain, "p", now=NOW + 86400 * 20)
    _, prov = prov_of(brain, f"raw/p/docs/2026-10-04{S}a.md")
    assert prov["first_seen"] == "2026-10-04" and prov["synced_at"].startswith("2026-10-24")
