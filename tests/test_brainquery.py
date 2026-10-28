"""MD-22: search / read / list over the brain's Markdown (raw/ and wiki/ only)."""
import os

import pytest

from conftest import write
from mdpipeline.brainquery import QueryError, list_path, read_file, search


@pytest.fixture
def populated(brain_dir, brain):
    write(brain_dir / "raw/projects/biz/pricing.md",
          "---\ntitle: Pricing decisions\nprovenance:\n  source: local\n  pipeline: biz\n  source_label: docs\n"
          "  source_path: pricing.md\n  synced_at: '2026-10-01T09:00:00Z'\n---\n# Pricing\n\nWe decided on a usage based pricing model.\nPricing tiers: free, team.\n")
    write(brain_dir / "raw/projects/biz/roadmap.md", "# Roadmap\n\nQ4 launch. Mentions pricing once.\n")
    write(brain_dir / "raw/notes.md", "hand written note about unrelated things\n")
    write(brain_dir / "wiki/concepts/guard.md", "# Guard\n\nThe big change guard protects the brain.\n")
    write(brain_dir / "work/secret.md", "pricing secret in work, must never be readable\n")
    write(brain_dir / ".mdpipeline/hidden.md", "pricing hidden bookkeeping\n")
    return brain_dir


def paths(results):
    return [r["path"] for r in results]


def test_search_ranks_files_with_matching_passages_and_provenance(populated, brain):
    res = search(brain, "pricing")
    assert paths(res)[0] == "raw/projects/biz/pricing.md"
    top = res[0]
    assert top["score"] > res[1]["score"]
    assert any("usage based pricing" in p["text"] for p in top["passages"])
    assert all(isinstance(p["line"], int) for p in top["passages"])
    assert top["provenance"]["pipeline"] == "biz" and top["provenance"]["source_path"] == "pricing.md"
    assert res[1]["provenance"] is None or isinstance(res[1]["provenance"], dict)


def test_search_scope_is_raw_and_wiki_only(populated, brain):
    assert not any(p.startswith(("work/", ".mdpipeline/")) for p in paths(search(brain, "pricing")))
    assert "wiki/concepts/guard.md" in paths(search(brain, "guard"))


def test_search_is_case_insensitive_and_files_with_all_terms_rank_first(populated, brain):
    res = paths(search(brain, "PRICING launch"))
    assert res[0] == "raw/projects/biz/roadmap.md"  # has both terms


def test_search_phrase_in_quotes(populated, brain):
    assert paths(search(brain, '"usage based"')) == ["raw/projects/biz/pricing.md"]


def test_search_path_prefix_and_limit(populated, brain):
    assert set(paths(search(brain, "pricing", path_prefix="raw/projects"))) == {
        "raw/projects/biz/pricing.md", "raw/projects/biz/roadmap.md"}
    assert len(search(brain, "pricing", limit=1)) == 1
    assert paths(search(brain, "guard", path_prefix="raw")) == []


def test_search_no_match_is_empty_and_empty_query_is_an_error(populated, brain):
    assert search(brain, "zzzxxyy") == []
    with pytest.raises(QueryError):
        search(brain, "   ")


def test_search_prefix_outside_scope_is_refused(populated, brain):
    with pytest.raises(QueryError):
        search(brain, "pricing", path_prefix="work")


def test_search_handles_non_ascii_text(brain_dir, brain):
    write(brain_dir / "raw/he.md", "החלטה על תמחור המוצר\n")
    assert paths(search(brain, "תמחור")) == ["raw/he.md"]


def test_read_returns_content_and_parsed_provenance(populated, brain):
    r = read_file(brain, "raw/projects/biz/pricing.md")
    assert "usage based pricing" in r["content"] and r["content"].startswith("---")
    assert r["provenance"]["pipeline"] == "biz" and r["path"] == "raw/projects/biz/pricing.md"


def test_read_file_without_provenance(populated, brain):
    assert read_file(brain, "raw/notes.md")["provenance"] is None


def test_read_can_read_wiki(populated, brain):
    assert "big change guard" in read_file(brain, "wiki/concepts/guard.md")["content"]


@pytest.mark.parametrize("bad", ["work/secret.md", ".mdpipeline/hidden.md", "../outside.md", "/etc/passwd", "raw/../work/secret.md",
                                 "raw/../../etc/passwd", "raw/missing.md", "raw", "raw/projects", "CLAUDE.md", "raw/x.txt", ""])
def test_read_refuses_anything_outside_scope_or_not_markdown(populated, brain, bad):
    with pytest.raises(QueryError):
        read_file(brain, bad)


def test_read_refuses_symlinks_that_escape(populated, brain, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    os.symlink(outside, populated / "raw/link.md")
    os.symlink(populated / "work", populated / "raw/linkdir")
    with pytest.raises(QueryError):
        read_file(brain, "raw/link.md")
    with pytest.raises(QueryError):
        read_file(brain, "raw/linkdir/secret.md")
    assert "raw/link.md" not in paths(search(brain, "secret"))
    assert all("secret" not in p for p in paths(search(brain, "pricing")))


def test_list_root_shows_only_raw_and_wiki(populated, brain):
    r = list_path(brain)
    assert [f["path"] for f in r["folders"]] == ["raw", "wiki"] and r["files"] == []


def test_list_a_folder_shows_files_with_provenance_summary_and_subfolders(populated, brain):
    r = list_path(brain, "raw/projects/biz")
    assert [f["path"] for f in r["files"]] == ["raw/projects/biz/pricing.md", "raw/projects/biz/roadmap.md"]
    assert r["files"][0]["provenance"]["pipeline"] == "biz"
    assert r["files"][1]["provenance"] is None
    top = list_path(brain, "raw")
    assert [f["path"] for f in top["folders"]] == ["raw/projects"] and top["folders"][0]["md_files"] == 2
    assert [f["path"] for f in top["files"]] == ["raw/notes.md"]


@pytest.mark.parametrize("bad", ["work", "../x", "/", ".mdpipeline", "raw/nonexistent", "raw/notes.md"])
def test_list_refuses_out_of_scope_or_missing(populated, brain, bad):
    with pytest.raises(QueryError):
        list_path(brain, bad)


def test_non_markdown_files_are_never_listed_or_searched(populated, brain):
    (populated / "raw/data.json").write_text('{"pricing": 1}')
    assert "raw/data.json" not in [f["path"] for f in list_path(brain, "raw")["files"]]
    assert "raw/data.json" not in paths(search(brain, "pricing"))


def test_queries_never_modify_the_brain(populated, brain):
    from conftest import tree_snapshot

    before = tree_snapshot(populated)
    search(brain, "pricing"); list_path(brain, "raw"); read_file(brain, "raw/notes.md")
    assert tree_snapshot(populated) == before
