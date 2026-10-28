"""MD-94 (backend): the view-mode page of a pipeline also carries its status, its open 'Needs you' items and its last 10 runs."""
import pytest

from conftest import tree_snapshot
from fake_github import FakeGitHub
from mdpipeline import github_api
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.service import set_paused
from uihelper import running_ui


def detail(ui, name="p"):
    status, res = ui.get(f"/api/pipelines/{name}/view")
    assert status == 200, res
    return res


@pytest.fixture
def ten(brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": f"note {i}" for i in range(10)})
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], name_policy="original"))
    sync_pipeline(brain, "p")
    return brain, src


def test_status_has_the_homes_text_last_sync_counts_and_owned_files(ten):
    brain, src = ten
    (src / "n0.md").write_text("changed")
    sync_pipeline(brain, "p", trigger="mcp")
    with running_ui(brain) as ui:
        res = detail(ui)
        _, state = ui.get("/api/state")
    s, home = res["status"], state["pipelines"][0]
    assert s["status"] == "ok" == home["status"] and s["summary"] == home["summary"] == "1 updated"
    assert s["last_sync_ts"] == home["last_sync_ts"] and s["owned_files"] == 10 and s["paused"] is False
    assert s["last_run"]["trigger"] == "mcp" and s["last_run"]["result"] == "ok" and s["last_run"]["counts"]["updated"] == 1


def test_a_never_synced_and_a_paused_pipeline(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    with running_ui(brain) as ui:
        never = detail(ui)
        set_paused(brain, "p", True, trigger="cli")
        paused = detail(ui)
    assert never["status"]["status"] == "never" and never["status"]["last_sync_ts"] is None and never["runs"] == [] and never["needs_you"] == []
    assert paused["status"]["status"] == "paused" and paused["status"]["paused"] is True


def test_github_status_has_the_last_commit_link_and_the_clone_status(brain, add_pipeline, make_source, tmp_path, monkeypatch):
    from mdpipeline.targetconfig import GitHubSpec

    monkeypatch.setenv("BRAIN_TOKEN", "ghp_STATUSSECRET0123456789")
    fake = FakeGitHub(repo="acme/brain", branch="main", token="ghp_STATUSSECRET0123456789", files={"wiki/w.md": b"W"})
    github_api.set_transport(fake)
    try:
        dest = GitHubSpec("acme/brain", "env:BRAIN_TOKEN", "main").to_dict()
        add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))], destination=dest))
        sync_pipeline(brain, "p")
        with running_ui(brain) as ui:
            res = detail(ui)
    finally:
        github_api.set_transport(None)
    target = res["status"]["destination"]
    assert target["type"] == "github" and len(target["last_commit"]["sha"]) >= 7 and target["last_commit"]["url"].startswith("https://github.com/acme/brain/commit/")
    assert "ghp_STATUSSECRET" not in str(res)


def test_a_failed_sync_shows_its_error_card_only_for_this_pipeline(brain, add_pipeline, make_source, tmp_path):
    a = make_source("a", {"1.md": "1", "2.md": "2"})
    b = make_source("b", {"3.md": "3"})
    add_pipeline(Pipeline.new("bad", [Source(str(a), "a")]))
    add_pipeline(Pipeline.new("fine", [Source(str(b), "b")]))
    sync_pipeline(brain, "bad")
    sync_pipeline(brain, "fine")
    a.rename(tmp_path / "moved")
    sync_pipeline(brain, "bad", trigger="mcp")
    with running_ui(brain) as ui:
        bad, fine = detail(ui, "bad"), detail(ui, "fine")
    assert [c["kind"] for c in bad["needs_you"]] == ["source_missing"] and bad["needs_you"][0]["pipeline"] == "bad"
    assert bad["status"]["status"] == "error" and bad["status"]["summary"]
    assert fine["needs_you"] == [] and all(r["pipeline"] == "fine" for r in fine["runs"])


def test_a_guard_approval_and_conflicts_are_listed_with_what_they_need(ten):
    brain, src = ten
    for i in range(6):
        (src / f"n{i}.md").unlink()
    sync_pipeline(brain, "p", trigger="mcp")
    with running_ui(brain) as ui:
        res = detail(ui)
    [card] = res["needs_you"]
    assert card["kind"] == "guard" and card["count"] == 6 and card["total"] == 10 and res["status"]["status"] == "blocked"
    assert res["status"]["pending_approval"] is True


def test_conflicts_card(ten):
    brain, src = ten
    (brain.root / "raw/p/docs/n1.md").write_text("my edit")
    (src / "n1.md").write_text("src changed")
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        res = detail(ui)
    [card] = res["needs_you"]
    assert card["kind"] == "conflicts" and card["items"][0]["path"] == "raw/p/docs/n1.md" and res["status"]["status"] == "conflicts"


def test_runs_are_this_pipelines_last_ten_newest_first_with_file_details(ten):
    brain, src = ten
    for i in range(12):
        (src / "n0.md").write_text(f"v{i}")
        sync_pipeline(brain, "p", trigger="cli" if i % 2 else "mcp")
    set_paused(brain, "p", True, trigger="ui")                  # an event, not a run
    with running_ui(brain) as ui:
        runs = detail(ui)["runs"]
    assert len(runs) == 10 and all(r["kind"] == "run" and r["pipeline"] == "p" for r in runs)
    assert runs[0]["counts"]["updated"] == 1 and runs[0]["trigger"] == "cli" and isinstance(runs[0]["duration_ms"], int) and runs[0]["ts"]
    assert runs[0]["details"] == [{"op": "update", "target": "raw/p/docs/n0.md", "note": ""}]
    assert [r["id"] for r in runs] == sorted((r["id"] for r in runs), reverse=True)


def test_building_the_page_writes_nothing_even_with_a_pending_guard(ten, app_data):
    brain, src = ten
    for i in range(6):
        (src / f"n{i}.md").unlink()
    sync_pipeline(brain, "p", trigger="mcp")
    before = tree_snapshot(brain.root, src)
    with running_ui(brain) as ui:
        for _ in range(2):
            detail(ui)
    assert tree_snapshot(brain.root, src) == before


def test_the_activity_view_can_be_filtered_to_one_pipeline(brain, add_pipeline, make_source):
    for name in ("one", "two"):
        add_pipeline(Pipeline.new(name, [str(make_source(name, {"1.md": "1"}))]))
        sync_pipeline(brain, name)
    with running_ui(brain) as ui:
        status, res = ui.get("/api/activity?pipeline=one")
        _, everything = ui.get("/api/activity")
    assert status == 200 and {h["pipeline"] for h in res["history"]} == {"one"}
    assert {h["pipeline"] for h in everything["history"]} == {"one", "two"}
