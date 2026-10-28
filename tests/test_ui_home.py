"""MD-25: the local app opens from the CLI and shows the Pipelines home (list, status, Sync now, Sync all)."""
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import write
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.locking import brain_lock
from mdpipeline.log import ActivityLog
from mdpipeline.service import set_paused
from uihelper import running_ui

STATIC = Path(__file__).parent.parent / "src/mdpipeline/ui_static"


@pytest.fixture
def two(brain, add_pipeline, make_source):
    a = make_source("raw", {"1.md": "1", "2.md": "2"})
    b = make_source("wiki", {"3.md": "3"})
    c = make_source("single", {"s.md": "s"})
    add_pipeline(Pipeline.new("multi", [Source(str(a), "raw"), Source(str(b), "wiki")]))
    add_pipeline(Pipeline.new("one", [str(c)], target="raw/projects/one/{relpath}"))
    return brain


def rows(state):
    return {p["name"]: p for p in state["pipelines"]}


# --- reachability, offline, security ---------------------------------------------------
def test_server_binds_to_localhost_only(brain):
    with running_ui(brain) as ui:
        assert ui.server.server_address[0] == "127.0.0.1"


def test_index_page_is_served_and_has_no_external_resources(brain):
    with running_ui(brain) as ui:
        status, html = ui.get("/", token=False)
    assert status == 200 and "<title>" in html
    assert not re.findall(r'(?:src|href)\s*=\s*"https?://', html)
    for asset in STATIC.glob("*"):
        text = asset.read_text(encoding="utf-8")
        assert not re.findall(r'(?:src|href)\s*=\s*["\']https?://', text), asset
        assert not re.findall(r"url\(\s*['\"]?https?://", text), asset
        assert "cdn." not in text and "googleapis" not in text, asset


def test_requests_with_a_foreign_host_header_are_refused(brain):
    with running_ui(brain) as ui:
        status, _ = ui.get("/api/state", headers={"Host": "evil.example.com"})
    assert status == 403


def test_mutating_requests_need_the_session_token(two):
    with running_ui(two) as ui:
        assert ui.post("/api/pipelines/one/sync", token=False)[0] == 403
        assert ui.post("/api/pipelines/one/sync", token="wrong")[0] == 403
        assert ui.post("/api/pipelines/one/sync")[0] == 200


def test_cross_origin_posts_are_refused(two):
    with running_ui(two) as ui:
        status, _ = ui.post("/api/pipelines/one/sync", headers={"Origin": "http://evil.example.com"})
    assert status == 403
    assert not (two.root / "raw/projects").exists()


# --- the home state ---------------------------------------------------------------------
def test_empty_state_has_no_pipelines_the_summary_and_the_connect_panel(brain, brain_dir):
    write(brain_dir / "raw/n.md", "x")
    with running_ui(brain) as ui:
        _, st = ui.get("/api/state")
    assert st["pipelines"] == [] and st["badge"] == 0
    assert str(brain_dir) in st["brain"]["path"]
    assert "1 Markdown file already in raw/" in st["summary"] and "no pipelines yet" in st["summary"]
    assert "claude mcp add" in st["connect"]["claude_code_command"] and "[mcp_servers." in st["connect"]["codex_toml"]


def test_each_row_shows_name_folders_target_status_and_last_sync(two):
    sync_pipeline(two, "one")
    with running_ui(two) as ui:
        _, st = ui.get("/api/state")
    multi, one = rows(st)["multi"], rows(st)["one"]
    assert multi["folders_text"] == "2 folders: raw, wiki" and [s["label"] for s in multi["sources"]] == ["raw", "wiki"]
    assert multi["sources"][0]["path"].endswith("/raw") and multi["target"] == "raw/{pipeline}/{source}/{relpath}"
    assert one["folders_text"].endswith("/single") and one["target"] == "raw/projects/one/{relpath}"
    assert multi["status"] == "never" and multi["last_sync_ts"] is None
    assert one["status"] == "ok" and one["last_sync_ts"] and one["age_seconds"] < 5 and one["summary"] == "1 created"


