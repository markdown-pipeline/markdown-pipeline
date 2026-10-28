"""MD-46..52: the Jira source on the source contract, against the fake Jira (no real network)."""
import pytest

from fake_jira import FakeJira, adf_doc, adf_p
from jira_fixtures import populate
from mdpipeline import jira_api
from mdpipeline.config import Source
from mdpipeline.jira_render import RENDER_VERSION
from mdpipeline.jira_source import JiraSource, clean_title
from mdpipeline.sources import adapter_for

EMAIL, TOKEN = "me@example.com", "ATATT-SECRET-TOKEN"
SITE = "https://acme.atlassian.net"


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def source(jql="project = MD", label="jira"):
    return Source(SITE, label, type="jira", options={"site": SITE, "jql": jql, "email_ref": "env:JIRA_EMAIL", "token_ref": "env:JIRA_TOKEN"})


def test_the_registry_builds_a_jira_adapter(fake):
    assert isinstance(adapter_for(source()), JiraSource)


def test_scan_lists_every_issue_with_identity_display_path_dates_and_version(fake):
    fake.add_issue("MD", "First issue")
    fake.add_issue("MD", "Second/with slash")
    scan = adapter_for(source()).scan(["**/*.md"], [])
    assert scan.status == "ok" and scan.kind == "jira" and scan.guard_empty and scan.path == SITE
    a, b = scan.entries
    assert a.identity == fake.issue_id("MD-1") and a.relpath == "MD/MD-1 First issue.md"
    assert b.relpath == "MD/MD-2 Second with slash.md"
    assert a.created and a.mtime >= a.created and a.version.endswith(f"|r{RENDER_VERSION}")
    assert a.provenance == {"site": SITE, "issue_id": a.identity, "issue_key": "MD-1", "issue_url": f"{SITE}/browse/MD-1", "source_updated": a.provenance["source_updated"]}


def test_scan_fetches_no_issue_content_until_asked(fake):
    fake.add_issue("MD", "One")
    entry = adapter_for(source()).scan([], []).entries[0]
    assert [p for _, p in fake.requests if "/issue/" in p] == []
    text = entry.read_bytes().decode()
    assert "# MD-1: One" in text and [p for _, p in fake.requests if "/issue/" in p]


def test_content_has_the_rendered_issue_and_counts_conversion_warnings(fake):
    keys = populate(fake)
    scan = adapter_for(source("project = MD")).scan([], [])
    entry = next(e for e in scan.entries if e.provenance["issue_key"] == keys["unknown"])
    assert "unsupported" in entry.read_bytes().decode() and scan.warnings == 1


def test_a_failing_listing_makes_the_source_unreadable_with_a_category_and_no_entries(fake):
    fake.add_issue("MD", "One")
    for status, category in ((401, "auth"), (403, "permission"), (503, "network")):
        fake.fail("GET", "/search/jql", status=status, times=1)
        scan = adapter_for(source()).scan([], [])
        assert scan.status == "unreadable" and scan.category == category and scan.entries == []


def test_a_page_failure_in_the_middle_makes_the_whole_source_unreadable(fake):
    for n in range(3):
        fake.add_issue("MD", f"i{n}")
    adapter = adapter_for(source())
    adapter.page_size = 1
    fake.fail_page(2)
    scan = adapter.scan([], [])
    assert scan.status == "unreadable" and scan.entries == [] and scan.category == "network"


def test_a_missing_credential_is_an_unreadable_source_not_an_empty_one(fake, monkeypatch):
    fake.add_issue("MD", "One")
    monkeypatch.delenv("JIRA_TOKEN")
    scan = adapter_for(source()).scan([], [])
    assert scan.status == "unreadable" and "JIRA_TOKEN" in scan.error and scan.category == "auth" and scan.entries == []


def test_zero_matches_is_a_successful_empty_scan(fake):
    scan = adapter_for(source("project = NOPE")).scan([], [])
    assert scan.status == "ok" and scan.entries == [] and scan.guard_empty


def test_test_connection_reports_the_user_and_the_match_count(fake):
    fake.add_issue("MD", "One")
    fake.add_issue("MD", "Two")
    assert adapter_for(source()).test_connection() == {"user": "Mia Owner", "matches": 2, "site": SITE}


@pytest.mark.parametrize("title,expected", [("a/b\\c", "a b c"), ("  spaced   out  ", "spaced out"), ("", "untitled"), ("x" * 300, "x" * 120),
                                            ("tab\tand\nnewline", "tab and newline"), ("...", "untitled")])
def test_clean_title(title, expected):
    assert clean_title(title) == expected
