"""MD-86: MCP search/read/list cover every pipeline target that has local files; GitHub targets without a clone are listed as not searchable."""
import asyncio
import json

import pytest

from conftest import write
from mcp.client import Client
from mdpipeline.brainquery import QueryError, list_path, read_file, search
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.mcp_server import build_server


def prov(pipeline, path):
    return f"---\nprovenance:\n  pipeline: {pipeline}\n  source_path: {path}\n---\n"


@pytest.fixture
def world(brain, brain_dir, make_source, add_pipeline, tmp_path):
    other = tmp_path / "vault"
    write(brain_dir / "raw/here/note.md", prov("here", "note.md") + "# Note\nunicorn pricing in the brain\n")
    write(other / "raw/there/doc.md", prov("there", "doc.md") + "# Doc\nunicorn pricing in the other folder\n")
    clone = tmp_path / "clone"
    write(clone / "raw/cloned/c.md", prov("cloned", "c.md") + "# C\nunicorn pricing in the clone\n")
    add_pipeline(Pipeline.new("here", [str(make_source("a", {"a.md": "A"}))]))
    add_pipeline(Pipeline.new("there", [str(make_source("b", {"b.md": "B"}))], destination={"type": "directory", "path": str(other)}))
    add_pipeline(Pipeline.new("cloned", [str(make_source("c", {"c.md": "C"}))],
                              destination={"type": "github", "repo": "acme/brain", "branch": "main", "credential": "vault:v_x", "clone": str(clone)}))
    add_pipeline(Pipeline.new("remote", [str(make_source("d", {"d.md": "D"}))],
                              destination={"type": "github", "repo": "acme/other", "branch": "main", "credential": "vault:v_y"}))
    return type("W", (), {"brain": brain, "other": other, "clone": clone})


def paths(results):
    return [r["path"] for r in results]


def test_search_covers_the_brain_folder_other_folders_and_clones_and_marks_where_each_came_from(world):
    results = search(world.brain, "unicorn")
    assert sorted(paths(results)) == sorted(["raw/here/note.md", "@there/raw/there/doc.md", "@cloned/raw/cloned/c.md"])
    by_path = {r["path"]: r for r in results}
    assert by_path["@there/raw/there/doc.md"]["provenance"]["pipeline"] == "there" and by_path["@cloned/raw/cloned/c.md"]["provenance"]["pipeline"] == "cloned"
    assert "directory" in by_path["@there/raw/there/doc.md"]["target"] and "clone" in by_path["@cloned/raw/cloned/c.md"]["target"]
    assert "target" not in by_path["raw/here/note.md"]


def test_read_follows_the_prefix_into_the_right_root(world):
    assert "other folder" in read_file(world.brain, "@there/raw/there/doc.md")["content"]
    assert "the clone" in read_file(world.brain, "@cloned/raw/cloned/c.md")["content"]
    assert read_file(world.brain, "@there/raw/there/doc.md")["path"] == "@there/raw/there/doc.md"
    assert "in the brain" in read_file(world.brain, "raw/here/note.md")["content"]


def test_list_shows_the_brain_the_extra_roots_and_the_targets_that_are_not_searchable_locally(world):
    top = list_path(world.brain)
    assert [f["path"] for f in top["folders"]] == ["raw", "wiki", "@cloned", "@there"]
    assert top["not_searchable"] == [{"pipeline": "remote", "target": "github acme/other (main)", "reason": "GitHub target without a local clone: set one with `pipeline edit NAME --clone DIR` to let agents search it"}]
    inner = list_path(world.brain, "@there")
    assert [f["path"] for f in inner["folders"]] == ["@there/raw"]
    assert [f["path"] for f in list_path(world.brain, "@there/raw/there")["files"]] == ["@there/raw/there/doc.md"]


def test_path_prefix_can_narrow_a_search_to_one_root(world):
    assert paths(search(world.brain, "unicorn", path_prefix="@there/raw")) == ["@there/raw/there/doc.md"]
    assert paths(search(world.brain, "unicorn", path_prefix="raw")) == ["raw/here/note.md"]


