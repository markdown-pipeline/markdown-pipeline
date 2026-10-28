"""MD-103 (CLI): switch a source Off and On by its label; list and show display the state; back On shows the plan."""
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source


@pytest.fixture
def world(brain, add_pipeline, make_source, monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    docs = make_source("docs", {f"d{i}.md": f"doc {i}" for i in range(6)})
    add_pipeline(Pipeline("p", [Source(str(docs), "docs"), jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    yield type("W", (), {"brain": brain, "docs": docs, "fake": f})
    jira_api.set_transport(None)


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def enabled(brain):
    return [s.enabled for s in load_config(brain).get("p").sources]


def test_a_source_is_switched_off_and_on_by_its_label_and_keeps_its_setup(world, capsys):
    before = load_config(world.brain).get("p").sources[1].options
    code, out, err = run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "md-jira")
    assert code == 0, err
    assert enabled(world.brain) == [True, False] and load_config(world.brain).get("p").sources[1].options == before
    assert "md-jira" in out and "off" in out.lower() and "kept" in out.lower()
    code, out, err = run(capsys, world.brain, "pipeline", "edit", "p", "--source-on", "md-jira")
    assert code == 0 and enabled(world.brain) == [True, True]


def test_switching_off_the_last_source_that_is_on_is_refused(world, capsys):
    run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "docs")
    before = world.brain.config_path.read_text()
    code, out, err = run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "md-jira")
    assert code == 2 and "Turn a source on, or pause the pipeline." in out + err and world.brain.config_path.read_text() == before


def test_an_unknown_label_is_a_usage_error(world, capsys):
    code, out, err = run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "nope")
    assert code == 2 and "no source labelled 'nope'" in out + err


def test_off_and_on_in_the_same_command_for_one_label_is_a_usage_error(world, capsys):
    code, out, err = run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "docs", "--source-on", "docs")
    assert code == 2


def test_back_on_shows_the_plan_with_what_changed_and_writes_no_file(world, capsys):
    run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "docs")
    (world.docs / "d5.md").unlink()
    (world.docs / "new.md").write_text("new")
    owned = sorted(State.load(world.brain).pipeline("p").files)
    code, out, err = run(capsys, world.brain, "pipeline", "edit", "p", "--source-on", "docs")
    assert code == 0, err
    assert "Next sync would" in out and "deleted 1" in out and "created 1" in out
    assert sorted(State.load(world.brain).pipeline("p").files) == owned and (world.brain.root / "raw/p/docs/d5.md").exists()


def test_dry_run_with_source_on_shows_the_plan_and_saves_nothing(world, capsys):
    run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "docs")
    (world.docs / "d5.md").unlink()
    code, out, err = run(capsys, world.brain, "pipeline", "edit", "p", "--source-on", "docs", "--dry-run")
    assert code == 0 and "deleted 1" in out and "Nothing was changed" in out and enabled(world.brain) == [False, True]


def test_list_show_and_json_display_each_sources_state(world, capsys):
    run(capsys, world.brain, "pipeline", "edit", "p", "--source-off", "docs")
    code, out, _ = run(capsys, world.brain, "pipeline", "list")
    assert "docs (folder" in out and "off" in out.lower()
    rows = json.loads(run(capsys, world.brain, "pipeline", "list", "--json")[1])
    assert [(s["label"], s["enabled"]) for s in rows[0]["sources"]] == [("docs", False), ("md-jira", True)]
    code, out, _ = run(capsys, world.brain, "pipeline", "show", "p")
    assert "OFF" in out
    data = json.loads(run(capsys, world.brain, "pipeline", "show", "p", "--json")[1])
    assert data["schema"] == 1 and [s["enabled"] for s in data["sources"]] == [False, True]
