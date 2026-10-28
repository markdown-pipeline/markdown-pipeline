"""MD-125: the README gets a stranger from install to a connected agent, its pages exist and say what the code does."""
import re
from pathlib import Path

from mdpipeline.usage_events import EVENTS

APP = Path(__file__).parent.parent
README = (APP / "docs" / "reference.md").read_text(encoding="utf-8")


def _get_started() -> str:
    return README.split("## Get started", 1)[1].split("\n## ", 1)[0]


def test_get_started_is_the_first_section_and_covers_every_required_step():
    assert README.index("## Get started") < README.index("## Install")
    text = _get_started().lower()
    for topic in ("install", "mdpipeline", "jira", "folder", "sample", "connect claude code", "background", "log in", "uninstall",
                  "schedule background off"):
        assert topic in text, topic


def test_every_relative_link_in_the_readme_points_at_a_file_that_exists():
    targets = [t for t in re.findall(r"\]\(([^)#\s]+)", README) if not t.startswith(("http://", "https://", "mailto:", "../"))]
    assert targets, "the reference links to its pages"
    for target in targets:     # the reference lives in docs/, so its links are relative to that folder
        assert (APP / "docs" / target).is_file(), target


def test_the_three_troubleshooting_pages_and_what_we_send_are_linked_from_get_started():
    text = _get_started()
    for page in ("troubleshooting-install.md", "troubleshooting-jira-token.md", "troubleshooting-agent.md", "what-we-send.md"):
        assert f"]({page}" in README, page
    assert "what-we-send.md" in README.split("## Get started")[0], "the claims block links to what is sent before the steps"
    assert text.count("](troubleshooting-") >= 3


def test_the_jira_token_page_in_the_docs_is_the_one_the_app_serves():
    assert (APP / "docs/troubleshooting-jira-token.md").read_text(encoding="utf-8") == (APP / "src/mdpipeline/ui_static/jira-token-help.md").read_text(encoding="utf-8")


def test_the_jira_page_has_the_combined_401_message_and_the_no_scoped_tokens_rule():
    text = (APP / "docs/troubleshooting-jira-token.md").read_text(encoding="utf-8")
    assert "same answer for a wrong email and for a bad token" in text
    assert "not \"Create API token with scopes\"" in text


def test_the_agent_page_names_the_buttons_the_screen_really_has():
    page = (APP / "docs/troubleshooting-agent.md").read_text(encoding="utf-8")
    js = (APP / "src/mdpipeline/ui_static/agents.js").read_text(encoding="utf-8")
    for label in ("Replace yours (a copy is kept)", "Keep yours, add ours as", "Another agent"):
        assert label in page and label in js, label
    assert "MARKDOWN-PIPELINE.md" in page


def test_what_we_send_names_every_event_that_is_emitted_and_no_event_that_does_not_exist():
    page = (APP / "docs/what-we-send.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z_]+)`", page)) & {n for n in re.findall(r"`([a-z_]+)`", page) if "_" in n}
    events = set(EVENTS)
    emitted = {name for name, event in EVENTS.items() if not event.ships_with}
    for name in emitted - {"consent_outcome"}:
        assert name in page, f"{name} is on the allowlist but not explained"
    unknown = {n for n in named if re.fullmatch(r"[a-z]+_[a-z_]+", n)} - events
    assert unknown <= {"reset_id", "is_internal"}, unknown      # is_internal is a field on every event (MD-154), not an event


def test_what_we_send_lists_only_events_that_are_really_sent():
    """MD-138: a user-facing doc has no 'Coming soon' row; an event not emitted yet is not named."""
    page = (APP / "docs/what-we-send.md").read_text(encoding="utf-8")
    assert "Coming soon" not in page
    for name, event in EVENTS.items():
        if event.ships_with:
            assert f"`{name}`" not in page, f"{name} is not emitted yet ({event.ships_with}) but the page names it"


def test_what_we_send_lists_the_two_explicit_sends_and_the_limits_in_code():
    page = (APP / "docs/what-we-send.md").read_text(encoding="utf-8")
    assert "source_requested" in page and "feedback_answered" in page
    assert "100 characters" in page and "500 characters" in page
    assert len(EVENTS["source_requested"].props["other_source"].check("x" * 101)) == 100        # longer text is cut, never sent whole
    assert len(EVENTS["feedback_answered"].props["comment"].check("x" * 501)) == 500


