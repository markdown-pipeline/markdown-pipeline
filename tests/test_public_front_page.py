"""MD-186: the short README is the repo's front page, with marketing's four changes (MM-142); the full manual is docs/reference.md."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
FRONT = (APP / "README.md").read_text(encoding="utf-8")


def _release():
    spec = importlib.util.spec_from_file_location("release_script_ff", APP / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_script_ff"] = module
    spec.loader.exec_module(module)
    return module


def _section(title: str) -> str:
    return FRONT.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def test_the_front_page_is_short():
    assert len(FRONT.splitlines()) < 80


def test_the_full_manual_moved_to_docs_reference_and_the_front_page_links_it():
    assert (APP / "docs" / "reference.md").read_text(encoding="utf-8").startswith("# Markdown Pipeline")
    assert "](docs/reference.md)" in FRONT


def test_change3_the_two_trust_bullets_follow_raw_layer_only():
    head = FRONT.split("## Install", 1)[0]
    assert head.index("**Raw layer only.**") < head.index("**It sees what you see.**") < head.index("**Open source.**")
    assert "Apache-2.0" in head.split("**Open source.**", 1)[1].split("\n", 1)[0]
    assert "never appears in the files, the logs or your agent's answers" in head


def test_change4_the_mcp_section_follows_install_and_precedes_what_we_send():
    assert FRONT.index("## Install") < FRONT.index("## Use it from your agent (MCP)") < FRONT.index("## What we send")


def test_change4_the_mcp_section_names_the_connect_command():
    section = _section("Use it from your agent (MCP)")
    assert "mdpipeline connect --agent claude" in section and "--agent codex" in section


def test_change4_the_tools_named_are_exactly_the_tools_the_server_lists(brain):
    async def fn(c):
        return {t.name for t in (await c.list_tools()).tools}

    from test_mcp_server import session

    served = session(brain, fn)
    named = set(re.findall(r"`([a-z_]+)`", _section("Use it from your agent (MCP)").split("Tools:", 1)[1].split("No tool writes", 1)[0]))
    assert named == served == {"search", "read", "list", "recent_changes", "sync_status", "sync"}


def test_the_license_section_is_last_and_points_at_the_license_file():
    assert FRONT.rstrip().endswith("## License\n\nApache-2.0. See [LICENSE](LICENSE).")


def test_the_repo_description_and_topics_are_marketings_words():
    rel = _release()
    assert rel.REPO_DESCRIPTION == ("Your team's tools as clean Markdown files, kept current for your AI agents, with a local MCP server. "
                                    "Early access: Jira Cloud and local folders, Linux. Apache-2.0.")
    assert rel.REPO_TOPICS == ["markdown", "mcp", "mcp-server", "model-context-protocol", "jira", "claude-code", "codex",
                               "ai-agents", "llm", "knowledge-base", "llm-wiki", "etl"]


def test_the_settings_command_sets_every_topic():
    cmd = _release().repo_settings_command("o/r")
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-topic"] == _release().REPO_TOPICS
