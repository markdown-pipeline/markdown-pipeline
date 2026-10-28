"""MD-144: the README and user docs make only claims that the build's behaviour backs (marketing check MM-107)."""
import re
from pathlib import Path

APP = Path(__file__).parent.parent
README = (APP / "docs" / "reference.md").read_text()
WHAT_WE_SEND = (APP / "docs" / "what-we-send.md").read_text()
USER_DOCS = {
    "README.md": README,
    "docs/what-we-send.md": WHAT_WE_SEND,
    **{f"docs/{p.name}": p.read_text() for p in (APP / "docs").glob("troubleshooting-*.md")},
}

NETWORK_RULE = (
    "Network calls come only from the sources and destinations you connect (your Atlassian site; api.github.com and the GitHub "
    "sign-in pages if you use a GitHub repository), and, only if you chose \"Share usage\" or press Send, from usage data to "
    "us.i.posthog.com. With folder sources, a local folder and sharing off, the app makes zero network calls. Everything is "
    "https; the window listens on 127.0.0.1 only."
)
LOCAL_RULE = (
    "Your files stay in a folder you choose. Nothing is uploaded unless you choose a GitHub repository as the destination."
)


def flat(text: str) -> str:
    """Collapse whitespace so a sentence wrapped over lines still matches."""
    return re.sub(r"\s+", " ", text)


def test_m1_network_rule_states_the_posthog_exception():
    assert NETWORK_RULE in flat(README)


def test_m1_no_user_doc_says_no_telemetry():
    for name, text in USER_DOCS.items():
        assert not re.search(r"no\s+telemetry", text, re.I), name


def test_m2_local_claim_names_the_github_exception():
    assert LOCAL_RULE in flat(README)
    assert "nothing is uploaded." not in flat(README).replace(LOCAL_RULE, "").lower()


def test_m3_install_section_has_no_commands_an_invitee_cannot_run():
    for name, text in USER_DOCS.items():
        for forbidden in ("uv tool install mdpipeline", "pipx install mdpipeline", "one-liner", "download page"):
            assert forbidden not in text, (name, forbidden)


def test_m3_install_names_the_private_repo():
    assert "markdown-pipeline/markdown-pipeline" in README


def test_m3_exactly_one_install_to_first_screen_measurement():
    assert len(re.findall(r"install to first\s+screen", README)) == 1


def test_m4_quickstart_states_the_raw_default():
    text = flat(README)
    assert "land at its root" not in text
    assert "raw/{relpath}" in README


def test_s1_no_unmeasured_five_minute_claim():
    assert "about 5 minutes" not in README
    assert README.count("(a few minutes)") == 2


def test_s2_user_readme_has_no_internal_ids():
    ids = re.findall(r"\bMD-\d+\b|\bMP-\d+\b|\bB[1-8]\b|\bA[1-6]\b|\bP[123](?:\.\d)?\b", README)
    assert ids == []


def test_s2_developer_reference_moved_to_docs_development():
    dev = (APP / "docs" / "development.md").read_text()
    for heading in ("## Decisions and assumptions", "## Tests", "## Layout"):
        assert heading in dev, heading
        assert heading not in README, heading


def test_s3_what_we_send_makes_no_credential_level_read_only_claim():
    assert "read-only" not in WHAT_WE_SEND
    assert "we only read" in WHAT_WE_SEND
