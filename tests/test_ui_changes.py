"""MD-122: Home rows and view mode carry the last sync's "3 new · 5 changed · 1 deleted" and its item list."""
import pytest

from conftest import write
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from uihelper import running_ui


@pytest.fixture
def world(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "# A\n", "b.md": "# B\n"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], name_policy="original"))
    return brain, src


def test_a_home_row_of_a_never_synced_pipeline_has_no_last_changes(world):
    brain, _ = world
    with running_ui(brain) as ui:
        _, state = ui.get("/api/state")
    assert state["pipelines"][0]["last_changes"] is None


def test_a_home_row_carries_the_last_sync_text_counts_and_items(world):
    brain, src = world
    sync_pipeline(brain, "p")
    write(src / "a.md", "# A2\n")
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        _, state = ui.get("/api/state")
    lc = state["pipelines"][0]["last_changes"]
    assert lc["text"] == "1 changed" and lc["counts"] == {"new": 0, "changed": 1, "deleted": 0}
    assert [(i["op"], i["title"]) for i in lc["items"]] == [("changed", "a")]


def test_a_long_item_list_is_capped_in_the_row_and_says_how_many_more(brain, add_pipeline, make_source):
    src = make_source("big", {f"f{i}.md": f"# {i}\n" for i in range(60)})
    add_pipeline(Pipeline.new("p", [Source(str(src), "big")], name_policy="original"))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        _, state = ui.get("/api/state")
    lc = state["pipelines"][0]["last_changes"]
    assert lc["counts"]["new"] == 60 and len(lc["items"]) == 50 and lc["more"] == 10


def test_view_mode_status_carries_the_same_last_changes(world):
    brain, _ = world
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        status, res = ui.get("/api/pipelines/p/view")
    assert status == 200 and res["status"]["last_changes"]["text"] == "2 new"
