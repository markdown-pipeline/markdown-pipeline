"""MD-236 AC9: one opt-in LIVE test against a real public Jira Cloud site (hibernate.atlassian.net). Skipped unless ``MDP_LIVE_PUBLIC_JIRA=1``:
CI and the normal local run never touch the network. Read-only; no login of any kind."""
import os

import pytest

from mdpipeline.jira_api import JiraApi
from mdpipeline.jira_source import JiraSource
from mdpipeline.config import Source

pytestmark = pytest.mark.skipif(os.environ.get("MDP_LIVE_PUBLIC_JIRA") != "1", reason="live test: set MDP_LIVE_PUBLIC_JIRA=1 to read hibernate.atlassian.net")

SITE = "https://hibernate.atlassian.net"
JQL = "project = HHH ORDER BY updated DESC"


def test_a_real_public_site_answers_anonymously_and_myself_does_not():
    api = JiraApi(SITE, public=True)
    assert api.count(JQL) > 0
    issues = api.list_issues("project = HHH AND updated >= -30d", "summary,created,updated", 20)
    assert issues and all(i["key"].startswith("HHH-") for i in issues)
    with pytest.raises(Exception):
        JiraApi(SITE, "nobody@example.com", "no-token").myself()          # the real site refuses /myself without a valid login


def test_test_connection_reports_no_login_and_a_count():
    info = JiraSource(Source(SITE, "hhh", type="jira", options={"site": SITE, "jql": JQL, "auth_mode": "public"})).test_connection()
    assert info["user"] == "no login (public)" and info["matches"] > 0
