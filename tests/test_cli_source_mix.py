"""MD-100: the CLI creates and edits a pipeline with any mix of sources (Jira only included) and removes any source by label."""
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
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


JIRA = ["--jira-label", "md-jira", "--jira-site", SITE, "--jira-jql", "project = MD", "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN"]


@pytest.fixture
def mixed(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(6)})
    add_pipeline(Pipeline("p", [Source(str(docs), "docs"), jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=100))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    return brain


def owned(brain):
    return sorted(State.load(brain).pipeline("p").files)


def test_the_last_folder_can_be_removed_while_a_jira_source_remains(mixed, capsys):
    n = len(owned(mixed))
    code, out, err = run(capsys, mixed, "pipeline", "edit", "p", "--remove-source", "docs")
    assert code == 0, err
    assert [s.label for s in load_config(mixed).get("p").sources] == ["md-jira"]
    assert len(list((mixed.root / "raw/p/docs").glob("*.md"))) == 6 and "6 files kept" in out
    assert len(owned(mixed)) == n - 6 and not any("/docs/" in t for t in owned(mixed))


def test_a_jira_source_can_be_removed_by_its_label_and_its_files_are_kept_unowned(mixed, capsys):
    code, out, err = run(capsys, mixed, "pipeline", "edit", "p", "--remove-source", "md-jira")
    assert code == 0, err
    assert [s.label for s in load_config(mixed).get("p").sources] == ["docs"]
    assert len(list((mixed.root / "raw/p/md-jira").rglob("*.md"))) == 3 and "3 files kept" in out
    assert not any("/md-jira/" in t for t in owned(mixed))


def test_the_only_source_cannot_be_removed(brain, add_pipeline, make_source, capsys):
    add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
    before = brain.config_path.read_text()
    code, out, err = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "docs")
    assert code == 2 and "at least one source" in out + err and brain.config_path.read_text() == before


def test_switching_a_folder_pipeline_to_jira_only_in_one_command(brain, fake, add_pipeline, make_source, capsys):
    add_pipeline(Pipeline.new("p", [str(make_source("docs", {f"d{i}.md": str(i) for i in range(6)}))], name_policy="original"))
    sync_pipeline(brain, "p", trigger="cli")
    code, out, err = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "docs", *JIRA)
    assert code == 0, err
    assert [s.type for s in load_config(brain).get("p").sources] == ["jira"]
    assert len(list((brain.root / "raw/p/docs").glob("*.md"))) == 6


def test_a_failing_replacement_removes_nothing(brain, fake, add_pipeline, make_source, capsys, monkeypatch):
    add_pipeline(Pipeline.new("p", [str(make_source("docs", {f"d{i}.md": str(i) for i in range(6)}))], name_policy="original"))
    sync_pipeline(brain, "p", trigger="cli")
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    before = brain.config_path.read_text()
    code, out, err = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "docs", "--delete-files", *JIRA)
    assert code == 2 and brain.config_path.read_text() == before and len(owned(brain)) == 6
    assert len(list((brain.root / "raw/p/docs").glob("*.md"))) == 6


def test_dry_run_of_a_removal_shows_what_happens_and_changes_nothing(mixed, capsys):
    before, state = mixed.config_path.read_text(), owned(mixed)
    code, out, err = run(capsys, mixed, "pipeline", "edit", "p", "--remove-source", "docs", "--dry-run")
    assert code == 0, err
    assert "6 files" in out and "kept" in out and "unowned" in out and "Nothing was changed" in out
    assert mixed.config_path.read_text() == before and owned(mixed) == state
    code, out, err = run(capsys, mixed, "pipeline", "edit", "p", "--remove-source", "docs", "--delete-files", "--dry-run")
    assert "deleted" in out and "Nothing was changed" in out and owned(mixed) == state


def test_the_guard_applies_to_a_removal_with_delete_files(brain, add_pipeline, make_source, fake, capsys):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(8)})
    add_pipeline(Pipeline("p", [Source(str(docs), "docs"), jira_source(label="md-jira")], name_policy="original"))
    sync_pipeline(brain, "p", trigger="cli")
    code, out, err = run(capsys, brain, "pipeline", "edit", "p", "--remove-source", "docs", "--delete-files")
    assert code == 3 and "--approve" in out + err and len(list((brain.root / "raw/p/docs").glob("*.md"))) == 8


def test_list_and_show_name_each_source_with_its_type_and_label(mixed, capsys):
    code, out, _ = run(capsys, mixed, "pipeline", "list")
    assert code == 0 and "docs (folder" in out and "md-jira (Jira" in out
    code, out, _ = run(capsys, mixed, "pipeline", "list", "--json")
    rows = json.loads(out)
    assert [(s["type"], s["label"]) for s in rows[0]["sources"]] == [("folder", "docs"), ("jira", "md-jira")]
    code, out, _ = run(capsys, mixed, "pipeline", "show", "p", "--json")
    data = json.loads(out)
    assert data["schema"] == 1 and [(s["type"], s["label"]) for s in data["sources"]] == [("folder", "docs"), ("jira", "md-jira")]
    assert TOKEN not in out and EMAIL not in out


def test_adding_with_no_source_at_all_is_a_usage_error_and_saves_nothing(brain, capsys):
    code, out, err = run(capsys, brain, "pipeline", "add", "p")
    assert code == 2 and "at least one source" in (out + err).lower() and load_config(brain).get("p") is None
