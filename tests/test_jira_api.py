"""MD-46/MD-47/MD-50/MD-52: the Jira Cloud client: read-only, https + configured host only, pagination, rate limits, error categories."""
import json

import pytest

from fake_jira import FakeJira, adf_doc, adf_p
from mdpipeline import jira_api
from mdpipeline.jira_api import JiraApi, urllib_transport
from mdpipeline.sources import SourceError

EMAIL, TOKEN = "me@example.com", "ATATT-SECRET-TOKEN"


@pytest.fixture
def fake():
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(5):
        f.add_issue("MD", f"issue {n}", comments=["c"] * (n * 2))
    f.add_issue("MP", "other")
    return f


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def api(fake, sleeps):
    return JiraApi("https://acme.atlassian.net", EMAIL, TOKEN, transport=fake, sleep=sleeps.append)


def test_myself_and_count(api):
    assert api.myself()["displayName"] == "Mia Owner"
    assert api.count("project = MD") == 5


def test_list_issues_follows_every_page(api, fake):
    issues = api.list_issues("project = MD", "summary,updated", page_size=2)
    assert [i["key"] for i in issues] == [f"MD-{n}" for n in range(1, 6)]
    assert [m for m, p in fake.requests if p.endswith("/search/jql")] == ["GET"] * 3


def test_one_failing_page_fails_the_whole_listing(api, fake):
    fake.fail_page(2)
    with pytest.raises(SourceError) as exc:
        api.list_issues("project = MD", "summary", page_size=2)
    assert exc.value.category == "network"


def test_issue_and_all_comment_pages(api, fake):
    assert api.issue(fake.issue_id("MD-3"))["key"] == "MD-3"
    assert len(api.comments(fake.issue_id("MD-5"), page_size=3)) == 8


def test_bad_credentials_are_auth_without_the_token_in_the_message(fake):
    bad = JiraApi("https://acme.atlassian.net", EMAIL, "ATATT-WRONG-TOKEN-VALUE", transport=fake)
    with pytest.raises(SourceError) as exc:
        bad.myself()
    assert exc.value.category == "auth" and "ATATT" not in str(exc.value) and EMAIL not in str(exc.value)


def test_invalid_jql_carries_jiras_own_text(api):
    with pytest.raises(SourceError) as exc:
        api.count("project = = =")
    assert exc.value.category == "bad-query" and "Error in the JQL Query" in str(exc.value)


def test_a_missing_issue_is_item_gone(api):
    with pytest.raises(SourceError) as exc:
        api.issue("99999")
    assert exc.value.category == "item-gone"


def test_permission_and_site_not_found_and_server_errors(api, fake):
    fake.fail("GET", "/myself", status=403, times=1)
    with pytest.raises(SourceError) as e1:
        api.myself()
    assert e1.value.category == "permission"
    fake.fail("GET", "/myself", status=404, times=1)
    with pytest.raises(SourceError) as e2:
        api.myself()
    assert e2.value.category == "site-not-found"
    fake.fail("GET", "/myself", status=503, times=1)
    with pytest.raises(SourceError) as e3:
        api.myself()
    assert e3.value.category == "network"


def test_a_transport_exception_is_a_network_error_without_request_details():
    def boom(*a):
        raise OSError("connection refused")

    with pytest.raises(SourceError) as exc:
        JiraApi("https://acme.atlassian.net", EMAIL, TOKEN, transport=boom).myself()
    assert exc.value.category == "network" and TOKEN not in str(exc.value)


def test_malformed_json_is_malformed(fake):
    def garbage(method, url, headers, body):
        return 200, {}, b"<html>not json</html>"

    with pytest.raises(SourceError) as exc:
        JiraApi("https://acme.atlassian.net", EMAIL, TOKEN, transport=garbage).myself()
    assert exc.value.category == "malformed"


def test_rate_limit_waits_for_retry_after_then_succeeds(api, fake, sleeps):
    fake.rate_limit(times=2, retry_after=7)
    assert api.myself()["displayName"] == "Mia Owner"
    assert sleeps == [7, 7]


