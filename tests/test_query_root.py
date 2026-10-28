"""MD-133 AC6: MCP search/read/list cover each pipeline's output files wherever they live (not only raw/ and wiki/), while a user's own
files in the same folder are not exposed. Existing raw/ and wiki/ paths keep working."""
import pytest

from conftest import write
from mdpipeline import brainquery
from mdpipeline.brainquery import QueryError
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline


@pytest.fixture
def world(brain, brain_dir, add_pipeline, make_source):
    src = make_source("docs", {"top.md": "# Top\nzebrafish in the root\n", "Jira/ACME-1.md": "# One\nzebrafish in a folder\n"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], target="{relpath}", name_policy="original"))
    sync_pipeline(brain, "p")
    write(brain_dir / "README.md", "# my readme\nzebrafish in the users own file\n")
    write(brain_dir / "work" / "private.md", "# private\nzebrafish private\n")
    write(brain_dir / "wiki" / "w.md", "# wiki\nzebrafish in wiki\n")
    write(brain_dir / "raw" / "legacy.md", "# legacy\nzebrafish in raw\n")
    write(brain_dir / ".mdpipeline" / "notes.md", "zebrafish hidden")
    return brain


def paths(results):
    return sorted(r["path"] for r in results)


def test_search_finds_the_pipelines_files_at_the_root_and_in_folders(world):
    found = paths(brainquery.search(world, "zebrafish"))
    assert "top.md" in found and "Jira/ACME-1.md" in found


def test_search_still_finds_raw_and_wiki_files_without_provenance(world):
    found = paths(brainquery.search(world, "zebrafish"))
    assert "wiki/w.md" in found and "raw/legacy.md" in found


def test_a_users_own_file_in_the_same_folder_is_not_searchable(world):
    found = paths(brainquery.search(world, "zebrafish"))
    assert "README.md" not in found and "work/private.md" not in found and not any(p.startswith(".mdpipeline") for p in found)


def test_read_returns_a_pipeline_file_at_the_root_and_in_a_folder(world):
    assert "zebrafish in the root" in brainquery.read_file(world, "top.md")["content"]
    assert brainquery.read_file(world, "Jira/ACME-1.md")["provenance"]["pipeline"] == "p"


@pytest.mark.parametrize("path", ["README.md", "work/private.md", ".mdpipeline/notes.md", ".mdpipeline/config.yaml", ".git/config.md"])
def test_read_refuses_the_users_own_files_and_the_apps_folder(world, path):
    with pytest.raises(QueryError):
        brainquery.read_file(world, path)


def test_read_still_reads_raw_and_wiki_files_without_provenance(world):
    assert "in wiki" in brainquery.read_file(world, "wiki/w.md")["content"]
    assert "in raw" in brainquery.read_file(world, "raw/legacy.md")["content"]


def test_path_prefix_narrows_the_search_to_a_folder_of_the_output(world):
    assert paths(brainquery.search(world, "zebrafish", path_prefix="Jira")) == ["Jira/ACME-1.md"]


def test_list_at_the_top_shows_output_folders_and_files_but_not_the_users_files(world):
    top = brainquery.list_path(world)
    folders = {f["path"]: f["md_files"] for f in top["folders"]}
    assert folders["Jira"] == 1 and "raw" in folders and "wiki" in folders
    assert "work" not in folders and not any(p.startswith(".") for p in folders)
    assert [f["path"] for f in top["files"]] == ["top.md"]
    assert top["files"][0]["provenance"]["pipeline"] == "p"


def test_list_inside_an_output_folder_shows_its_files(world):
    assert [f["path"] for f in brainquery.list_path(world, "Jira")["files"]] == ["Jira/ACME-1.md"]


def test_a_symlink_never_leads_out(world, brain_dir, tmp_path):
    import os

    outside = tmp_path / "outside"
    write(outside / "x.md", "---\nprovenance:\n  pipeline: p\n---\nzebrafish outside\n")
    os.symlink(outside, brain_dir / "Jira" / "link")
    assert "link/x.md" not in " ".join(paths(brainquery.search(world, "zebrafish")))
    with pytest.raises(QueryError):
        brainquery.read_file(world, "Jira/link/x.md")


def test_another_directory_target_is_searchable_at_its_root(brain, add_pipeline, make_source, tmp_path):
    other = tmp_path / "other-out"
    other.mkdir()
    src = make_source("docs", {"n.md": "# N\nzebrafish elsewhere\n"})
    add_pipeline(Pipeline.new("q", [Source(str(src), "docs")], target="{relpath}", name_policy="original",
                              destination={"type": "directory", "path": str(other)}))
    sync_pipeline(brain, "q")
    write(other / "README.md", "zebrafish users file elsewhere")
    found = paths(brainquery.search(brain, "zebrafish"))
    assert found == ["@q/n.md"]
    assert "zebrafish elsewhere" in brainquery.read_file(brain, "@q/n.md")["content"]
    assert [f["path"] for f in brainquery.list_path(brain, "@q")["files"]] == ["@q/n.md"]
