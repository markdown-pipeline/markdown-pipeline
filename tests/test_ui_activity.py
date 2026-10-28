"""MD-27 / MD-29 / MD-28: Activity with 'Needs you', run history, approve/cancel/force, open file, live updates."""
import subprocess
import sys
import time

import pytest

from conftest import tree_snapshot
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.service import set_paused
from uihelper import running_ui


@pytest.fixture
def ten(brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": f"note {i}" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    return brain, src


def activity(ui):
    status, res = ui.get("/api/activity")
    assert status == 200
    return res


def kinds(res):
    return [i["kind"] for i in res["needs_you"]]


# history -------------------------------------------------------------------------------------
def test_empty_activity(brain):
    with running_ui(brain) as ui:
        res = activity(ui)
    assert res["needs_you"] == [] and res["history"] == [] and res["badge"] == 0


def test_run_history_rows_have_time_pipeline_trigger_counts_result_duration_and_file_changes(ten):
    brain, src = ten
    (src / "n0.md").write_text("changed")
    sync_pipeline(brain, "p", trigger="mcp")
    with running_ui(brain) as ui:
        res = activity(ui)
    newest, oldest = res["history"][0], res["history"][1]
    assert newest["kind"] == "run" and newest["trigger"] == "mcp" and newest["result"] == "ok"
    assert newest["counts"]["updated"] == 1 and isinstance(newest["duration_ms"], int) and newest["ts"]
    assert newest["details"] == [{"op": "update", "target": "raw/p/docs/n0.md", "note": ""}]
    assert oldest["trigger"] == "cli" and oldest["counts"]["created"] == 10 and len(oldest["details"]) == 10


def test_history_includes_pause_resume_and_skipped_paused_events(ten):
    brain, _ = ten
    set_paused(brain, "p", True, trigger="ui")
    sync_pipeline(brain, "p", trigger="mcp")
    set_paused(brain, "p", False, trigger="cli")
    with running_ui(brain) as ui:
        res = activity(ui)
    rows = [(r["kind"], r.get("action") or r["result"], r["trigger"]) for r in res["history"][:3]]
    assert rows == [("event", "resume", "cli"), ("run", "skipped: paused", "mcp"), ("event", "pause", "ui")]


def test_mcp_tool_calls_are_not_part_of_the_visible_history(ten):
    brain, _ = ten
    ActivityLog(brain).mcp_call("search")
    with running_ui(brain) as ui:
        assert all(r["kind"] != "mcp_call" for r in activity(ui)["history"])


# needs you: errors ------------------------------------------------------------------------------
def test_a_missing_folder_of_two_is_a_calm_source_missing_card(brain, add_pipeline, make_source, tmp_path):
    """MD-136: not an error card; the pipeline still counts as needing attention (badge)."""
    a = make_source("a", {"1.md": "1", "2.md": "2"})
    b = make_source("b", {"3.md": "3", "4.md": "4", "5.md": "5"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    (a / "1.md").write_text("changed")
    b.rename(tmp_path / "moved")
    sync_pipeline(brain, "p", trigger="mcp")
    with running_ui(brain) as ui:
        res = activity(ui)
    [card] = res["needs_you"]
    assert card["kind"] == "source_missing" and card["pipeline"] == "p" and card["trigger"] == "mcp"
    assert (card["label"], card["path"], card["kept_files"]) == ("B", str(b), 3)
    assert res["badge"] == 1


def test_the_only_folder_missing_is_a_source_missing_card_with_its_kept_files(brain, add_pipeline, make_source, tmp_path):
    a = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(a)]))
    sync_pipeline(brain, "p")
    a.rename(tmp_path / "moved")
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        card = activity(ui)["needs_you"][0]
    assert card["kind"] == "source_missing" and card["kept_files"] == 1


