"""MD-148: every path that CREATES a pipeline saves the owner's default file-name policy (date-name) explicitly.

Pipelines saved before the decision keep their names: nothing is renamed after an upgrade.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, naming, uieditor
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.naming import DEFAULT_NAME_POLICY, SEPARATOR
from test_jira_sync import EMAIL, TOKEN
from uihelper import running_ui

TODAY = datetime.now(timezone.utc).date().isoformat()


@pytest.fixture
def notes(make_source):
    return make_source("team-wiki", {f"n{i}.md": f"# Note {i}\nbody {i}\n" for i in range(7)})


def saved_policy(brain, name):
    return load_config(brain).get(name).name_policy


def test_a_draft_without_a_policy_gets_the_one_shared_default():
    assert uieditor.draft_to_pipeline({"name": "p", "sources": [{"path": "/x", "label": "x"}]}).name_policy == DEFAULT_NAME_POLICY == "date-name"


def test_no_string_literal_falls_back_to_original_for_new_creates():
    source = (Path(uieditor.__file__)).read_text()
    assert 'or "original"' not in source


def test_first_run_folder_review_shows_the_real_resulting_names(brain, notes, tmp_path):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/review", {"source": "folder", "path": str(notes), "dest": str(tmp_path / "out")})
    assert res["paths"] == [f"raw/{TODAY}{SEPARATOR}n{i}.md" for i in range(5)]


def test_first_run_folder_saves_date_name_explicitly_and_writes_those_names(brain, notes, tmp_path):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/write", {"source": "folder", "path": str(notes), "dest": str(dest)})
    assert res["ok"] is True
    assert saved_policy(brain, "team-wiki") == DEFAULT_NAME_POLICY
    assert "name_policy: date-name" in brain.config_path.read_text()
    assert (dest / "raw" / f"{TODAY}{SEPARATOR}n0.md").exists() and not (dest / "raw" / "n0.md").exists()


def test_first_run_sample_saves_date_name(brain, app_data, tmp_path):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/write", {"source": "sample", "dest": str(tmp_path / "sample-out")})
    assert res["ok"] is True and saved_policy(brain, "sample") == DEFAULT_NAME_POLICY
    assert all(re.match(r"\d{4}-\d\d-\d\d" + re.escape(SEPARATOR), p.name) for p in (tmp_path / "sample-out").rglob("*.md"))


def test_first_run_jira_saves_date_name(brain, tmp_path):
    fake = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    fake.add_issue("ACME", "Issue number 0")
    jira_api.set_transport(fake, sleep=lambda s: None)
    try:
        with running_ui(brain) as ui:
            _, res = ui.post("/api/first-run/write", {"source": "jira", "site": "https://acme.atlassian.net", "email": EMAIL, "token": TOKEN,
                                                     "dest": str(tmp_path / "jira-out")})
    finally:
        jira_api.set_transport(None)
    assert res["ok"] is True and saved_policy(brain, "acme-jira") == DEFAULT_NAME_POLICY


def test_the_advanced_editor_api_create_without_a_policy_saves_date_name(brain, notes):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": {"name": "adv", "sources": [{"path": str(notes), "label": "notes"}]}})
    assert status in (200, 201), res
    assert saved_policy(brain, "adv") == DEFAULT_NAME_POLICY


def test_cli_pipeline_add_without_a_flag_saves_date_name(brain, notes, capsys):
    code = main(["--brain", str(brain.root), "pipeline", "add", "cli", "--source", str(notes)])
    capsys.readouterr()
    assert code == 0 and saved_policy(brain, "cli") == DEFAULT_NAME_POLICY


def test_all_creation_paths_share_the_naming_constant():
    assert naming.DEFAULT_NAME_POLICY == uieditor.draft_to_pipeline({}).name_policy


# existing pipelines are never renamed ------------------------------------------------------------------------------------

def test_a_config_saved_without_a_policy_still_loads_as_original(brain, notes):
    brain.config_path.write_text(
        "pipelines:\n- name: old\n  sources:\n  - path: " + str(notes) + "\n    label: team-wiki\n", encoding="utf-8")
    assert saved_policy(brain, "old") == "original"


def test_editing_an_original_pipeline_through_a_draft_without_a_policy_keeps_original_and_renames_nothing(brain, add_pipeline, notes):
    add_pipeline(Pipeline.new("old", [str(notes)], target="raw/{relpath}", name_policy="original"))
    sync_pipeline(brain, "old")
    before = sorted(p.name for p in (brain.root / "raw").rglob("*.md"))
    draft = {"name": "old", "sources": [{"path": str(notes), "label": "team-wiki"}], "target": "raw/{relpath}"}      # no name_policy key
    with running_ui(brain) as ui:
        _, preview = ui.post("/api/preview", {"draft": draft, "editing": "old"})
        counts = {k: v for k, v in preview["preview"]["counts"].items() if v}
        status, _ = ui.put("/api/pipelines/old", {"draft": draft})
    assert status == 200
    assert saved_policy(brain, "old") == "original"
    assert "rename" not in counts and "update" not in counts
    assert sorted(p.name for p in (brain.root / "raw").rglob("*.md")) == before


def test_the_readme_says_every_new_pipeline_starts_with_date_name():
    readme = (Path(__file__).parent.parent / "docs" / "reference.md").read_text()
    assert "every new pipeline starts with `date-name`" in readme and "gets `original`" not in readme
