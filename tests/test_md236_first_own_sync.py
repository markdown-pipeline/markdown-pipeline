"""MD-236 AC11 (M1 integrity): a sync that reads a public Jira site is not "the person's own source", so it never fires first_own_sync_completed
and never sets its stamp. first_sync_completed is unchanged. A later login-Jira or folder sync on the same install still fires it, once."""
import json

from mdpipeline import usage
from test_md236_public_jira_cli import PUBLIC, run, site  # noqa: F401
from test_usage_cli_events import own, queued, sharing  # noqa: F401
from test_jira_cli import JIRA, fake  # noqa: F401


def stamp() -> object:
    return json.loads((usage.data_dir() / "usage.json").read_text()).get("first_own_sync_ts")


def test_a_public_sync_fires_first_sync_but_not_first_own_sync_and_leaves_no_stamp(sharing, brain, site, capsys):
    run(capsys, brain, "pipeline", "add", "pub", *PUBLIC)
    run(capsys, brain, "sync", "pub")
    assert len(queued("first_sync_completed")) == 1 and own(queued("first_sync_completed")[0])["auth_mode"] == "public"
    assert queued("first_own_sync_completed") == [] and stamp() is None


def test_a_later_folder_sync_on_the_same_install_still_fires_it_once(sharing, brain, site, capsys, make_source):
    run(capsys, brain, "pipeline", "add", "pub", *PUBLIC)
    run(capsys, brain, "sync", "pub")
    folder = make_source("docs", {"a.md": "A"})
    run(capsys, brain, "pipeline", "add", "f", "--source", str(folder))
    run(capsys, brain, "sync", "f")
    events = queued("first_own_sync_completed")
    assert len(events) == 1 and "auth_mode" not in own(events[0]) and stamp() is not None
    run(capsys, brain, "sync", "f")
    assert len(queued("first_own_sync_completed")) == 1


def test_a_later_login_jira_sync_on_the_same_install_still_fires_it(sharing, brain, site, fake, capsys):
    run(capsys, brain, "pipeline", "add", "pub", *PUBLIC)
    run(capsys, brain, "sync", "pub")
    run(capsys, brain, "pipeline", "add", "login", *JIRA)
    run(capsys, brain, "sync", "login")
    events = queued("first_own_sync_completed")
    assert len(events) == 1 and own(events[0])["auth_mode"] == "login"


def test_doc_says_first_own_sync_is_login_only_and_not_sent_for_public():
    """The public disclosure matches AC11: the event's auth_mode allows only `login`, and the section says a public sync is not sent."""
    from mdpipeline.usage_docs import render_doc
    section = render_doc().split("### `first_own_sync_completed`")[1].split("\n### ")[0]
    assert "| `auth_mode` | `login` |" in section
    assert ("Emitted from: engine. It is not sent for a sync that reads a public Jira site with no login (auth_mode `public`). "
            "The first later sync of your own source then counts instead.") in section
