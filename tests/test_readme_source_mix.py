"""MD-100: the README documents pipelines with any source mix (a Jira-only example) and removing a source by label."""
import re
import shlex
from pathlib import Path

from mdpipeline.cli import build_parser

README = (Path(__file__).parent.parent / "docs" / "reference.md").read_text()


def jira_only_section() -> str:
    match = re.search(r"^### A Jira-only pipeline.*?(?=^#{2,3} )", README, re.S | re.M)
    assert match, "README needs a '### A Jira-only pipeline' section"
    return match.group(0)


def test_the_old_one_or_more_folders_wording_is_gone():
    assert "one or more project folders" not in README.lower()
    assert "one or more sources" in README.lower()


def test_the_jira_only_example_and_its_commands_parse():
    parser = build_parser()
    commands = re.findall(r"^\s*(mdpipeline [^\n]+)$", jira_only_section(), re.M)
    assert len(commands) >= 3 and any("--jira-site" in c and "--source" not in c for c in commands)
    for line in commands:
        parser.parse_args(shlex.split(line.split("   #")[0])[1:])


def test_removing_a_source_by_label_and_its_rules_are_explained():
    text = jira_only_section().lower()
    for needle in ("--remove-source", "unowned", "--delete-files", "--dry-run", "at least one source", "guard"):
        assert needle in text, needle


def off_section() -> str:
    match = re.search(r"^### Switching a source Off and On.*?(?=^#{2,3} )", README, re.S | re.M)
    assert match, "README needs a '### Switching a source Off and On' section"
    return match.group(0)


def test_the_off_switch_is_documented_and_its_commands_parse():
    parser = build_parser()
    text = off_section()
    for needle in ("--source-off", "--source-on", "kept", "owned", "not read", "plan", "guard", "Off is not the same as removed"):
        assert needle.lower() in text.lower(), needle
    commands = re.findall(r"^\s*(mdpipeline [^\n]+)$", text, re.M)
    assert len(commands) >= 2
    for line in commands:
        parser.parse_args(shlex.split(line.split("   #")[0])[1:])
