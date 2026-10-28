"""MD-120 AC3: the suggested first question is built only from items that exist, and must have something to answer from.

When the specific question would come back empty, the app shows a question that always works on synced files, with the reason.
"""
import pytest

from mdpipeline import agents
from mdpipeline.config import Pipeline


def jira_file(folder, key, category, summary="Something"):
    path = folder / "ACME" / f"{key} {summary}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\njira:\n  key: {key}\n  status: x\n  status_category: {category}\n---\n# {key}: {summary}\n")
    return path


@pytest.fixture
def jira_pipeline(brain, add_pipeline, tmp_path):
    out = tmp_path / "out" / "acme-jira"
    out.mkdir(parents=True)
    p = Pipeline.new("acme-jira", [])
    p.destination = {"type": "directory", "path": str(out)}
    add_pipeline(p)
    return out


def test_a_jira_pipeline_with_issues_in_progress_asks_about_the_largest_project(brain, jira_pipeline):
    jira_file(jira_pipeline, "ACME-1", "In Progress")
    jira_file(jira_pipeline, "ACME-2", "To Do")
    q = agents.suggest_question(brain, "acme-jira")
    assert q["question"] == "What is in progress in ACME right now, and what is blocked?"
    assert q["fallback"] is False and q["reason"] == "" and q["evidence"] == 1


def test_the_largest_project_wins(brain, jira_pipeline):
    jira_file(jira_pipeline, "ACME-1", "In Progress")
    for n in range(1, 4):
        path = jira_pipeline / "WEB" / f"WEB-{n}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"---\njira:\n  key: WEB-{n}\n  status_category: In Progress\n---\n# WEB-{n}\n")
    assert "WEB" in agents.suggest_question(brain, "acme-jira")["question"]


def test_when_nothing_is_in_progress_the_question_that_always_works_is_shown_with_its_reason(brain, jira_pipeline):
    jira_file(jira_pipeline, "ACME-1", "To Do")
    jira_file(jira_pipeline, "ACME-2", "Done")
    q = agents.suggest_question(brain, "acme-jira")
    assert q["question"] == "Which issues were updated most recently, and what changed in them?"
    assert q["fallback"] is True
    assert q["reason"] == "No issue in ACME is in progress right now, so this question works on any synced files."
    assert q["evidence"] == 2


def test_a_folder_that_mentions_decisions_gets_the_summary_question(brain, add_pipeline, tmp_path):
    out = tmp_path / "out" / "team-wiki"
    out.mkdir(parents=True)
    (out / "a.md").write_text("# Roadmap\nOpen decision: pricing.\n")
    (out / "b.md").write_text("# Notes\n")
    p = Pipeline.new("team-wiki", [])
    p.destination = {"type": "directory", "path": str(out)}
    add_pipeline(p)
    q = agents.suggest_question(brain, "team-wiki")
    assert q["question"] == "Summarize team-wiki and list the open decisions."
    assert q["fallback"] is False and q["evidence"] == 1


def test_a_folder_without_decisions_gets_the_fallback_with_its_reason(brain, add_pipeline, tmp_path):
    out = tmp_path / "out" / "team-wiki"
    out.mkdir(parents=True)
    (out / "a.md").write_text("# Notes\nnothing special\n")
    p = Pipeline.new("team-wiki", [])
    p.destination = {"type": "directory", "path": str(out)}
    add_pipeline(p)
    q = agents.suggest_question(brain, "team-wiki")
    assert q["question"] == "Which files changed most recently, and what do they say?"
    assert q["fallback"] is True and q["reason"] == "No file in team-wiki mentions a decision, so this question works on any synced files."


def test_no_synced_files_means_no_question(brain, jira_pipeline):
    assert agents.suggest_question(brain, "acme-jira") is None


def test_an_unknown_pipeline_means_no_question(brain):
    assert agents.suggest_question(brain, "nope") is None


def test_the_copyable_command_starts_the_agent_with_the_question():
    assert agents.start_command("claude_code", "What is in progress in ACME right now?") == "claude 'What is in progress in ACME right now?'"
    assert agents.start_command("codex", "What's new?") == "codex 'What'\"'\"'s new?'"


# on a real (fake-server) Jira sync, end to end: the question the app suggests is answerable from the files the sync wrote ------------
from fake_jira import FakeJira  # noqa: E402
from mdpipeline import brainquery, jira_api  # noqa: E402
from test_jira_sync import EMAIL, TOKEN  # noqa: E402
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def jira():
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_issue("ACME", "Pay the invoice", status="In Progress", status_category="In Progress")
    f.add_issue("ACME", "Write the report")
    f.add_issue("ACME", "Review the plan", status="Done", status_category="Done")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


def test_after_a_real_jira_sync_the_specific_question_is_suggested_and_its_files_are_findable(brain, jira, tmp_path):
    with running_ui(brain) as ui:
        _, wrote = ui.post("/api/first-run/write", {"source": "jira", "site": "https://acme.atlassian.net", "email": EMAIL, "token": TOKEN,
                                                     "dest": str(tmp_path / "out")})
        _, view = ui.get(f"/api/agents?pipeline={wrote['pipeline']}")
    assert wrote["files"] == 3
    assert view["question"]["question"] == "What is in progress in ACME right now, and what is blocked?"
    assert view["question"]["fallback"] is False and view["question"]["evidence"] == 1
    assert brainquery.search(brain, "Pay the invoice")            # what the agent would search for finds the in-progress issue


def test_after_a_jira_sync_with_nothing_in_progress_the_fallback_is_suggested(brain, tmp_path):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_issue("ACME", "Write the report")
    jira_api.set_transport(f, sleep=lambda s: None)
    try:
        with running_ui(brain) as ui:
            _, wrote = ui.post("/api/first-run/write", {"source": "jira", "site": "https://acme.atlassian.net", "email": EMAIL, "token": TOKEN,
                                                         "dest": str(tmp_path / "out")})
            _, view = ui.get(f"/api/agents?pipeline={wrote['pipeline']}")
    finally:
        jira_api.set_transport(None)
    assert view["question"]["fallback"] is True and "No issue in ACME is in progress" in view["question"]["reason"]
