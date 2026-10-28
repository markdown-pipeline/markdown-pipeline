"""MD-11: pause and resume a pipeline."""
import pytest

from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_all, sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.service import NotFound, set_paused
from mdpipeline.status import brain_status
from test_mcp_server import payload, session


@pytest.fixture
def pq(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    add_pipeline(Pipeline.new("q", [str(make_source("b", {"2.md": "2"}))]))
    return brain


def test_pause_persists_and_resume_restores(pq):
    set_paused(pq, "p", True, trigger="cli")
    assert load_config(pq).get("p").paused is True
    set_paused(pq, "p", False, trigger="cli")
    assert load_config(pq).get("p").paused is False


def test_a_paused_pipeline_is_skipped_by_sync_all_from_every_trigger(pq):
    set_paused(pq, "p", True, trigger="cli")
    for trigger in ("cli", "mcp", "ui"):
        results = {r.pipeline: r.result for r in sync_all(pq, trigger=trigger)}
        assert results == {"p": "skipped: paused", "q": "ok"}
    assert not (pq.root / "raw/p").exists()


def test_explicit_sync_of_a_paused_pipeline_returns_paused_and_is_logged(pq):
    set_paused(pq, "p", True, trigger="cli")
    res = sync_pipeline(pq, "p", trigger="ui")
    assert res.result == "skipped: paused" and "paused" in res.message
    run = ActivityLog(pq).read(types={"run"})[-1]
    assert run["result"] == "skipped: paused" and run["trigger"] == "ui" and run["pipeline"] == "p"


def test_pause_and_resume_are_logged_with_their_trigger(pq):
    set_paused(pq, "p", True, trigger="ui")
    set_paused(pq, "p", False, trigger="cli")
    ev = [(e["action"], e["trigger"], e["pipeline"]) for e in ActivityLog(pq).read(types={"event"})]
    assert ev == [("pause", "ui", "p"), ("resume", "cli", "p")]


def test_resume_restores_normal_syncing_and_the_next_sync_runs_as_usual(pq):
    set_paused(pq, "p", True, trigger="cli")
    set_paused(pq, "p", False, trigger="cli")
    res = sync_pipeline(pq, "p")
    assert res.result == "ok" and res.counts()["created"] == 1


def test_paused_status_and_back(pq):
    set_paused(pq, "p", True, trigger="cli")
    assert next(s for s in brain_status(pq)["pipelines"] if s["name"] == "p")["status"] == "paused"
    set_paused(pq, "p", False, trigger="cli")
    assert next(s for s in brain_status(pq)["pipelines"] if s["name"] == "p")["status"] == "never"


def test_pausing_an_unknown_pipeline_fails(pq):
    with pytest.raises(NotFound):
        set_paused(pq, "nope", True, trigger="cli")


def test_pausing_twice_is_harmless_and_logs_only_a_change(pq):
    set_paused(pq, "p", True, trigger="cli")
    set_paused(pq, "p", True, trigger="cli")
    assert len([e for e in ActivityLog(pq).read(types={"event"}) if e["action"] == "pause"]) == 1


def test_cli_pause_resume_and_sync_messages(pq, capsys):
    b = str(pq.root)
    assert main(["--brain", b, "pipeline", "pause", "p"]) == 0
    assert main(["--brain", b, "sync", "p"]) == 0                 # paused is not a failure
    out = capsys.readouterr().out
    assert "paused" in out
    assert main(["--brain", b, "sync", "--all"]) == 0
    assert "p: skipped: paused" in capsys.readouterr().out
    assert main(["--brain", b, "pipeline", "resume", "p"]) == 0
    assert main(["--brain", b, "pipeline", "pause", "nope"]) == 2


def test_cli_list_shows_paused(pq, capsys):
    main(["--brain", str(pq.root), "pipeline", "pause", "p"])
    main(["--brain", str(pq.root), "pipeline", "list"])
    assert "[paused]" in capsys.readouterr().out


def test_mcp_sync_of_a_paused_pipeline(pq):
    set_paused(pq, "p", True, trigger="cli")

    async def fn(c):
        return payload(await c.call_tool("sync", {}))

    results = {r["pipeline"]: r["result"] for r in session(pq, fn)["results"]}
    assert results == {"p": "skipped: paused", "q": "ok"}
