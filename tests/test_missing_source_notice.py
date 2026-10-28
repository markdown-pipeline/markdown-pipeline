"""MD-136: a missing source folder is a calm 'Needs you' notice with two ways forward, not a red error. Nothing is ever deleted for it."""
import shutil

import pytest

from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
from mdpipeline.uiactivity import activity_view
from uihelper import running_ui


@pytest.fixture
def two(brain, add_pipeline, make_source):
    """Pipeline 'p' with folders A (2 files) and B (1 file), synced once."""
    a = make_source("A", {"a1.md": "# a1\n", "a2.md": "# a2\n"})
    b = make_source("B", {"b1.md": "# b1\n"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")], target="{relpath}", name_policy="original"))
    sync_pipeline(brain, "p")
    return a, b


def md(brain):
    return sorted(p.name for p in brain.root.rglob("*.md") if ".mdpipeline" not in p.parts)


def test_a_missing_folder_is_a_source_missing_card_and_not_an_error_card(brain, two):
    a, _ = two
    shutil.rmtree(a)
    sync_pipeline(brain, "p")
    needs = activity_view(brain)["needs_you"]
    assert [n["kind"] for n in needs] == ["source_missing"]
    card = needs[0]
    assert (card["pipeline"], card["label"], card["path"]) == ("p", "A", str(a))
    assert card["kept_files"] == 2


def test_a_missing_folder_plus_an_unreadable_file_gives_both_cards(brain, two):
    a, b = two
    shutil.rmtree(a)
    from mdpipeline.log import ActivityLog

    sync_pipeline(brain, "p")
    log = ActivityLog(brain)
    last = [e for e in log.read() if e["type"] == "run"][-1]
    last["errors"].append({"source": "B", "path": "b1.md", "error": "cannot read"})
    needs = _cards_for_run(brain, last)
    assert sorted(n["kind"] for n in needs) == ["error", "source_missing"]


def _cards_for_run(brain, run):
    from mdpipeline import uiactivity

    status = {"name": "p", "status": "error", "last_run": run, "conflicts": [], "credential_conflict": None}
    return uiactivity._error_cards(brain, status, load_config(brain))


def test_nothing_is_deleted_while_the_source_is_missing_even_after_many_syncs(brain, two):
    a, _ = two
    before = md(brain)
    shutil.rmtree(a)
    for _ in range(3):
        res = sync_pipeline(brain, "p")
        assert res.counts()["deleted"] == 0
    assert md(brain) == before
    assert len(State.load(brain).pipeline("p").files) == 3


def test_choose_new_location_saves_the_path_checks_it_and_syncs_nothing(brain, two, tmp_path):
    a, _ = two
    shutil.rmtree(a)
    moved = tmp_path / "moved" / "A"
    moved.mkdir(parents=True)
    (moved / "a1.md").write_text("# a1\n")
    (moved / "a2.md").write_text("# a2\n")
    before = md(brain)
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/source-path", {"label": "A", "path": str(moved)})
        assert status == 200, res
        assert res["saved"] is True
        _, plan = ui.get("/api/pipelines/p/plan")
    cfg = load_config(brain).get("p")
    assert {s.label: s.path for s in cfg.sources} == {"A": str(moved), "B": cfg.sources[1].path}
    assert md(brain) == before                                     # saving never writes; the plan shows the result first
    assert plan["preview"]["counts"].get("delete", 0) == 0


@pytest.mark.parametrize("bad", ["", "/nonexistent/nowhere"])
def test_choose_new_location_refuses_a_folder_that_does_not_exist_and_changes_nothing(brain, two, bad):
    a, _ = two
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/source-path", {"label": "A", "path": bad})
    assert status in (400, 409, 422)
    assert load_config(brain).get("p").sources[0].path == str(a)


def test_choose_new_location_refuses_a_file_and_an_unknown_label(brain, two, tmp_path):
    f = tmp_path / "file.md"
    f.write_text("x")
    with running_ui(brain) as ui:
        s1, _ = ui.post("/api/pipelines/p/source-path", {"label": "A", "path": str(f)})
        s2, _ = ui.post("/api/pipelines/p/source-path", {"label": "ZZ", "path": str(tmp_path)})
        s3, _ = ui.post("/api/pipelines/nope/source-path", {"label": "A", "path": str(tmp_path)})
    assert s1 in (400, 409, 422) and s2 in (400, 404) and s3 == 404


def test_remove_this_source_keeps_its_files_unowned(brain, two):
    a, _ = two
    shutil.rmtree(a)
    before = md(brain)
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/remove-source", {"label": "A"})
    assert status == 200 and res["kept"] == 2
    assert [s.label for s in load_config(brain).get("p").sources] == ["B"]
    assert md(brain) == before


def test_the_last_source_cannot_be_removed_from_the_notice(brain, two):
    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/remove-source", {"label": "A"})
        status, res = ui.post("/api/pipelines/p/remove-source", {"label": "B"})
    assert status in (400, 409)
    assert [s.label for s in load_config(brain).get("p").sources] == ["B"]


def test_the_view_mode_activity_for_a_pipeline_carries_the_card(brain, two):
    a, _ = two
    shutil.rmtree(a)
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        _, view = ui.get("/api/pipelines/p/view")
    assert [n["kind"] for n in view["needs_you"]] == ["source_missing"]


# AC3 (product, 2026-10-15): when the only problem is a missing source, every status surface says "needs you", not "error" -----------------
def _row_and_view(brain):
    from mdpipeline.pipelineview import status_view

    with running_ui(brain) as ui:
        row = ui.get("/api/state")[1]["pipelines"][0]
        hist = ui.get("/api/activity")[1]["history"]
    return row, status_view(brain, load_config(brain).get("p")), hist


def test_a_missing_source_alone_is_needs_you_in_the_row_the_view_status_and_the_run_history(brain, two):
    a, _ = two
    shutil.rmtree(a)
    sync_pipeline(brain, "p")
    row, view, hist = _row_and_view(brain)
    assert row["needs_you"] is True and view["needs_you"] is True
    assert row["summary"].startswith("needs you: ") and "not found" in row["summary"].lower()
    run = next(h for h in hist if h["kind"] == "run")
    assert run["needs_you"] is True


def test_a_real_failure_stays_an_error_even_next_to_a_missing_source(brain, two, monkeypatch):
    a, b = two
    shutil.rmtree(a)
    (b / "bad.md").write_text("---\nunclosed: [\n---\nbody\n")
    sync_pipeline(brain, "p")
    row, view, hist = _row_and_view(brain)
    assert row["needs_you"] is False and view["needs_you"] is False
    assert not row["summary"].startswith("needs you")


def test_a_healthy_pipeline_is_not_needs_you(brain, two):
    row, view, hist = _row_and_view(brain)
    assert row["needs_you"] is False and view["needs_you"] is False
    assert all(h.get("needs_you") is False for h in hist if h["kind"] == "run")
