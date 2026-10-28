"""MD-150: the first-run "Test and continue" asks real Jira Cloud only questions it accepts, and never turns a refused query into
"this account can't see any Jira project"."""
import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, usage_events, usage_hooks
from mdpipeline.jira_api import JiraApi
from mdpipeline.sources import SourceError
from test_jira_sync import EMAIL, TOKEN
from uihelper import running_ui


def body(**kw):
    return {"site": "acme", "email": EMAIL, "token": TOKEN, **kw}


@pytest.fixture
def site():
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_project("ACME", "Acme Platform")
    f.add_issue("ACME", "An issue")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


def test_the_fake_rejects_the_relative_date_real_jira_rejects_and_accepts_the_one_it_accepts(site):
    api = JiraApi("https://acme.atlassian.net", EMAIL, TOKEN)
    with pytest.raises(SourceError) as exc:
        api.count("updated >= -36500d")
    assert exc.value.category == "bad-query" and "Date value '-36500d' for field 'updated' is invalid" in str(exc.value)
    assert api.count("updated >= -3650d") == 1


def test_an_account_that_can_see_a_project_passes_without_any_issue_query(brain, site):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", body())
    assert res["ok"] is True and res["user"] == "Mia Owner"
    assert not [r for r in site.requests if "approximate-count" in r[1] or "search/jql" in r[1]]


def test_an_account_with_no_visible_project_still_says_so(brain):
    empty = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    jira_api.set_transport(empty, sleep=lambda s: None)
    try:
        with running_ui(brain) as ui:
            _, res = ui.post("/api/first-run/jira-test", body())
    finally:
        jira_api.set_transport(None)
    assert res["ok"] is False and res["kind"] == "no_projects" and "Mia Owner" in res["headline"]


def test_a_refused_request_during_the_check_is_not_reported_as_no_access(brain, site):
    site.fail("GET", "/project/search", status=400, message="something Jira did not like")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", body())
    assert res["ok"] is False and res["kind"] == "other"
    assert "couldn't check this Jira connection" in res["headline"] and "can't see any" not in res["headline"]
    assert "something Jira did not like" not in str(res)          # fixed words only, never the server's text


def test_that_failure_is_reported_in_usage_data_as_its_own_class():
    assert "probe_rejected" in usage_events.ERROR_CLASS.values
    assert usage_hooks.error_class_of("probe-rejected") == "probe_rejected"
    assert usage_hooks.error_class_of("bad-query") == "validation"          # what a person typed is still a validation error



def test_a_token_that_may_not_list_projects_still_passes_so_the_picker_can_explain_it(brain, site):
    site.fail("GET", "/project/search", status=403, times=9)          # a scoped token without the project scope (MD-141 calm notice)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/jira-test", body())
    assert res["ok"] is True and res["user"] == "Mia Owner"