@pytest.mark.parametrize("bad", ["@nope/raw/x.md", "@there/work/x.md", "@there/../raw/x.md", "@/raw/x.md", "@there", "@there/.mdpipeline/state.json"])
def test_the_scope_rules_hold_inside_every_root(world, bad):
    with pytest.raises(QueryError):
        read_file(world.brain, bad)


def test_a_target_without_local_files_yet_is_not_an_error(world):
    import shutil

    shutil.rmtree(world.other)
    assert "@there/raw/there/doc.md" not in paths(search(world.brain, "unicorn")) and "raw/here/note.md" in paths(search(world.brain, "unicorn"))


def test_symlinks_are_not_followed_in_other_roots_either(world, tmp_path):
    import os

    outside = tmp_path / "outside.md"
    outside.write_text("unicorn secret")
    os.symlink(outside, world.other / "raw/there/link.md")
    assert "@there/raw/there/link.md" not in paths(search(world.brain, "unicorn"))
    with pytest.raises(QueryError):
        read_file(world.brain, "@there/raw/there/link.md")


def test_two_pipelines_sharing_a_root_show_it_once_under_the_first_name(world, make_source):
    cfg = load_config(world.brain)
    cfg.pipelines.append(Pipeline.new("there2", [str(make_source("e", {"e.md": "E"}))], destination={"type": "directory", "path": str(world.other)}))
    save_config(world.brain, cfg)
    assert [f["path"] for f in list_path(world.brain)["folders"]].count("@there") == 1
    assert not any(p.startswith("@there2") for p in paths(search(world.brain, "unicorn")))


def test_the_mcp_tools_work_across_targets_and_sync_status_reports_each_pipelines_target(world):
    async def calls():
        async with Client(build_server(world.brain)) as client:
            found = await client.call_tool("search", {"query": "unicorn"})
            status = await client.call_tool("sync_status", {})
            return [json.loads(c.text) for c in found.content], [json.loads(c.text) for c in status.content]

    found, status = asyncio.run(calls())
    assert {r["path"] for r in found[0]["results"]} == {"raw/here/note.md", "@there/raw/there/doc.md", "@cloned/raw/cloned/c.md"}
    targets = {p["name"]: p["target"] for p in status[0]["pipelines"]}
    assert targets["there"]["type"] == "directory" and targets["cloned"]["clone"].endswith("clone") and targets["remote"]["type"] == "github"
    assert all("credential" not in t for t in targets.values())


def test_sync_through_the_mcp_works_for_every_pipeline_target(world):
    async def calls():
        async with Client(build_server(world.brain)) as client:
            return await client.call_tool("sync", {"pipeline": "there"})

    res = asyncio.run(calls())
    assert (world.other / "raw").is_dir() and json.loads(res.content[0].text)["results"][0]["result"] in ("ok", "conflicts")


def test_a_conflict_in_another_target_can_be_opened_and_copied_with_the_right_paths(brain, make_source, add_pipeline, tmp_path):
    from mdpipeline.uiactivity import activity_view
    from uihelper import running_ui

    other = tmp_path / "vault"
    other.mkdir()
    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("there", [str(src)], destination={"type": "directory", "path": str(other)}, name_policy="original"))
    sync_pipeline(brain, "there", trigger="cli")
    target = next((other / "raw").rglob("a.md"))
    target.write_text(target.read_text() + "\nmy edit\n")
    (src / "a.md").write_text("A changed")
    assert sync_pipeline(brain, "there", trigger="cli").result == "conflicts"
    [card] = [c for c in activity_view(brain)["needs_you"] if c["kind"] == "conflicts"]
    item = card["items"][0]
    assert item["abs_path"] == str(target) and item["open_path"] == "@there/" + str(target.relative_to(other))
    opened = []
    with running_ui(brain, opener=opened.append) as ui:
        status, res = ui.post("/api/open", {"path": item["open_path"]})
    assert status == 200 and res["absolute_path"] == str(target) and opened == [str(target)]
