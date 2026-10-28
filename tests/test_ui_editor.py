"""MD-26: the pipeline editor API: folder checks, live dry-run preview, validation next to fields, save."""
import pytest

from conftest import write
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
from uihelper import running_ui


def draft(sources, **kw):
    d = {"name": "p", "sources": [{"path": str(p), "label": l} for p, l in sources],
         "target": "raw/{pipeline}/{source}/{relpath}", "include": ["**/*.md"],
         "exclude": [".git/**", "node_modules/**", ".obsidian/**", "**/.*/**"], "on_source_delete": "delete",
         "guard_threshold_percent": 40, "guard_min_files": 5, "name_policy": "original"}       # MD-148: these tests are about preview mechanics, not names
    d.update(kw)
    return d


def errs(res):
    return {e["field"]: e["message"] for e in res["validation"]["errors"]}


@pytest.fixture
def src2(make_source):
    a = make_source("raw", {"a1.md": "1", "a2.md": "2", "sub/a3.md": "3", ".hid/x.md": "x"})
    b = make_source("wiki", {"index.md": "i", "concepts/c.md": "c"})
    return a, b


# folder check -----------------------------------------------------------------------------------
def test_folder_check_counts_markdown_files_and_expands_tilde(brain, src2, monkeypatch, tmp_path):
    a, _ = src2
    monkeypatch.setenv("HOME", str(tmp_path / "sources"))
    with running_ui(brain) as ui:
        _, res = ui.post("/api/folder-check", {"path": str(a)})
        assert res == {"exists": True, "is_dir": True, "md_files": 4, "path": str(a)}
        _, res = ui.post("/api/folder-check", {"path": "~/raw"})
        assert res["exists"] and res["md_files"] == 4 and res["path"] == str(a)
        _, res = ui.post("/api/folder-check", {"path": str(a / "nope")})
        assert res["exists"] is False
        _, res = ui.post("/api/folder-check", {"path": str(a / "a1.md")})
        assert res["exists"] is True and res["is_dir"] is False


# preview ----------------------------------------------------------------------------------------
def test_preview_groups_by_folder_with_counts_and_reasons(brain, src2):
    a, b = src2
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(a, "raw"), (b, "wiki")])})
    assert res["validation"]["errors"] == []
    groups = res["preview"]["groups"]
    assert [g["label"] for g in groups] == ["raw", "wiki"]
    assert groups[0]["path"] == str(a) and groups[0]["counts"]["create"] == 3 and groups[0]["counts"]["skip"] == 1
    items = {i["source"]: i for i in groups[0]["items"]}
    assert items["a1.md"]["op"] == "create" and items["a1.md"]["target"] == "raw/p/raw/a1.md"
    assert items[".hid/x.md"]["op"] == "skip" and "excluded by rule" in items[".hid/x.md"]["note"]
    c = res["preview"]["counts"]
    assert (c["all"], c["create"], c["skip"]) == (6, 5, 1)
    assert res["preview"]["example"] == {"source": "raw: a1.md", "target": "raw/p/raw/a1.md"}


def test_preview_writes_nothing_and_never_modifies_sources(brain, brain_dir, src2):
    from conftest import tree_snapshot

    a, b = src2
    before = (tree_snapshot(brain_dir), tree_snapshot(a, b))
    with running_ui(brain) as ui:
        ui.post("/api/preview", {"draft": draft([(a, "raw"), (b, "wiki")])})
    assert (tree_snapshot(brain_dir), tree_snapshot(a, b)) == before


def test_preview_of_an_existing_pipeline_shows_update_delete_unchanged(brain, add_pipeline, make_source):
    src = make_source("docs", {"keep.md": "k", "change.md": "v1", "gone.md": "g"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    (src / "change.md").write_text("v2")
    (src / "gone.md").unlink()
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(src, "docs")]), "editing": "p"})
    ops = {i["source"] or i["target"]: i["op"] for g in res["preview"]["groups"] for i in g["items"]}
    assert ops["keep.md"] == "skip" and ops["change.md"] == "update" and ops["raw/p/docs/gone.md"] == "delete"
    keep = next(i for g in res["preview"]["groups"] for i in g["items"] if i["source"] == "keep.md")
    assert keep["note"] == "unchanged"


@pytest.mark.parametrize("target,field", [("raw/../wiki/{relpath}", "target"), ("/etc/{relpath}", "target"),
                                          ("wiki/{relpath}", "target"), ("raw/{bogus}", "target")])
