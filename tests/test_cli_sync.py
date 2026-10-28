"""MD-21: CLI sync (one/all, dry-run, force), status and log."""
import json

import pytest

from conftest import tree_snapshot
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.locking import brain_lock
from mdpipeline.log import ActivityLog


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def two(brain, add_pipeline, make_source):
    a = make_source("a", {"1.md": "1", "2.md": "2"})
    b = make_source("b", {"3.md": "3"})
    add_pipeline(Pipeline.new("pa", [str(a)]))
    add_pipeline(Pipeline.new("pb", [str(b)]))
    return a, b


def test_sync_one_pipeline_prints_a_summary_and_logs_trigger_cli(brain, two, capsys):
    code, out, _ = run(capsys, brain, "sync", "pa")
    assert code == 0 and "pa" in out and "created 2" in out
    assert (brain.root / "raw/pa/a/1.md").exists() and not (brain.root / "raw/pb").exists()
    assert ActivityLog(brain).read(types={"run"})[-1]["trigger"] == "cli"


def test_sync_all_runs_every_pipeline_and_reports_paused_ones(brain, two, capsys):
    cfg = load_config(brain)
    cfg.pipelines[1].paused = True
    save_config(brain, cfg)
    code, out, _ = run(capsys, brain, "sync", "--all")
    assert code == 0
    assert (brain.root / "raw/pa/a/1.md").exists() and not (brain.root / "raw/pb").exists()
    assert "pb" in out and "paused" in out


def test_sync_requires_a_pipeline_or_all(brain, two, capsys):
    code, out, err = run(capsys, brain, "sync")
    assert code == 2 and "--all" in err


def test_sync_unknown_pipeline_is_a_usage_error(brain, two, capsys):
    code, out, err = run(capsys, brain, "sync", "nope")
    assert code == 2 and "nope" in err


def test_dry_run_prints_the_plan_grouped_by_source_and_writes_nothing(brain, brain_dir, add_pipeline, make_source, capsys):
    a = make_source("a", {"1.md": "1", ".hid/x.md": "x"})
    b = make_source("b", {"2.md": "2"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "first"), Source(str(b), "second")]))
    before = tree_snapshot(brain_dir)
    code, out, _ = run(capsys, brain, "sync", "p", "--dry-run")
    assert code == 0 and tree_snapshot(brain_dir) == before
    assert out.index("first") < out.index("second")
    assert "create" in out and "raw/p/first/1.md" in out
    assert "excluded by rule" in out and "created 2" in out
    assert "dry run" in out.lower()


def test_dry_run_counts_equal_the_real_sync(brain, two, capsys):
    _, dry, _ = run(capsys, brain, "sync", "pa", "--dry-run")
    _, real, _ = run(capsys, brain, "sync", "pa")
    summary = lambda text: [l for l in text.splitlines() if l.strip().startswith("created")]  # noqa: E731
    assert summary(dry) == summary(real) and summary(real)


def test_exit_code_4_for_conflicts_and_force_overwrites_only_edited_owned_files(brain, two, capsys):
    run(capsys, brain, "sync", "pa")
    target = brain.root / "raw/pa/a/1.md"
    target.write_text("my edit")
    code, out, _ = run(capsys, brain, "sync", "pa")
    assert code == 4 and "conflict" in out and "raw/pa/a/1.md" in out
    assert target.read_text() == "my edit"
    code, out, _ = run(capsys, brain, "sync", "pa", "--force")
    assert code == 0 and "my edit" not in target.read_text()


def test_exit_code_1_for_error_result(brain, add_pipeline, tmp_path, capsys):
    add_pipeline(Pipeline.new("p", [str(tmp_path / "missing")]))
    code, out, err = run(capsys, brain, "sync", "p")
    assert code == 1 and "missing" in (out + err)


def test_exit_code_5_when_busy(brain, two, capsys):
    with brain_lock(brain):
        code, out, err = run(capsys, brain, "sync", "pa")
    assert code == 5 and "another sync is running" in (out + err)


def test_status_shows_last_run_result_counts_conflicts_guard_and_brain_age(brain, two, capsys):
    run(capsys, brain, "sync", "pa")
    (brain.root / "raw/pa/a/1.md").write_text("edit")
    run(capsys, brain, "sync", "pa")
    code, out, _ = run(capsys, brain, "status")
    assert code == 0
    for text in ("pa", "conflicts", "raw/pa/a/1.md", "guard", "output folder age", "pb", "never"):
        assert text in out.lower() or text in out


def test_status_json_and_single_pipeline(brain, two, capsys):
    run(capsys, brain, "sync", "pa")
    code, out, _ = run(capsys, brain, "status", "pa", "--json")
    data = json.loads(out)
    assert [p["name"] for p in data["pipelines"]] == ["pa"]
    assert data["pipelines"][0]["last_run"]["counts"]["created"] == 2 and data["brain_age_seconds"] < 5


def test_log_shows_recent_runs_with_a_pipeline_filter(brain, two, capsys):
    run(capsys, brain, "sync", "pa")
    run(capsys, brain, "sync", "pb")
    code, out, _ = run(capsys, brain, "log")
    assert "pa" in out and "pb" in out and "cli" in out
    _, out, _ = run(capsys, brain, "log", "--pipeline", "pb")
    assert "pb" in out and " pa " not in out
    _, out, _ = run(capsys, brain, "log", "--limit", "1")
    assert len([l for l in out.splitlines() if l.strip()]) == 1


def test_log_includes_events_like_force(brain, two, capsys):
    run(capsys, brain, "sync", "pa")
    (brain.root / "raw/pa/a/1.md").write_text("edit")
    run(capsys, brain, "sync", "pa", "--force")
    _, out, _ = run(capsys, brain, "log", "--pipeline", "pa")
    assert "force" in out


def test_sync_help_documents_the_options(capsys):
    with pytest.raises(SystemExit):
        main(["sync", "--help"])
    out = capsys.readouterr().out
    for flag in ("--dry-run", "--approve", "--force", "--all"):
        assert flag in out