def test_what_we_send_says_it_is_off_until_chosen_and_how_to_turn_it_off():
    page = (APP / "docs/what-we-send.md").read_text(encoding="utf-8")
    assert "off until you choose" in page and "mdpipeline usage off" in page and "mdpipeline usage log" in page


def test_every_command_the_pages_tell_people_to_type_exists():
    from mdpipeline.cli import build_parser

    parser = build_parser()
    for cmd in ("start", "update", "uninstall", "connect", "background", "usage on", "usage off", "usage log", "usage reset-id", "schedule background off"):
        parser.parse_args(cmd.split())


def test_what_we_send_lists_the_project_list_read_call_and_that_names_stay_local():
    """MD-141 AC6: the one new Jira read call is named, and project names and keys are not sent anywhere."""
    page = (APP / "docs/what-we-send.md").read_text(encoding="utf-8")
    assert "lists the\nprojects your account can read" in page and "names stay on your computer" in page


# MD-131 AC3: the install section is the one public command (Linux), with no checkout, no environment-variable and no invite step ---------
def _app_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def _install_section(text):
    return text.split("## Install", 1)[1].split("\n## ", 1)[0]


def test_the_install_section_is_the_one_public_command_and_nothing_about_checkouts_or_invites():
    from mdpipeline import lifecycle

    text = (_app_dir() / "docs" / "reference.md").read_text(encoding="utf-8")
    section = _install_section(text)
    assert lifecycle.INSTALL_COMMAND in section and lifecycle.INSTALL_COMMAND in text.split("## Get started", 1)[1].split("\n## ", 1)[0]
    for old in ("scripts/install.sh", "MDPIPELINE_SOURCE", "invited", "checkout", "private repo", "clone it"):
        assert old not in section, old
    assert "scripts/install.sh" not in text.split("## Get started", 1)[1].split("\n## ", 1)[0]


def test_the_troubleshooting_page_uses_the_same_public_command():
    from mdpipeline import lifecycle

    page = (_app_dir() / "docs" / "troubleshooting-install.md").read_text(encoding="utf-8")
    assert lifecycle.INSTALL_COMMAND in page and "sh scripts/install.sh" not in page


def test_the_public_readme_says_what_marketing_asked_for_and_nothing_it_forbade():
    from mdpipeline import lifecycle

    text = (_app_dir() / "README.md").read_text(encoding="utf-8")
    low = text.lower()
    assert lifecycle.INSTALL_COMMAND in text
    for must in ("linux (tested)", "macos", "wsl 2", "we only read", "what-we-send", "issues"):
        assert must in low, must
    for never in ("read-only token", "insight", "company brain", "knowledge base", "ai search", "slack", "m365", "microsoft 365", "every os", "invite"):
        assert never not in low, never
    assert not re.search(r"\b\d+\s*(seconds?|minutes?|min|s)\b", low)      # no setup time we have not measured


# MM-121 (marketing, 2026-10-18): four exact changes to the public README ---------------------------------------------------------------------
def _public_readme():
    return (_app_dir() / "README.md").read_text(encoding="utf-8")


def test_c1_the_local_claim_is_true_once_the_github_target_exists():
    text = _public_readme()
    assert "Your files stay in a folder you choose" not in text
    assert re.search(r"folder you choose,? or (to )?a GitHub repo", text)


def test_c2_the_install_line_says_it_installs_uv_if_it_is_missing():
    install = _public_readme().split("## Install", 1)[1].split("\n## ", 1)[0]
    assert re.search(r"installs `?uv`? (first )?if (you don't have it|it is missing|it's missing)", install, re.I)


def test_c3_after_install_there_is_a_command_not_found_hint():
    install = _public_readme().split("## Install", 1)[1].split("\n## ", 1)[0]
    assert "command not found" in install and "new terminal" in install


def test_c4_the_issues_link_is_absolute():
    text = _public_readme()
    assert "https://github.com/markdown-pipeline/markdown-pipeline/issues" in text and "../../issues" not in text
