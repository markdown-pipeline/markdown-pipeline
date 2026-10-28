"""MD-135: the first panel of view mode says in one plain sentence what the pipeline does. Generated from the configuration only; no secrets."""
import os

import pytest

from mdpipeline.config import Pipeline, Source
from mdpipeline.pipelinesummary import summary_sentence
from uihelper import running_ui


def folder(path="/data/notes", label="notes"):
    return Source(path, label)


def jira(site="https://acme.atlassian.net", jql="project = ACME", label="jira"):
    return Source(site, label, type="jira", options={"site": site, "jql": jql, "email_ref": "vault:abc", "token_ref": "vault:def"})


def text(brain, p):
    return summary_sentence(brain, p)


def test_a_folder_to_the_brain_directory_under_raw(brain):
    p = Pipeline.new("p", [folder()], target="raw/{relpath}")
    assert text(brain, p) == f"Turns /data/notes into Markdown files in {brain.root}, under raw/. Checks for changes every hour."


def test_the_home_folder_is_shown_as_a_tilde(brain, monkeypatch):
    monkeypatch.setenv("HOME", "/home/someone")
    p = Pipeline.new("p", [folder("/home/someone/biz/notes")], target="raw/{relpath}")
    assert "Turns ~/biz/notes into Markdown files in" in text(brain, p)


def test_an_empty_template_means_the_root_of_the_output_folder(brain):
    p = Pipeline.new("p", [folder()], target="")
    assert "at the root of the output folder" in text(brain, p) and "under" not in text(brain, p)


def test_a_longer_template_is_shown_as_the_pattern(brain):
    p = Pipeline.new("p", [folder()], target="raw/{pipeline}/{source}/{relpath}")
    assert "under raw/{pipeline}/{source}/" in text(brain, p)


def test_a_github_target_names_the_repository_and_branch(brain):
    p = Pipeline.new("p", [folder()], target="raw/{relpath}", destination={"type": "github", "repo": "someone-else/team-brain", "branch": "main", "credential": "vault:x"})
    assert "in GitHub someone-else/team-brain (main), under raw/." in text(brain, p)


def test_a_jira_source_shows_the_site_and_the_project_in_short_form(brain):
    p = Pipeline.new("p", [jira()], target="raw/{relpath}")
    assert "Jira acme.atlassian.net (project ACME) into Markdown files in" in text(brain, p)


def test_a_jira_jql_that_is_not_a_plain_project_is_shown_shortened(brain):
    jql = "project = ACME AND updated >= -30d AND status != Done ORDER BY updated DESC, key ASC, priority DESC"
    out = text(brain, Pipeline.new("p", [jira(jql=jql)], target="raw/{relpath}"))
    assert "JQL: project = ACME AND updated >= -30d" in out and "…" in out and len(out) < 330


def test_several_sources_are_listed_then_counted(brain):
    p = Pipeline.new("p", [folder("/a", "a"), folder("/b", "b"), jira(), folder("/c", "c")], target="raw/{relpath}")
    out = text(brain, p)
    assert "Turns /a, /b and 2 more into Markdown files in" in out


def test_a_source_switched_off_is_not_listed(brain):
    off = folder("/off", "off")
    off.enabled = False
    out = text(brain, Pipeline.new("p", [folder("/a", "a"), off], target="raw/{relpath}"))
    assert "/off" not in out and "Turns /a into" in out


@pytest.mark.parametrize("schedule,words", [("15m", "Checks for changes every 15 minutes."), ("1h", "Checks for changes every hour."), ("6h", "Checks for changes every 6 hours."),
                                            ("daily", "Checks for changes once a day."),
                                            ("off", "Only when you sync it.")])
def test_the_schedule_in_words(brain, schedule, words):
    p = Pipeline.new("p", [folder()], target="raw/{relpath}")
    p.schedule = schedule
    assert text(brain, p).endswith(words)


def test_a_paused_pipeline_says_it_is_paused(brain):
    p = Pipeline.new("p", [folder()], target="raw/{relpath}")
    p.paused = True
    assert text(brain, p).startswith("Paused. ") and "Turns /data/notes into Markdown files" in text(brain, p)


def test_active_filters_are_named(brain):
    p = Pipeline.new("p", [folder()], target="raw/{relpath}", include=["docs/**/*.md"], exclude=["drafts/**"])
    out = text(brain, p)
    assert "Only files matching docs/**/*.md." in out and "Skips drafts/**." in out


def test_the_default_filters_are_not_mentioned(brain):
    assert "Only files" not in text(brain, Pipeline.new("p", [folder()], target="raw/{relpath}"))


def test_no_secret_or_reference_ever_appears(brain):
    out = text(brain, Pipeline.new("p", [jira()], target="raw/{relpath}", destination={"type": "github", "repo": "o/r", "branch": "main", "credential": "vault:SECRET1", "auth": "token"}))
    assert "vault:" not in out and "SECRET1" not in out and "abc" not in out


def test_the_view_page_carries_the_sentence_and_show_json_schema_is_unchanged(brain, add_pipeline, make_source):
    src = make_source("a", {"1.md": "x"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "a")], target="raw/{relpath}"))
    with running_ui(brain) as ui:
        _, view = ui.get("/api/pipelines/p/view")
    assert view["summary"].startswith("Turns ") and "summary" not in view["config"]


def test_one_verb_fits_a_jira_source_and_a_folder_source(brain):
    folder_text = text(brain, Pipeline.new("p", [folder()], target="raw/{relpath}"))
    jira_text = text(brain, Pipeline.new("p", [jira()], target="raw/{relpath}"))
    assert folder_text.startswith("Turns /data/notes into Markdown files in ")
    assert jira_text.startswith("Turns ") and " into Markdown files in " in jira_text and "Copies" not in jira_text
