"""MD-44: the fake Jira Cloud used by every Jira test (in-memory site: projects, issues, comments, scriptable changes and failures)."""
import base64
import json

import pytest

from fake_jira import FakeJira, adf_doc, adf_p


def call(fake, method, path, body=None, query="", user="me@example.com", token="tok"):
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    status, headers, data = fake(method, f"https://acme.atlassian.net{path}{query}", {"Authorization": f"Basic {auth}"},
                                 json.dumps(body).encode() if body is not None else None)
    return status, headers, json.loads(data) if data else None


@pytest.fixture
def fake():
    f = FakeJira(site="acme", email="me@example.com", token="tok", display_name="Mia Owner")
    f.add_issue("MD", "First issue", description=adf_doc(adf_p("hello")), comments=["a comment"])
    f.add_issue("MD", "Second issue")
    f.add_issue("MP", "Other project")
    return f


def test_bad_credentials_are_401(fake):
    assert call(fake, "GET", "/rest/api/3/myself", token="nope")[0] == 401


def test_rejected_credentials_look_anonymous_on_search_projects_and_count_like_real_jira_cloud(fake):
    """MD-174: the real site answers these three with 200-empty for a rejected token; only /myself and the issue reads answer 401."""
    assert call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20MD&maxResults=50", token="nope")[::2] == (200, {"issues": [], "isLast": True})
    assert call(fake, "GET", "/rest/api/3/project/search", token="nope")[2]["values"] == []
    assert call(fake, "POST", "/rest/api/3/search/approximate-count", {"jql": "project = MD"}, token="nope")[::2] == (200, {"count": 0})
    assert call(fake, "GET", "/rest/api/3/issue/MD-1", token="nope")[0] == 401


def test_myself_reports_the_display_name(fake):
    assert call(fake, "GET", "/rest/api/3/myself")[2]["displayName"] == "Mia Owner"


def test_search_by_project_returns_ids_keys_and_updated_with_pagination(fake):
    status, _, body = call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20MD&fields=updated&maxResults=1")
    assert status == 200 and len(body["issues"]) == 1 and body["isLast"] is False and body["nextPageToken"]
    first = body["issues"][0]
    assert first["key"] == "MD-1" and first["id"] and "updated" in first["fields"] and "summary" not in first["fields"]
    status, _, page2 = call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20MD&fields=updated&maxResults=1&nextPageToken=" + body["nextPageToken"])
    assert [i["key"] for i in page2["issues"]] == ["MD-2"] and page2["isLast"] is True


def test_search_supports_in_lists_and_rejects_invalid_jql_with_jiras_own_text(fake):
    ok = call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20in%20(MD%2C%20MP)&maxResults=50")[2]
    assert len(ok["issues"]) == 3
    status, _, bad = call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20%3D%20%3D&maxResults=50")
    assert status == 400 and "Error in the JQL Query" in bad["errorMessages"][0]


def test_approximate_count(fake):
    assert call(fake, "POST", "/rest/api/3/search/approximate-count", {"jql": "project = MD"})[2]["count"] == 2


def test_issue_has_adf_description_people_by_display_name_and_comments_are_paged(fake):
    issue = call(fake, "GET", "/rest/api/3/issue/MD-1", query="?fields=*all")[2]
    assert issue["fields"]["summary"] == "First issue" and issue["fields"]["description"]["type"] == "doc"
    assert issue["fields"]["reporter"]["displayName"] == "Mia Owner"
    for n in range(4):
        fake.add_comment("MD-1", f"c{n}")
    page = call(fake, "GET", "/rest/api/3/issue/MD-1/comment", query="?startAt=0&maxResults=2")[2]
    assert page["total"] == 5 and len(page["comments"]) == 2
    assert len(call(fake, "GET", "/rest/api/3/issue/MD-1/comment", query="?startAt=4&maxResults=2")[2]["comments"]) == 1


def test_updating_or_commenting_bumps_the_updated_marker(fake):
    before = call(fake, "GET", "/rest/api/3/issue/MD-2", query="?fields=updated")[2]["fields"]["updated"]
    fake.add_comment("MD-2", "new")
    after = call(fake, "GET", "/rest/api/3/issue/MD-2", query="?fields=updated")[2]["fields"]["updated"]
    assert after > before
    fake.update_issue("MD-2", summary="Renamed")
    assert call(fake, "GET", "/rest/api/3/issue/MD-2", query="?fields=updated")[2]["fields"]["updated"] > after


def test_deleted_issues_vanish_from_search_and_are_404(fake):
    fake.delete_issue("MD-2")
    assert [i["key"] for i in call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20MD&maxResults=50")[2]["issues"]] == ["MD-1"]
    assert call(fake, "GET", "/rest/api/3/issue/MD-2")[0] == 404


def test_moving_an_issue_changes_its_key_but_not_its_id_and_the_old_key_still_resolves(fake):
    issue_id = call(fake, "GET", "/rest/api/3/issue/MD-2")[2]["id"]
    fake.move_issue("MD-2", "MP")
    moved = call(fake, "GET", f"/rest/api/3/issue/{issue_id}")[2]
    assert moved["id"] == issue_id and moved["key"].startswith("MP-")
    assert call(fake, "GET", "/rest/api/3/issue/MD-2")[2]["id"] == issue_id
    assert "MD-2" not in [i["key"] for i in call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20MD&maxResults=50")[2]["issues"]]


def test_hidden_issues_look_deleted_to_this_user(fake):
    fake.hide("MD-1")
    assert [i["key"] for i in call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20MD&maxResults=50")[2]["issues"]] == ["MD-2"]


def test_failures_rate_limits_and_page_failures_are_scriptable(fake):
    fake.fail("GET", "/rest/api/3/myself", status=503, times=1)
    assert call(fake, "GET", "/rest/api/3/myself")[0] == 503 and call(fake, "GET", "/rest/api/3/myself")[0] == 200
    fake.rate_limit(times=2, retry_after=7)
    status, headers, _ = call(fake, "GET", "/rest/api/3/myself")
    assert status == 429 and headers["Retry-After"] == "7"
    fake.rate_limit(times=0)
    fake.fail_page(2)
    assert call(fake, "GET", "/rest/api/3/search/jql", query="?jql=project%20%3D%20MD&maxResults=1")[0] == 200


def test_only_reads_are_served_and_every_request_is_recorded(fake):
    assert call(fake, "POST", "/rest/api/3/issue", {"fields": {}})[0] in (404, 405)
    assert call(fake, "DELETE", "/rest/api/3/issue/MD-1")[0] in (404, 405)
    assert fake.write_attempts == 2
    assert fake.requests
