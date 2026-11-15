"""MD-236: `--jira-public` on `pipeline add` and `jira test`: no email, no token, no prompt, no vault."""
import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api
from mdpipeline.cli import main
from mdpipeline.config import load_config

SITE = "https://open.atlassian.net"
PUBLIC = ["--jira-public", "--jira-site", SITE, "--jira-jql", "project = HHH"]


@pytest.fixture
def site():
    f = FakeJira(site="open")
    f.public = True
    for n in range(3):
        f.add_issue("HHH", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_jira_test_in_public_mode_prints_site_no_login_and_the_match_count(brain, site, capsys):
    code, out, err = run(capsys, brain, "jira", "test", *PUBLIC)
    assert code == 0, err
    assert SITE in out and "no login (public)" in out and "3 issues" in out
    assert set(site.auth_headers) == {None}


def test_pipeline_add_public_saves_the_mode_and_no_credentials(brain, site, capsys):
    code, out, err = run(capsys, brain, "pipeline", "add", "p", *PUBLIC)
    assert code == 0, err
    src = load_config(brain).get("p").sources[0]
    assert src.options == {"site": SITE, "jql": "project = HHH", "auth_mode": "public"}
    assert not (brain.root / "vault").exists() if hasattr(brain, "root") else True


def test_public_mode_never_asks_for_a_token(brain, site, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("asked for input"))
    monkeypatch.setattr("getpass.getpass", lambda *a: pytest.fail("asked for a secret"))
    code, _, err = run(capsys, brain, "pipeline", "add", "p", *PUBLIC)
    assert code == 0, err


@pytest.mark.parametrize("extra", [["--jira-email", "me@example.com"], ["--jira-token", "env:X"], ["--jira-stdin"]])
def test_public_mode_with_a_login_flag_is_a_usage_error(brain, site, capsys, extra):
    code, _, err = run(capsys, brain, "pipeline", "add", "p", *PUBLIC, *extra)
    assert code == 2 and "--jira-public" in err and load_config(brain).get("p") is None


def test_public_mode_on_a_private_site_is_refused_with_the_plain_message(brain, site, capsys):
    site.public = False
    code, out, err = run(capsys, brain, "jira", "test", *PUBLIC)
    assert code != 0 and "open.atlassian.net doesn't let anyone read these issues without a login. Log in with your Atlassian email and API token instead, or choose a public project." in err


def test_the_help_names_the_public_flag(capsys):
    with pytest.raises(SystemExit):
        main(["pipeline", "add", "--help"])
    assert "--jira-public" in capsys.readouterr().out
