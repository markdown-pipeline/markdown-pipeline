"""MD-167: marketing's MM-126 review (L1, L2, L2b, L3, L4), word for word, plus the guard that no README or CLI line tells server users
to run `mdpipeline background` by hand after a reboot (it restarts at login or on any command, MD-165)."""
import re
from pathlib import Path

from mdpipeline import autostart, cli

APP = Path(__file__).parent.parent
STATIC = APP / "src" / "mdpipeline" / "ui_static"
README = (APP / "docs" / "reference.md").read_text()
PUBLIC = (APP / "README.md").read_text()
APP_JS = (STATIC / "app.js").read_text()
FIRSTRUN_JS = (STATIC / "firstrun.js").read_text()
CLI_SRC = (APP / "src" / "mdpipeline" / "cli.py").read_text()


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


L1 = ("Background sync stops when you log out, and after a reboot it waits until you log in. To keep it running, run this once: "
      "`loginctl enable-linger $USER` (if it says access denied: `sudo loginctl enable-linger $USER`).")
L2 = "Background sync is on: it keeps running when this window is closed and starts again when you log in."


def test_l1_linger_line_word_for_word():
    assert autostart.LINGER_LINE == L1


def test_l1_cli_version_is_plain_text_without_backticks():
    assert autostart.LINGER_LINE_PLAIN == L1.replace("`", "") and "`" not in autostart.LINGER_LINE_PLAIN


def test_l1_ui_renders_the_commands_in_code_style():
    assert "lingerHtml" in APP_JS and "<code>" in APP_JS
    assert "lingerHtml(bg.linger_text)" in APP_JS and "lingerHtml(S.state.background.linger_text)" in FIRSTRUN_JS
    assert "esc(bg.linger_text)" not in APP_JS and "esc(S.state.background.linger_text)" not in FIRSTRUN_JS


def test_l2_schedule_background_on_line():
    assert f'print("{L2}")' in CLI_SRC


def test_l2b_schedule_help_matches_l2():
    assert ("A small background process starts at login") not in CLI_SRC
    assert ("A small background process keeps running when the window is closed and starts again when you log in.") in CLI_SRC


def test_l3_readme_step_5():
    assert ("On a server or in WSL 2 it stops when you log out unless you run `loginctl enable-linger $USER` once, and after a reboot "
            "logging in or running any `mdpipeline` command starts it again (see Scheduled sync).") in flat(README)


def test_l4_public_readme_platforms():
    assert ("On a server or in WSL 2, background sync stops when you log out unless you run `loginctl enable-linger $USER` once; after a "
            "reboot, logging in or running any `mdpipeline` command starts it again.") in flat(PUBLIC)


def test_no_readme_or_cli_line_tells_server_users_to_start_it_by_hand_after_a_reboot():
    for text in (flat(README), flat(PUBLIC), flat(CLI_SRC)):
        assert not re.search(r"after a reboot[^.]{0,80}(start|run)[^.]{0,40}`?mdpipeline background", text)
        assert "start it again after a reboot with" not in text
        assert "after a reboot start background sync" not in text


def test_k8_and_k9_are_unchanged():
    assert "It keeps running when this window is closed and starts when you log in to your desktop." in FIRSTRUN_JS
    assert "(starts when you log in to your desktop)" in APP_JS