def test_rate_limit_storm_is_bounded_and_reported(api, fake, sleeps):
    fake.rate_limit(times=99, retry_after=3)
    with pytest.raises(SourceError) as exc:
        api.myself()
    assert exc.value.category == "rate-limited" and len(sleeps) == 3


def test_retry_after_is_capped(api, fake, sleeps):
    fake.rate_limit(times=1, retry_after=100000)
    api.myself()
    assert sleeps == [60]


def test_only_read_requests_can_be_sent(api, fake):
    for method, path in (("POST", "/rest/api/3/issue"), ("PUT", "/rest/api/3/issue/1"), ("DELETE", "/rest/api/3/issue/1"),
                         ("POST", "/rest/api/3/issue/1/comment"), ("GET", "/rest/api/3/user/search"), ("POST", "/rest/api/3/search/jql")):
        with pytest.raises(ValueError):
            api.request(method, path)
    assert not [r for r in fake.requests if r[0] != "GET" and not r[1].endswith("approximate-count")]


def test_only_https_to_an_atlassian_site_host(fake):
    for url in ("http://acme.atlassian.net", "https://evil.example.com", "https://acme.atlassian.net.evil.com", "ftp://acme.atlassian.net"):
        with pytest.raises(ValueError):
            JiraApi(url, EMAIL, TOKEN, transport=fake)


def test_the_real_transport_refuses_other_hosts_before_connecting():
    for url in ("https://evil.example.com/x", "http://acme.atlassian.net/x"):
        with pytest.raises(ValueError):
            urllib_transport("GET", url, {}, None, allowed_host="acme.atlassian.net")


def test_requests_carry_basic_auth_only_in_the_header(api, fake):
    api.myself()
    assert all(TOKEN not in p for _, p in fake.requests)


# --- MD-174: a rejected token must never read as "Jira has nothing" -------------------------------------------
@pytest.fixture
def rejected(fake, sleeps):
    """The same site, but the client holds a revoked token: real Jira answers search/projects/count 200-empty and /myself 401."""
    return JiraApi("https://acme.atlassian.net", EMAIL, "ATATT-REVOKED-TOKEN-VALUE", transport=fake, sleep=sleeps.append)


def test_a_401_search_is_auth(fake, api):
    fake.fail("GET", "/search/jql", status=401, times=9)
    with pytest.raises(SourceError) as exc:
        api.list_issues("project = MD", "summary")
    assert exc.value.category == "auth"


def test_a_200_empty_search_with_a_rejected_token_is_auth_not_an_empty_list(rejected, fake):
    with pytest.raises(SourceError) as exc:
        rejected.list_issues("project = MD", "summary")
    assert exc.value.category == "auth" and "ATATT" not in str(exc.value) and "Jira rejected the email or API token" in str(exc.value)


def test_a_200_empty_project_list_with_a_rejected_token_is_auth(rejected):
    with pytest.raises(SourceError) as exc:
        rejected.projects()
    assert exc.value.category == "auth"


def test_a_200_zero_count_with_a_rejected_token_is_auth(rejected):
    with pytest.raises(SourceError) as exc:
        rejected.count("project = MD")
    assert exc.value.category == "auth"


def test_a_real_empty_result_with_valid_credentials_is_still_an_empty_list(api):
    assert api.list_issues("project = NOPE", "summary") == []
    assert api.count("project = NOPE") == 0


def test_a_normal_listing_still_works(api):
    assert len(api.list_issues("project = MD", "summary")) == 5


def test_identity_is_confirmed_once_per_client_however_many_reads(api, fake):
    api.list_issues("project = MD", "summary", page_size=2)
    api.count("project = MD")
    api.projects()
    assert [p for _, p in fake.requests].count("/rest/api/3/myself") == 1


def test_identity_is_confirmed_before_the_first_search_is_trusted(api, fake):
    api.list_issues("project = MD", "summary")
    assert fake.requests[0] == ("GET", "/rest/api/3/myself")


def test_a_rejected_identity_is_rechecked_not_cached_as_good(rejected, fake):
    for _ in range(2):
        with pytest.raises(SourceError):
            rejected.list_issues("project = MD", "summary")
    assert [p for _, p in fake.requests].count("/rest/api/3/myself") == 2 and not any(p.endswith("/search/jql") for _, p in fake.requests)
