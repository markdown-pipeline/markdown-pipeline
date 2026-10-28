"""MD-164: marketing's copy pass (K1-K13, docs/marketing/2026-10-20_mm-119-mm-105-ea-copy-and-first-testers.md), word for word.

The guard (AC2): no user-facing line says the secrets are "encrypted" without the keychain condition, because on a machine with no
keychain the vault is a plain file only the user can read (MD-163). Same pattern as test_no_brain_in_ui_copy.py."""
import re
from pathlib import Path

APP = Path(__file__).parent.parent
STATIC = APP / "src" / "mdpipeline" / "ui_static"
README = (APP / "docs" / "reference.md").read_text()
PUBLIC = (APP / "README.md").read_text()
APP_JS = (STATIC / "app.js").read_text()
FIRSTRUN_JS = (STATIC / "firstrun.js").read_text()

TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_k1_jira_token_hint_has_the_direct_link_and_no_encrypted():
    assert (f"Create one at {TOKEN_PAGE}. It goes once to this local app, is checked against Jira and kept in the app vault on this "
            "computer; it is never shown again.") in APP_JS


def test_k2_github_token_hint_has_no_encrypted():
    assert "is checked, and is kept in the app vault on this computer. It is never shown again." in APP_JS


def test_k3_vault_glossary_names_the_keychain_condition():
    assert ("**Vault:** the app's own store for secrets on this computer (encrypted with an OS keychain; without one, a file only your "
            "user can read)") in flat(README)


def test_k4_jira_token_section_has_the_direct_link_and_no_encrypted():
    text = flat(README)
    assert f"At {TOKEN_PAGE} (**Create API token**)" in text and "keeps them in its **vault**" in text


def test_k5_failure_row_has_the_direct_link():
    assert f"Create a new API token at {TOKEN_PAGE} and enter it" in flat(README)


def test_k6_vault_section_says_output_folder_not_brain():
    assert "never in your output folder or a repository" in flat(README)


def test_k8_first_run_review_line_says_desktop_login():
    assert "It keeps running when this window is closed and starts when you log in to your desktop." in FIRSTRUN_JS
    assert "starts at login" not in FIRSTRUN_JS


def test_k9_settings_line_says_desktop_login():
    assert "(starts when you log in to your desktop)" in APP_JS and "(starts at login)" not in APP_JS


def test_k10_get_started_step_3_has_the_where_the_files_go_step():
    text = flat(README)
    assert ("Then choose where the files go: a folder on this computer (suggested: `~/markdown-pipeline/<name>`) or your team's GitHub "
            "repo. Review what will be written and press the button.") in text
    assert "and your files appear in `~/markdown-pipeline/<name>`" not in text


def test_k12_file_store_note_by_path():
    assert ("This computer has no system keychain, so your ${what} is kept in a file only your user can read, the way \\`gh\\` and the AWS "
            "CLI do it. To use a keychain instead, install libsecret-tools.") in FIRSTRUN_JS
    assert '"token"' in FIRSTRUN_JS and '"GitHub sign-in"' in FIRSTRUN_JS
    assert "stronger protection" not in FIRSTRUN_JS


def test_k13_next_sync_in_a_moment():
    assert "Next sync in a moment." in APP_JS or "in a moment" in APP_JS
    assert "Next sync any moment" not in APP_JS


def test_ac2_no_user_facing_line_says_encrypted_without_the_keychain_condition():
    """Every 'encrypted' in the UI or the READMEs must sit next to a keychain condition."""
    found = []
    sources = {"app.js": APP_JS, "firstrun.js": FIRSTRUN_JS, "README.md": README, "README.md (front page)": PUBLIC}
    for name, text in sources.items():
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"encrypt", line, re.I) and not re.search(r"keychain|frFileStore", line, re.I):
                found.append(f"{name}:{number}: {line.strip()[:110]}")
    assert found == []


def test_ac3_the_github_path_says_github_sign_in_and_the_jira_path_says_token():
    github = [line for line in FIRSTRUN_JS.splitlines() if "frIsGithub()" in line and "FR_FILE_STORE_NOTE(" in line]
    jira = [line for line in FIRSTRUN_JS.splitlines() if "FR_FILE_STORE_NOTE(" in line and "frIsGithub()" not in line]
    assert len(github) == 1 and 'FR_FILE_STORE_NOTE("GitHub sign-in")' in github[0]
    assert len(jira) == 1 and 'FR_FILE_STORE_NOTE("token")' in jira[0]
