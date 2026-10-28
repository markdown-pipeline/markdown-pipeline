"""MD-141: after the token check the person ticks projects instead of typing a key or JQL. Backend: list, build the JQL, usage event."""
from __future__ import annotations

import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, jira_picker
from mdpipeline.jira_api import JiraApi
from test_jira_sync import EMAIL, TOKEN
from test_usage_hooks import queued, sharing  # noqa: F401  (fixture)
from uihelper import running_ui


def body(**kw):
    return {"site": "acme", "email": EMAIL, "token": TOKEN, **kw}


@pytest.fixture
def make_site():
    made = []

    def make(n_projects: int, **kw) -> FakeJira:
        f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner", **kw)
        for i in range(n_projects):
            f.add_project(f"P{i:03d}" if n_projects > 26 else chr(65 + i) * 3, f"Project number {i}")
        jira_api.set_transport(f, sleep=lambda s: None)
        made.append(f)
        return f

    yield make
    jira_api.set_transport(None)


# --- the client call ---------------------------------------------------------------------------------------------------
def test_the_client_lists_projects_page_by_page_with_the_one_new_read_call(make_site):
    site = make_site(250)
    api = JiraApi("https://acme.atlassian.net", EMAIL, TOKEN)
    page = api.projects(start_at=0, max_results=100)
    assert (len(page["values"]), page["isLast"]) == (100, False)
    assert ("GET", "/rest/api/3/project/search") in site.requests and site.write_attempts == 0


# --- listing for the UI ------------------------------------------------------------------------------------------------
def test_listing_returns_name_and_key_sorted_by_name_and_pages_through_250(make_site, brain):
    make_site(250)
    with running_ui(brain) as ui:
        _, first = ui.post("/api/jira/projects", body())
        _, last = ui.post("/api/jira/projects", body(start_at=200))
    assert first["ok"] is True and len(first["projects"]) == 100 and first["is_last"] is False and first["next_start"] == 100
    assert set(first["projects"][0]) == {"key", "name"}
    assert len(last["projects"]) == 50 and last["is_last"] is True


def test_listing_can_search_by_name_or_key(make_site, brain):
    make_site(3)
    with running_ui(brain) as ui:
        _, by_key = ui.post("/api/jira/projects", body(query="bbb"))
    assert [p["key"] for p in by_key["projects"]] == ["BBB"]


def test_one_project_is_a_normal_list(make_site, brain):
    make_site(1)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/jira/projects", body())
    assert res["ok"] is True and [p["key"] for p in res["projects"]] == ["AAA"]


def test_zero_projects_is_a_calm_state_with_the_fix_and_raw_jql_kept(make_site, brain):
    make_site(0)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/jira/projects", body())
    assert res["ok"] is True and res["projects"] == [] and res["kind"] == "no_projects"
    assert "Browse projects" in res["fix"] and res["raw_jql"] is True


def test_a_403_means_a_missing_scope_and_names_it_with_the_help_page(make_site, brain):
    site = make_site(2)
    site.fail("GET", "/project/search", status=403, times=5)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/jira/projects", body())
    assert res["ok"] is False and res["kind"] == "missing_scope" and "read:jira-work" in res["fix"]
    assert res["help"] == "/jira-token-help.md" and res["raw_jql"] is True


def test_a_wrong_token_is_the_same_auth_message_as_the_token_check(make_site, brain):
    make_site(2)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/jira/projects", body(token="wrong"))
    assert (res["ok"], res["kind"]) == (False, "auth") and "wrong" not in json.dumps(res)


def test_the_token_is_never_echoed(make_site, brain):
    make_site(2)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/jira/projects", body())
    assert TOKEN not in json.dumps(res)


# --- building the JQL --------------------------------------------------------------------------------------------------
def test_one_key_builds_a_project_query_with_the_default_order():
    assert jira_picker.build_jql(["MD"]) == "project in (MD) ORDER BY updated DESC"


def test_several_keys_are_sorted_and_deduplicated():
    assert jira_picker.build_jql(["MP", "MD", "MP"]) == "project in (MD, MP) ORDER BY updated DESC"


@pytest.mark.parametrize("bad", ['MD") OR (project = "X', "MD;DROP", "md", "M D", "", "1ABC", 'A"B', "A" * 11])
def test_a_key_that_is_not_a_jira_key_is_refused_never_escaped_into_the_query(bad):
    with pytest.raises(ValueError):
        jira_picker.build_jql(["MD", bad])


def test_no_keys_is_refused():
    with pytest.raises(ValueError):
        jira_picker.build_jql([])


def test_the_build_endpoint_returns_the_query_the_plain_line_and_reports_the_pick(make_site, sharing, brain):  # noqa: F811
    with running_ui(brain) as ui:
        status, res = ui.post("/api/jira/build-jql", {"keys": ["MD", "MP"]})
    assert status == 200 and res["jql"] == "project in (MD, MP) ORDER BY updated DESC"
    assert res["line"] == "Every issue in MD and MP, newest change first."
    assert [e["properties"].get("method") for e in queued("jira_project_pick")] == ["picker"]


def test_the_build_endpoint_refuses_a_bad_key_with_400_and_reports_nothing(make_site, sharing, brain):  # noqa: F811
    with running_ui(brain) as ui:
        status, res = ui.post("/api/jira/build-jql", {"keys": ['X") OR ("a']})
    assert status == 400 and queued("jira_project_pick") == []


# --- privacy: the event carries a method and a bucket, never a key, name, site or query ---------------------------------
def test_the_event_allowlist_takes_only_method_and_count_bucket():
    from mdpipeline.usage_events import EVENTS, sanitize

    assert set(EVENTS["jira_project_pick"].props) == {"method", "count_bucket"}
    assert sanitize("jira_project_pick", {"method": "picker", "count_bucket": "1-10", "keys": ["MD"], "jql": "project = MD"}) == \
        {"method": "picker", "count_bucket": "1-10"}
    assert sanitize("jira_project_pick", {"method": "MD"}) == {}


def test_the_pick_event_carries_no_key_name_or_site(make_site, sharing, brain):  # noqa: F811
    with running_ui(brain) as ui:
        ui.post("/api/jira/build-jql", {"keys": ["SECRETKEY"], "site": "acme"})
    text = json.dumps(queued("jira_project_pick"))
    assert "SECRETKEY" not in text and "acme" not in text and "project in" not in text


def test_the_editor_can_list_with_saved_credential_references_instead_of_typed_values(make_site, brain, monkeypatch):
    make_site(2)
    monkeypatch.setenv("PICK_EMAIL", EMAIL)
    monkeypatch.setenv("PICK_TOKEN", TOKEN)
    with running_ui(brain) as ui:
        _, res = ui.post("/api/jira/projects", {"site": "acme", "email_ref": "env:PICK_EMAIL", "token_ref": "env:PICK_TOKEN"})
    assert res["ok"] is True and [p["key"] for p in res["projects"]] == ["AAA", "BBB"] and "pending" not in res
