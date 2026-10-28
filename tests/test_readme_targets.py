"""MD-70 / MD-80 / MD-90: the README documents per-pipeline targets and the vault, and every command it shows is a real command."""
import re
import shlex
from pathlib import Path

import pytest

from mdpipeline.cli import build_parser

README = (Path(__file__).parent.parent / "docs" / "reference.md").read_text()


def section() -> str:
    match = re.search(r"^## GitHub repository as a pipeline's target.*?(?=^## )", README, re.S | re.M)
    assert match, "README needs a '## GitHub repository as a pipeline's target' section"
    return match.group(0)


@pytest.mark.parametrize("topic", [
    "fine-grained", "Contents", "read and write", "--github", "protected", "md-sync", "pipeline test-target", "--clone", "fast-forward",
    "state.json", "never", "https", "api.github.com", "Troubleshooting", "token rejected", "rate limit", "head moved", "clone", "restore",
    "vault", "--stdin", "keychain", "passphrase", "vault locked", "--replace-token", "Sign in with GitHub", "device", "github.com/login/device",
    "installed", "install", "Only select repositories", "Sign in again", "sign-out", "app not installed", "change-target", "stays behind", "never changed",
    "vault import", "env:", "github-app set", "docs/github-app-registration.md", "refresh", "revoke"])
def test_the_section_covers_the_required_topics(topic):
    assert topic.lower() in section().lower(), topic


def test_every_mdpipeline_command_in_the_section_parses():
    parser = build_parser()
    commands = [line.strip() for line in re.findall(r"^\s*\$?\s*(mdpipeline [^\n]+)$", section(), re.M)]
    assert len(commands) >= 5
    for line in commands:
        argv = shlex.split(line.split("#")[0])[1:]
        parser.parse_args(argv)           # exits with an error if the documented command does not exist


def test_the_cli_table_lists_the_new_commands_and_not_the_removed_ones():
    for command in ("pipeline test-target NAME", "--github OWNER/REPO", "vault add", "init [DIR] [--create]"):
        assert command in README
    for removed in ("target switch", "target test", "init [DIR] --github"):
        assert removed not in README


def readme() -> str:
    return README


@pytest.mark.parametrize("topic", ["## Concepts", "- **Target:**", "- **Vault:**", "destination:", "Change target...", "Where the files land", "--jira-stdin", "vault import",
                                   "GitHub sign-in pages", "Targets and vault (", "per pipeline"])
def test_the_rest_of_the_readme_describes_targets_and_the_vault(topic):
    assert topic in README, topic


def test_every_command_in_a_code_block_of_the_whole_readme_parses():
    parser = build_parser()
    seen = 0
    for block in re.findall(r"```bash[^\n]*\n(.*?)```", README, re.S):
        text = re.sub(r"\\\n\s*", " ", block)                              # a backslash continues the command on the next line
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("mdpipeline "):
                continue
            argv = shlex.split(line.split("   #")[0])[1:]
            parser.parse_args(argv)
            seen += 1
    assert seen >= 20
