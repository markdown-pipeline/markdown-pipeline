"""MD-236: a Jira source in public mode syncs a public site with no login, keeps the same provenance, and the deletion guard still holds."""
import pytest

from conftest import write  # noqa: F401
from fake_jira import FakeJira
from mdpipeline import jira_api
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.jira_source import JiraSource, check_jira_source
from test_jira_sync import raw_files

SITE = "https://open.atlassian.net"


@pytest.fixture
def site():
    f = FakeJira(site="open")
    f.public = True
    for n in range(3):
        f.add_issue("HHH", f"issue {n}")
    f.sleeps = []
    jira_api.set_transport(f, sleep=f.sleeps.append)
    yield f
    jira_api.set_transport(None)


def public_source(jql="project = HHH"):
    return Source(SITE, "jira", type="jira", options={"site": SITE, "jql": jql, "auth_mode": "public"})


@pytest.fixture
def run(brain, add_pipeline, site):
    add_pipeline(Pipeline("p", [public_source()], name_policy="date-name", guard_threshold_percent=100))
    return lambda **kw: sync_pipeline(brain, "p", trigger="cli", **kw)


def test_a_public_source_syncs_with_no_credentials_and_no_authorization_header(run, site, brain):
    res = run()
    assert res.result == "ok" and len(raw_files(brain)) == 3
    assert site.auth_headers and set(site.auth_headers) == {None}


def test_provenance_is_the_same_as_in_login_mode(run, brain):
    run()
    meta, _ = parse_document((brain.root / raw_files(brain)[0]).read_text())
    prov = meta["provenance"]
    assert prov["source"] == "jira" and prov["site"] == SITE and prov["issue_key"].startswith("HHH-") and prov["issue_url"].startswith(f"{SITE}/browse/")
    assert "auth_mode" not in prov and "email" not in str(prov)


def test_a_site_that_stops_answering_anonymously_never_deletes(run, site, brain):
    run()
    site.public = False                                    # now it serves an anonymous request as 200 with nothing
    res = run()
    assert res.counts()["deleted"] == 0 and len(raw_files(brain)) == 3
    assert res.result == "error" and res.errors[0]["category"] == "auth"
    assert "open.atlassian.net doesn't let anyone read these issues without a login. Log in with your Atlassian email and API token instead, or choose a public project." == res.errors[0]["error"]


def test_an_empty_answer_from_a_public_site_still_asks_the_guard(run, site, brain):
    run()
    for n in range(1, 4):
        site.delete_issue(f"HHH-{n}")
    site.add_issue("OTHER", "keeps the project list non-empty")
    res = run()
    assert res.result == "blocked" and len(raw_files(brain)) == 3


def test_a_rate_limited_public_site_deletes_nothing_and_says_try_later(run, site, brain):
    run()
    site.rate_limit(times=50, retry_after=1)
    res = run()
    assert res.counts()["deleted"] == 0 and len(raw_files(brain)) == 3
    assert res.errors[0]["category"] == "rate-limited" and "is limiting requests right now. Wait a few minutes" in res.errors[0]["error"]


def test_test_connection_in_public_mode_names_no_login_and_counts_matches(site):
    info = JiraSource(public_source()).test_connection()
    assert info == {"user": "no login (public)", "matches": 3, "site": SITE}
    assert ("GET", "/rest/api/3/myself") not in site.requests


def test_save_time_check_needs_no_credential_references_in_public_mode(site):
    found, matches = check_jira_source(public_source(), True)
    assert found == [] and matches == 3
    assert check_jira_source(public_source(), False) == ([], None)


def test_save_time_check_on_a_private_site_says_to_use_a_login(site):
    site.public = False
    found, matches = check_jira_source(public_source(), True)
    assert matches is None and found and found[0][1] == "open.atlassian.net doesn't let anyone read these issues without a login. Log in with your Atlassian email and API token instead, or choose a public project."


def test_a_source_without_auth_mode_is_login_and_the_option_is_not_added_on_save():
    s = Source(SITE, "j", type="jira", options={"site": SITE, "jql": "x", "email_ref": "env:A", "token_ref": "env:B"})
    assert "auth_mode" not in s.to_dict()["options"]
    assert Source.from_dict(public_source().to_dict()).options["auth_mode"] == "public"
