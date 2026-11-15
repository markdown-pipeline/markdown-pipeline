"""MD-236: the Jira client in public mode: no Authorization header, a public-site check instead of /myself, plain auth/429 errors."""
import pytest

from fake_jira import FakeJira
from mdpipeline.jira_api import JiraApi
from mdpipeline.sources import SourceError


@pytest.fixture
def fake():
    f = FakeJira(site="open", email="x@example.com", token="t")
    f.public = True
    for n in range(3):
        f.add_issue("HHH", f"issue {n}")
    return f


def public_api(fake, sleeps=None):
    return JiraApi("https://open.atlassian.net", None, None, public=True, transport=fake, sleep=(sleeps or []).append)


def test_public_requests_carry_no_authorization_header(fake):
    api = public_api(fake)
    assert api.count("project = HHH") == 3
    api.list_issues("project = HHH", "summary,updated")
    api.issue("10001")
    api.comments("10001")
    assert fake.auth_headers and set(fake.auth_headers) == {None}


def test_public_mode_never_calls_myself(fake):
    api = public_api(fake)
    api.list_issues("project = HHH", "summary")
    assert ("GET", "/rest/api/3/myself") not in fake.requests


def test_public_check_runs_once_per_run(fake):
    api = public_api(fake)
    api.count("project = HHH")
    api.list_issues("project = HHH", "summary")
    assert fake.requests.count(("POST", "/rest/api/3/search/approximate-count")) == 1


def test_public_check_fails_when_the_site_does_not_answer_anonymously(fake):
    fake.public = False                       # a private site serves an anonymous request as 200 with nothing
    with pytest.raises(SourceError) as exc:
        public_api(fake).list_issues("project = HHH", "summary")
    assert exc.value.category == "auth"
    assert str(exc.value) == "open.atlassian.net doesn't let anyone read these issues without a login. Log in with your Atlassian email and API token instead, or choose a public project."


def test_public_check_passes_with_zero_matches_when_the_site_lists_projects(fake):
    assert public_api(fake).list_issues("project = NOPE", "summary") == []


def test_public_site_with_no_projects_and_no_matches_is_refused():
    empty = FakeJira(site="open")
    empty.public = True
    with pytest.raises(SourceError) as exc:
        public_api(empty).count("project = X")
    assert exc.value.category == "auth"


def test_public_site_going_private_midway_is_an_auth_error(fake):
    api = public_api(fake)
    api.count("project = HHH")
    fake.fail("GET", "/search/jql", status=401)
    with pytest.raises(SourceError) as exc:
        api.list_issues("project = HHH", "summary")
    assert exc.value.category == "auth" and str(exc.value) == "open.atlassian.net doesn't let anyone read these issues without a login. Log in with your Atlassian email and API token instead, or choose a public project."


def test_public_rate_limit_is_a_rate_limited_error_that_says_try_later(fake):
    fake.rate_limit(times=10, retry_after=1)
    sleeps = []
    with pytest.raises(SourceError) as exc:
        public_api(fake, sleeps).count("project = HHH")
    assert exc.value.category == "rate-limited" and "open.atlassian.net is limiting requests right now. Wait a few minutes, then run the same command again." in str(exc.value)


def test_login_mode_is_unchanged_and_still_sends_credentials():
    f = FakeJira(site="acme", email="me@example.com", token="tok")
    f.add_issue("MD", "a")
    api = JiraApi("https://acme.atlassian.net", "me@example.com", "tok", transport=f)
    assert api.count("project = MD") == 1
    assert ("GET", "/rest/api/3/myself") in f.requests and all(h and h.startswith("Basic ") for h in f.auth_headers)