def test_template_errors_are_attached_to_the_target_field(brain, src2, target, field):
    a, _ = src2
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(a, "raw")], target=target)})
    assert field in errs(res) and res["validation"]["can_save"] is False
    assert res["preview"]["groups"] == [] or all(not g["items"] for g in res["preview"]["groups"])


def test_missing_folder_is_a_warning_on_its_row_and_save_stays_possible(brain, src2, tmp_path):
    a, _ = src2
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(a, "raw"), (tmp_path / "unmounted", "usb")])})
    v = res["validation"]
    assert v["errors"] == [] and v["can_save"] is True and v["can_save_and_sync"] is True
    assert any(w["code"] == "source-missing" for w in v["warnings"])
    usb = res["preview"]["groups"][1]
    assert usb["status"] == "missing" and "not found" in usb["error"]


def test_all_folders_missing_disables_save_and_sync_only(brain, tmp_path):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(tmp_path / "nope", "x")])})
    assert res["validation"]["can_save"] is True and res["validation"]["can_save_and_sync"] is False


def test_zero_matching_files_is_a_warning_and_disables_save_and_sync(brain, make_source):
    src = make_source("t", {"readme.txt": "x"})
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(src, "t")])})
    v = res["validation"]
    assert v["can_save"] is True and v["can_save_and_sync"] is False and any(w["code"] == "no-files" for w in v["warnings"])


def test_nested_duplicate_label_and_brain_errors_are_attached_to_the_offending_row(brain, brain_dir, src2):
    a, _ = src2
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(a, "raw"), (a / "sub", "raw")])})
        e = errs(res)
        assert "sources[1].path" in e and "nested" in e["sources[1].path"]
        assert "sources[1].label" in e
        _, res = ui.post("/api/preview", {"draft": draft([(brain_dir / "raw", "r"), (brain_dir.parent, "p")])})
        e = errs(res)
        assert "inside the folder the files are written to" in e["sources[0].path"] and "contains your output folder" in e["sources[1].path"]


def test_collision_between_two_folders_blocks_save_and_the_preview_stays_visible(brain, make_source):
    a = make_source("a", {"index.md": "1", "x.md": "x"})
    b = make_source("b", {"index.md": "2"})
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(a, "a"), (b, "b")], target="raw/{pipeline}/{relpath}")})
    assert "target" in errs(res) and "both resolve to" in errs(res)["target"]
    assert res["validation"]["can_save"] is False
    assert res["preview"]["counts"]["collision"] == 2      # both sides of the collision are shown
    assert any(i["op"] == "collision" for g in res["preview"]["groups"] for i in g["items"])


def test_collision_with_another_pipeline_is_reported(brain, add_pipeline, make_source):
    a = make_source("a", {"index.md": "1"})
    b = make_source("b", {"index.md": "2"})
    add_pipeline(Pipeline.new("other", [str(a)], target="raw/shared/{relpath}"))
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(b, "b")], target="raw/shared/{relpath}", name="mine")})
    assert "target" in errs(res)


def test_guard_banner_data_when_the_edit_would_trip_the_guard(brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": "x" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(src, "docs")], target="raw/moved/{relpath}"), "editing": "p"})
    g = res["preview"]["guard"]
    assert g["blocked"] is True and g["count"] == 10 and g["total"] == 10 and "100%" in g["message"]


def test_removed_folder_is_grouped_and_its_files_are_kept_unowned_by_default(brain, add_pipeline, make_source):
    a = make_source("a", {"1.md": "1"})
    b = make_source("b", {"2.md": "2", "3.md": "3"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft([(a, "A")]), "editing": "p"})
        removed = [g for g in res["preview"]["groups"] if g.get("removed")][0]
        assert removed["label"] == "B" and removed["owned"] == 2
        assert all(i["op"] == "skip" and "unowned" in i["note"] for i in removed["items"])
        _, res = ui.post("/api/preview", {"draft": draft([(a, "A")]), "editing": "p", "delete_labels": ["B"]})
        removed = [g for g in res["preview"]["groups"] if g.get("removed")][0]
        assert [i["op"] for i in removed["items"]] == ["delete", "delete"]
        assert res["preview"]["counts"]["delete"] == 2


# save -------------------------------------------------------------------------------------------
def test_create_saves_a_valid_pipeline_with_warnings_and_returns_them(brain, src2, tmp_path):
    a, _ = src2
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft([(a, "raw"), (tmp_path / "unmounted", "usb")])})
    assert status == 200 and res["saved"] is True and any(w["code"] == "source-missing" for w in res["warnings"])
    assert load_config(brain).get("p") is not None and not (brain.root / "raw/p").exists()