@pytest.mark.parametrize("setup,expected_status,links", [
    ("conflict", "conflicts", True),
    ("error", "error", True),
    ("paused", "paused", False),
])
def test_status_values_and_problem_statuses_link_to_activity(two, tmp_path, setup, expected_status, links):
    sync_pipeline(two, "one")
    if setup == "conflict":
        (two.root / "raw/projects/one/s.md").write_text("edited")
        sync_pipeline(two, "one")
    elif setup == "error":
        cfg = load_config(two)
        (tmp_path / "sources/single").rename(tmp_path / "gone")
        sync_pipeline(two, "one")
    else:
        set_paused(two, "one", True, trigger="cli")
    with running_ui(two) as ui:
        _, st = ui.get("/api/state")
    row = rows(st)["one"]
    assert row["status"] == expected_status and row["links_to_activity"] is links


def test_blocked_status(brain, add_pipeline, make_source):
    src = make_source("big", {f"n{i}.md": "x" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    for i in range(6):
        (src / f"n{i}.md").unlink()
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        _, st = ui.get("/api/state")
    assert rows(st)["p"]["status"] == "blocked" and rows(st)["p"]["links_to_activity"] is True
    assert "would delete" in rows(st)["p"]["summary"] and "6 of 10" in rows(st)["p"]["summary"]


def test_syncing_status_while_the_lock_is_held(two):
    with running_ui(two) as ui:
        with brain_lock(two):
            _, st = ui.get("/api/state")
        assert st["syncing"] is True
        assert ui.get("/api/state")[1]["syncing"] is False


# --- Sync now / Sync all -------------------------------------------------------------------
def test_sync_now_runs_the_pipeline_logged_with_trigger_ui(two):
    with running_ui(two) as ui:
        status, res = ui.post("/api/pipelines/one/sync")
    assert status == 200 and res["result"] == "ok" and res["counts"]["created"] == 1
    assert (two.root / "raw/projects/one/s.md").exists()
    assert ActivityLog(two).read(types={"run"})[-1]["trigger"] == "ui"


def test_sync_now_on_an_unknown_pipeline_is_404(two):
    with running_ui(two) as ui:
        assert ui.post("/api/pipelines/nope/sync")[0] == 404


def test_sync_all_runs_every_pipeline_that_is_not_paused_with_trigger_ui(two):
    set_paused(two, "one", True, trigger="ui")
    with running_ui(two) as ui:
        status, res = ui.post("/api/sync-all")
    results = {r["pipeline"]: r["result"] for r in res["results"]}
    assert status == 200 and results == {"multi": "ok", "one": "skipped: paused"}
    runs = ActivityLog(two).read(types={"run"})
    assert {r["pipeline"]: r["trigger"] for r in runs} == {"multi": "ui", "one": "ui"}


def test_pause_and_resume_from_the_ui(two):
    with running_ui(two) as ui:
        assert ui.post("/api/pipelines/one/pause")[0] == 200
        assert rows(ui.get("/api/state")[1])["one"]["status"] == "paused"
        assert ui.post("/api/pipelines/one/resume")[0] == 200
        assert rows(ui.get("/api/state")[1])["one"]["status"] == "never"
    ev = [(e["action"], e["trigger"]) for e in ActivityLog(two).read(types={"event"})]
    assert ev == [("pause", "ui"), ("resume", "ui")]


def test_busy_sync_is_reported_not_crashed(two):
    with running_ui(two) as ui:
        with brain_lock(two):
            status, res = ui.post("/api/pipelines/one/sync")
    assert status == 200 and res["result"] == "busy"


# --- the CLI command ------------------------------------------------------------------------
def test_cli_ui_command_serves_the_app_and_prints_the_url(two):
    proc = subprocess.Popen([sys.executable, "-m", "mdpipeline", "--brain", str(two.root), "ui", "--no-browser", "--port", "0"],
                            stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        url = re.search(r"http://127\.0\.0\.1:\d+/?", line).group(0)
        import urllib.request

        html = urllib.request.urlopen(url, timeout=10).read().decode()
        assert "<title>" in html
    finally:
        proc.terminate()
        proc.wait(timeout=10)
