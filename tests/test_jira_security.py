"""MD-47: credentials and network stay safe: token never stored or shown, read-only Jira access, network only from the connectors."""
import json
import re
import socket
from pathlib import Path

import pytest

from fake_jira import FakeJira
from jira_fixtures import populate
from mdpipeline import jira_api
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source  # noqa: F401  (fixtures and helpers shared with the sync tests)
from test_mcp_server import payload, session
from uihelper import running_ui

SRC = Path(__file__).parent.parent / "src" / "mdpipeline"


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    populate(f)
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def secrets_in(texts):
    return [t for t in texts if TOKEN in t or EMAIL in t or "ATATT-WRONG" in t]


def test_after_a_full_life_the_token_and_email_are_nowhere(brain, fake, capsys, monkeypatch):
    outputs = []

    def cli(*argv):
        main(["--brain", str(brain.root), *argv])
        out = capsys.readouterr()
        outputs.extend([out.out, out.err])

    jira = ["--jira-label", "jira", "--jira-site", SITE, "--jira-jql", "project in (MD, MP)", "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN"]
    cli("jira", "test", *jira)
    cli("pipeline", "add", "p", *jira)
    cli("sync", "p", "--dry-run")
    cli("sync", "p")
    cli("sync", "p", "--json")
    fake.add_comment("MD-1", "a new comment")
    cli("sync", "p")
    cli("status")
    cli("status", "--json")
    cli("pipeline", "list")
    cli("pipeline", "list", "--json")
    cli("log")
    cli("log", "--json")
    fake.fail("GET", "/search/jql", status=503, times=1)
    cli("sync", "p")
    fake.rate_limit(times=99, retry_after=1)
    cli("sync", "p")
    fake.rate_limit(times=0)
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    cli("sync", "p")
    cli("jira", "test", "--pipeline", "p", "--source", "jira")
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    haystack = list(outputs)
    for path in brain.root.rglob("*"):
        if path.is_file():
            haystack.append(path.read_text(errors="replace"))
    assert [p for p in brain.root.rglob("*.md") if ".mdpipeline" not in p.parts]
    assert (brain.config_dir / "backups").is_dir()
    assert secrets_in(haystack) == []


def test_the_ui_and_the_mcp_never_return_the_token_or_email(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source("project in (MD, MP)")], guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    with running_ui(brain) as ui:
        replies = [json.dumps(ui.get(p)[1]) for p in ("/api/state", "/api/activity", "/api/version", "/api/pipelines/p")]
        replies.append(json.dumps(ui.post("/api/pipelines/p/sync")[1]))
    assert secrets_in(replies) == []

    async def fn(c):
        return json.dumps(payload(await c.call_tool("sync_status", {}))) + json.dumps(payload(await c.call_tool("sync", {"pipeline": "p"})))

    assert secrets_in([session(brain, fn)]) == []


def test_only_read_requests_were_ever_sent_to_jira(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source("project in (MD, MP)")], guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    fake.add_comment("MD-1", "x")
    sync_pipeline(brain, "p", trigger="cli")
    methods = {(m, re.sub(r"/issue/[^/]+", "/issue/{id}", p)) for m, p in fake.requests}
    assert fake.write_attempts == 0
    assert methods <= {("GET", "/rest/api/3/search/jql"), ("GET", "/rest/api/3/issue/{id}"), ("GET", "/rest/api/3/issue/{id}/comment"),
                       ("GET", "/rest/api/3/myself"), ("POST", "/rest/api/3/search/approximate-count")}


def test_the_client_source_has_no_write_method_or_endpoint():
    text = (SRC / "jira_api.py").read_text()
    used = set(re.findall(r'\b(?:GET|POST|PUT|PATCH|DELETE)\b', text.split("ALLOWED = (")[1].split(")\n\n\ndef set_transport")[0]))
    assert used == {"GET", "POST"} and text.count('("POST", re.compile') == 1 and text.count('self.request("POST"') == 1
    assert 'self.request("POST", "/rest/api/3/search/approximate-count"' in text


def test_the_connector_only_calls_the_configured_site_over_https(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source()], guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    assert fake.requests and all(p.startswith("/rest/api/3/") for _, p in fake.requests)


def test_folder_only_pipelines_still_make_zero_network_calls(brain, add_pipeline, make_source, monkeypatch):
    calls = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a))
    jira_api.set_transport(lambda *a: calls.append(a))
    try:
        add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
        assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
        assert main(["--brain", str(brain.root), "status"]) == 0
    finally:
        jira_api.set_transport(None)
    assert calls == []


def test_the_activity_log_holds_counts_keys_and_categories_never_issue_content(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source("project in (MD, MP)")], guard_threshold_percent=100))
    fake.add_issue("MD", "Sensitive", description=__import__("fake_jira").adf_doc(__import__("fake_jira").adf_p("TOP-SECRET-DESCRIPTION-TEXT")), comments=["TOP-SECRET-COMMENT-TEXT"])
    sync_pipeline(brain, "p", trigger="cli")
    fake.fail("GET", "/search/jql", status=401, times=1)
    sync_pipeline(brain, "p", trigger="cli")
    log = (brain.config_dir / "log.jsonl").read_text()
    assert "TOP-SECRET" not in log and '"category": "auth"' in log


def test_the_readme_states_the_exact_network_rule_instead_of_running_fully_offline():
    readme = (SRC.parent.parent / "docs" / "reference.md").read_text()
    assert "Runs fully offline" not in readme
    for phrase in ("only from source connectors", "https", "configured", "zero network calls"):
        assert phrase in readme
