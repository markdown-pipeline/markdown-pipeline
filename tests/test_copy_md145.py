"""MD-145: copy fixes from the marketing review MM-107 (no inaccurate capability claim, calm non-failure states)."""
from pathlib import Path

from mdpipeline.targetmessages import NEXT_STEPS as MESSAGES

STATIC = Path(__file__).parent.parent / "src" / "mdpipeline" / "ui_static"


def test_agent_card_does_not_claim_the_mcp_server_is_read_only():
    js = (STATIC / "agents.js").read_text(encoding="utf-8")
    assert "read-only MCP" not in js
    assert ("Registers our MCP server with ${esc(f.label)}, so it can search, read and refresh these files. It never changes your sources. "
            "It also adds an AGENTS.md to your folder. Only when you click.") in js


def test_expired_sign_in_message_does_not_mention_uninstalling_the_app():
    title, message = MESSAGES["sign-in-expired"]
    assert "uninstalled" not in message and "expired or was revoked" in message


def test_expired_sign_in_is_amber_not_red():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    line = next(l for l in js.splitlines() if 'n.category === "sign-in-expired") return' in l)
    assert "err" not in line.split("class=")[1].split(">")[0] and "✕" not in line


def test_next_sync_checking_is_lower_case_after_the_colon():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Next sync: checking…" in js and "Next sync: Checking…" not in js
