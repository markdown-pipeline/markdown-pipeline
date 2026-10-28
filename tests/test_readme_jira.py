"""MD-57: the README documents the Jira source and every command it shows is a real command."""
import re
import shlex
from pathlib import Path

import pytest

from mdpipeline.cli import build_parser

README = (Path(__file__).parent.parent / "docs" / "reference.md").read_text()


def section() -> str:
    match = re.search(r"^## Jira source.*?(?=^## )", README, re.S | re.M)
    assert match, "README needs a '## Jira source' section"
    return match.group(0)


@pytest.mark.parametrize("topic", [
    "API token", "id.atlassian.com", "env:", "keychain:", "JQL", "project in (", "statusCategory", "updated >= -90d", "comments",
    "attachments", "not downloaded", "display name", "read-only", "custom fields", "history", "Server", "https", "atlassian.net",
    "never deletes", "returns 0", "permission", "Troubleshooting", "token rejected", "rate limit", "site not found", "invalid JQL",
    "conversion warning", "one file per issue", "jira test", "metadata-only"])
def test_the_section_covers_the_required_topics(topic):
    assert topic.lower() in section().lower(), topic


def test_every_mdpipeline_command_in_the_section_parses():
    parser = build_parser()
    commands = [line.strip() for line in re.findall(r"^\s*\$?\s*(mdpipeline [^\n]+)$", section(), re.M)]
    assert len(commands) >= 5
    for line in commands:
        parser.parse_args(shlex.split(line.split("   #")[0])[1:])


def test_the_cli_table_lists_the_jira_flags_and_test_command():
    for needle in ("--jira-site", "jira test"):
        assert needle in README