def test_create_with_errors_is_422_with_messages_and_saves_nothing(brain, src2):
    a, _ = src2
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft([(a, "raw")], target="../x/{relpath}")})
    assert status == 422 and res["errors"][0]["field"] == "target"
    assert load_config(brain).pipelines == []


def test_create_and_sync_runs_the_first_sync_with_trigger_ui(brain, src2):
    from mdpipeline.log import ActivityLog

    a, b = src2
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft([(a, "raw"), (b, "wiki")]), "sync": True})
    assert status == 200 and res["sync"]["result"] == "ok" and res["sync"]["counts"]["created"] == 5
    assert ActivityLog(brain).read(types={"run"})[-1]["trigger"] == "ui"
    assert (brain.root / "raw/p/wiki/index.md").exists()


def test_create_and_sync_with_zero_matches_saves_but_does_not_sync(brain, make_source):
    src = make_source("t", {"readme.txt": "x"})
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft([(src, "t")]), "sync": True})
    assert status == 200 and res["saved"] is True and res["sync"] is None and "0 files" in res["sync_skipped"]


def test_get_pipeline_returns_an_editable_draft(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)], on_source_delete="keep", guard_threshold_percent=25))
    with running_ui(brain) as ui:
        _, res = ui.get("/api/pipelines/p")
        assert ui.get("/api/pipelines/nope")[0] == 404
    d = res["draft"]
    assert d["name"] == "p" and d["on_source_delete"] == "keep" and d["guard_threshold_percent"] == 25
    assert d["sources"] == [{"path": str(src), "label": "docs", "enabled": True}] and res["owned"] == {"docs": 0}


def test_update_changes_rules_without_colliding_with_itself(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft([(src, "docs")], on_source_delete="keep")})
    assert status == 200 and load_config(brain).get("p").on_source_delete == "keep"


def test_update_rename_label_migrates_ownership_so_the_preview_shows_the_move(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        status, _ = ui.put("/api/pipelines/p", {"draft": draft([(src, "handbook")]), "relabels": {"docs": "handbook"}})
        _, res = ui.post("/api/preview", {"draft": draft([(src, "handbook")]), "editing": "p"})
    assert status == 200
    ops = sorted(i["op"] for g in res["preview"]["groups"] for i in g["items"])
    assert ops == ["create", "delete"]


def test_update_removing_a_folder_keeps_its_files_unowned_by_default(brain, add_pipeline, make_source):
    a = make_source("a", {"1.md": "1"})
    b = make_source("b", {"2.md": "2"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        status, _ = ui.put("/api/pipelines/p", {"draft": draft([(a, "A")]), "remove_folders": [{"label": "B", "delete_files": False}]})
    assert status == 200 and [s.label for s in load_config(brain).get("p").sources] == ["A"]
    assert (brain.root / "raw/p/B/2.md").exists()
    assert "raw/p/B/2.md" not in State.load(brain).pipeline("p").files


def test_update_removing_a_folder_with_delete_files_goes_through_the_guard(brain, add_pipeline, make_source):
    a = make_source("a", {"1.md": "1"})
    b = make_source("b", {f"b{i}.md": "x" for i in range(8)})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    body = {"draft": draft([(a, "A")]), "remove_folders": [{"label": "B", "delete_files": True}]}
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", body)
        assert status == 409 and res["result"] == "blocked" and res["guard"]["count"] == 8
        assert [s.label for s in load_config(brain).get("p").sources] == ["A", "B"]      # nothing changed
        status, res = ui.put("/api/pipelines/p", {**body, "approve": True})
    assert status == 200 and not list((brain.root / "raw/p/B").glob("*.md"))
    assert [s.label for s in load_config(brain).get("p").sources] == ["A"]


def test_update_with_validation_errors_changes_nothing_not_even_removals(brain, add_pipeline, make_source):
    a = make_source("a", {"1.md": "1"})
    b = make_source("b", {"2.md": "2"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        status, _ = ui.put("/api/pipelines/p", {"draft": draft([(a, "A")], target="../x/{relpath}"),
                                                "remove_folders": [{"label": "B", "delete_files": True}]})
    assert status == 422 and (brain.root / "raw/p/B/2.md").exists()
    assert [s.label for s in load_config(brain).get("p").sources] == ["A", "B"]
