"""MD-236 AC8: every usage event that carries source_type also carries auth_mode (login | public) for a Jira source; never a host."""
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, usage
from mdpipeline.cli import main
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import EVENTS, Enum
from test_md236_public_jira_cli import PUBLIC, run, site  # noqa: F401
from test_usage_cli_events import own, queued, sharing  # noqa: F401
from test_jira_cli import JIRA, fake  # noqa: F401


def test_the_allowlist_gives_auth_mode_to_every_event_that_has_source_type():
    carriers = [n for n, e in EVENTS.items() if "source_type" in e.props]
    assert len(carriers) >= 10
    for name in carriers:
        rule = EVENTS[name].props.get("auth_mode")
        expected = ("login",) if name == "first_own_sync_completed" else ("login", "public")  # AC11: a public sync is never the first own sync
        assert isinstance(rule, Enum) and rule.values == expected, name


def test_no_other_event_carries_auth_mode():
    assert all("source_type" in e.props for e in EVENTS.values() if "auth_mode" in e.props)


def test_a_public_jira_test_reports_auth_mode_public(sharing, brain, site, capsys):
    run(capsys, brain, "jira", "test", *PUBLIC)
    events = queued("source_auth_started", "source_auth_completed")
    assert [own(e)["auth_mode"] for e in events] == ["public", "public"]


def test_a_login_jira_test_reports_auth_mode_login(sharing, brain, fake, capsys):
    run(capsys, brain, "jira", "test", *JIRA)
    assert {own(e)["auth_mode"] for e in queued("source_auth_started", "source_auth_completed")} == {"login"}


def test_adding_a_public_source_reports_it_on_the_source_events(sharing, brain, site, capsys):
    run(capsys, brain, "pipeline", "add", "p", *PUBLIC)
    for name in ("source_added", "source_connected", "source_scope_edited"):
        events = queued(name)
        assert events and {own(e)["auth_mode"] for e in events} == {"public"}, name


def test_a_public_sync_reports_auth_mode_public_on_completion_and_failure(sharing, brain, site, capsys):
    run(capsys, brain, "pipeline", "add", "p", *PUBLIC)
    run(capsys, brain, "sync", "p")
    assert {own(e)["auth_mode"] for e in queued("sync_completed")} == {"public"}
    site.public = False
    run(capsys, brain, "sync", "p")
    failed = queued("sync_failed")
    assert failed and own(failed[-1])["auth_mode"] == "public" and own(failed[-1])["error_class"] == "auth"


def test_a_folder_pipeline_has_no_auth_mode(sharing, brain, capsys, make_source):
    folder = make_source("docs", {"a.md": "A"})
    run(capsys, brain, "pipeline", "add", "f", "--source", str(folder))
    run(capsys, brain, "sync", "f")
    assert queued("sync_completed") and all("auth_mode" not in own(e) for e in queued("sync_completed", "source_added"))


def test_the_event_never_carries_the_site_host(sharing, brain, site, capsys):
    run(capsys, brain, "pipeline", "add", "p", *PUBLIC)
    run(capsys, brain, "sync", "p")
    assert "atlassian" not in json.dumps(queued())