def test_file_level_errors_are_listed_on_the_error_card(brain, add_pipeline, make_source):
    src = make_source("a", {"ok.md": "ok", "bad.md": "---\nkey: [oops\n---\nx"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        card = activity(ui)["needs_you"][0]
    assert card["kind"] == "error" and card["file_errors"][0]["path"] == "bad.md" and "front-matter" in card["file_errors"][0]["error"]


# needs you: guard -------------------------------------------------------------------------------
def blocked(brain, src, count=6, trigger="mcp"):
    for i in range(count):
        (src / f"n{i}.md").unlink()
    sync_pipeline(brain, "p", trigger=trigger)


def test_guard_card_lists_counts_percent_and_files_and_who_triggered_it(ten):
    brain, src = ten
    blocked(brain, src)
    (src / "fresh.md").write_text("new")
    with running_ui(brain) as ui:
        res = activity(ui)
    card = res["needs_you"][0]
    assert card["kind"] == "guard" and card["pipeline"] == "p" and card["trigger"] == "mcp"
    assert (card["count"], card["total"], card["percent"], card["threshold"]) == (6, 10, 60.0, 40)
    assert card["will_delete"] == [f"raw/p/docs/n{i}.md" for i in range(6)]
    assert card["will_create"] == ["raw/p/docs/fresh.md"]
    assert card["cli_hint"] == "mdpipeline sync p --approve" and res["badge"] == 1


def test_needs_you_order_is_errors_then_guard_then_conflicts(brain, add_pipeline, make_source, tmp_path):
    big = make_source("big", {f"n{i}.md": "x" for i in range(10)})
    conf = make_source("conf", {"c.md": "c"})
    gone = make_source("gone", {"g.md": "g"})
    add_pipeline(Pipeline.new("conf", [str(conf)]))
    add_pipeline(Pipeline.new("p", [str(big)]))
    add_pipeline(Pipeline.new("gone", [str(gone)]))
    for n in ("conf", "p", "gone"):
        sync_pipeline(brain, n)
    (brain.root / "raw/conf/conf/c.md").write_text("edit")
    sync_pipeline(brain, "conf")
    blocked(brain, big)
    gone.rename(tmp_path / "x")
    sync_pipeline(brain, "gone")
    with running_ui(brain) as ui:
        res = activity(ui)
    assert kinds(res) == ["source_missing", "guard", "conflicts"] and res["badge"] == 3


# approve / cancel / force ---------------------------------------------------------------------------
def test_approve_from_the_ui_syncs_and_logs_trigger_ui(ten):
    brain, src = ten
    blocked(brain, src)
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/approve")
        assert status == 200 and res["result"] == "ok" and res["counts"]["deleted"] == 6
        assert activity(ui)["needs_you"] == []
    log = ActivityLog(brain).read()
    assert [e for e in log if e["type"] == "event" and e["action"] == "approve"][-1]["trigger"] == "ui"
    assert [e for e in log if e["type"] == "run"][-1]["trigger"] == "ui"


def test_approval_covers_exactly_the_shown_set_if_sources_changed_it_asks_again(ten):
    brain, src = ten
    blocked(brain, src)
    (src / "n6.md").unlink()                      # changed after the card was shown
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/approve")
        assert status == 200 and res["result"] == "blocked" and "changed since" in res["message"]
        card = activity(ui)["needs_you"][0]
        assert card["count"] == 7
        assert ui.post("/api/pipelines/p/approve")[1]["result"] == "ok"


def test_cancel_from_the_ui_records_cancelled_and_the_row_stays_blocked(ten):
    brain, src = ten
    blocked(brain, src)
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/cancel")
        assert status == 200 and res["result"] == "cancelled"
        state = ui.get("/api/state")[1]
        assert state["pipelines"][0]["status"] == "blocked" and state["pipelines"][0]["pending_approval"] is False
        assert activity(ui)["needs_you"] == []
        assert activity(ui)["history"][0]["result"] == "cancelled" or activity(ui)["history"][0].get("action") == "cancel"


def test_cancel_with_nothing_pending_is_a_409_style_error(ten):
    brain, _ = ten
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/cancel")
    assert status == 409 and "nothing to cancel" in res["error"]


# conflicts ------------------------------------------------------------------------------------------
@pytest.fixture
def conflicts(ten):
    brain, src = ten
    (brain.root / "raw/p/docs/n1.md").write_text("my edit")
    (src / "n2.md").write_text("src changed")
    (brain.root / "raw/p/docs/hand.md").write_text("hand written")
    (src / "hand.md").write_text("collides")
    sync_pipeline(brain, "p")
    return brain, src


def test_conflict_card_lists_type_path_and_reason(conflicts):
    brain, _ = conflicts
    with running_ui(brain) as ui:
        res = activity(ui)
    card = res["needs_you"][0]
    assert card["kind"] == "conflicts" and card["pipeline"] == "p"
    by_path = {c["path"]: c for c in card["items"]}
    assert by_path["raw/p/docs/n1.md"]["type"] == "b" and by_path["raw/p/docs/hand.md"]["type"] == "a"
    assert "edited in your output folder" in by_path["raw/p/docs/n1.md"]["reason"]
    assert by_path["raw/p/docs/n1.md"]["source_path"] == "n1.md"
    assert res["badge"] == 2


def test_force_only_overwrites_the_chosen_type_b_file_logged_with_trigger_ui(conflicts):
    brain, src = conflicts
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines/p/force", {"paths": ["raw/p/docs/n1.md"]})
        assert status == 200
    assert "my edit" not in (brain.root / "raw/p/docs/n1.md").read_text()
    assert (brain.root / "raw/p/docs/hand.md").read_text() == "hand written"
    ev = [e for e in ActivityLog(brain).read(types={"event"}) if e["action"] == "force"][-1]
    assert ev["trigger"] == "ui" and ev["paths"] == ["raw/p/docs/n1.md"]


def test_force_on_a_type_a_conflict_does_nothing(conflicts):
    brain, _ = conflicts
    with running_ui(brain) as ui:
        ui.post("/api/pipelines/p/force", {"paths": ["raw/p/docs/hand.md"]})
    assert (brain.root / "raw/p/docs/hand.md").read_text() == "hand written"


def test_force_requires_paths(conflicts):
    brain, _ = conflicts
    with running_ui(brain) as ui:
        assert ui.post("/api/pipelines/p/force", {"paths": []})[0] == 400


# open file (MD-29) --------------------------------------------------------------------------------------
def test_open_calls_the_os_opener_with_the_absolute_file_path(ten):
    brain, _ = ten
    opened = []
    with running_ui(brain, opener=lambda p: opened.append(p)) as ui:
        status, res = ui.post("/api/open", {"path": "raw/p/docs/n1.md"})
    assert status == 200 and res["ok"] is True and opened == [str(brain.root / "raw/p/docs/n1.md")]


@pytest.mark.parametrize("bad", ["work/x.md", "../x.md", "/etc/passwd", "raw/p/docs", "raw/missing.md", ".mdpipeline/config.yaml", "raw/p/docs/n1.txt"])
def test_open_refuses_anything_that_is_not_a_markdown_file_in_raw_or_wiki(ten, bad):
    brain, _ = ten
    (brain.root / "work/x.md").write_text("x")
    opened = []
    with running_ui(brain, opener=lambda p: opened.append(p)) as ui:
        status, res = ui.post("/api/open", {"path": bad})
    assert status == 400 and opened == []


def test_open_failure_is_reported_so_copy_path_remains_available(ten):
    brain, _ = ten

    def broken(path):
        raise OSError("no default application")

    with running_ui(brain, opener=broken) as ui:
        status, res = ui.post("/api/open", {"path": "raw/p/docs/n1.md"})
    assert status == 200 and res["ok"] is False and "no default application" in res["error"]
    assert res["absolute_path"] == str(brain.root / "raw/p/docs/n1.md")


def test_default_opener_uses_xdg_open_or_open_without_a_shell(monkeypatch):
    from mdpipeline import uiapi

    calls = []

    class FakePopen:
        def __init__(self, args, **kw):
            calls.append((args, kw))

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(sys, "platform", "linux")
    uiapi.os_open("/tmp/some file.md")
    assert calls[0][0] == ["xdg-open", "/tmp/some file.md"] and not calls[0][1].get("shell")
    monkeypatch.setattr(sys, "platform", "darwin")
    uiapi.os_open("/tmp/a.md")
    assert calls[1][0] == ["open", "/tmp/a.md"]


# live updates (MD-28) ---------------------------------------------------------------------------------------
def test_version_token_changes_after_a_cli_or_mcp_sync_run_outside_the_ui(ten):
    brain, src = ten
    with running_ui(brain) as ui:
        v1 = ui.get("/api/version")[1]["version"]
        (src / "n0.md").write_text("changed")
        subprocess.run([sys.executable, "-m", "mdpipeline", "--brain", str(brain.root), "sync", "p"], check=True, capture_output=True)
        v2 = ui.get("/api/version")[1]["version"]
        assert v1 != v2
        assert ui.get("/api/version")[1]["version"] == v2         # stable while nothing happens
        assert ui.get("/api/state")[1]["version"] == v2


def test_state_and_activity_reflect_an_external_sync_without_a_restart(ten):
    brain, src = ten
    with running_ui(brain) as ui:
        assert activity(ui)["history"][0]["trigger"] == "cli"
        (src / "n0.md").write_text("changed")
        sync_pipeline(brain, "p", trigger="mcp")
        assert activity(ui)["history"][0]["trigger"] == "mcp"
        assert ui.get("/api/state")[1]["pipelines"][0]["last_run"]["trigger"] == "mcp"
