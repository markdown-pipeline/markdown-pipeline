"""MD-222: tells the person, once a day, that a newer release is on PyPI, with the one command that updates their install.

What it does: at most once per 24 hours it asks ``https://pypi.org/pypi/mdpipeline/json`` for the latest release (a plain GET: no install ID, no query
string, no body, only the standard headers: Accept and the default User-Agent), keeps the answer in the app data directory, and shows a one-line notice on the command line (stderr) and a
notice on the app page. It runs whatever the usage-consent choice is (it sends nothing about the person); ``MDPIPELINE_NO_UPDATE_CHECK=1`` or
``mdpipeline update-check off`` turns it off. Every public function is safe to call from any code path: none raises, none waits more than the timeout.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import usage
from .vault import data_dir

ENV_OFF = "MDPIPELINE_NO_UPDATE_CHECK"
URL = "https://pypi.org/pypi/mdpipeline/json"
ALLOWED_HOST = "pypi.org"
RELEASES_URL = "https://github.com/markdown-pipeline/markdown-pipeline/releases/tag/v{version}"
CHECK_EVERY_S = 24 * 3600
TIMEOUT_S = 2
FILE = "update-check.json"
SOURCE_COMMAND = "git pull, then `uv tool install --reinstall .` in the app folder"
QUIET_COMMANDS = ("mcp", "ui", "start", "update", "update-check", "background", "install-note", "usage", "uninstall")      # no line: stdio, long-lived or the update itself
_RELEASE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}")


# --- the setting -----------------------------------------------------------------------------------------------------------------------------
def _read() -> dict:
    try:
        data = json.loads((data_dir() / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    try:
        data_dir().mkdir(parents=True, exist_ok=True)
        (data_dir() / FILE).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def enabled() -> bool:
    """On unless ``MDPIPELINE_NO_UPDATE_CHECK`` is set to 1/true/yes or the person ran ``mdpipeline update-check off``."""
    if os.environ.get(ENV_OFF, "").strip().lower() in ("1", "true", "yes"):
        return False
    return not _read().get("off")


def set_enabled(on: bool) -> None:
    data = _read()
    data["off"] = not on
    _write(data)


def in_ci() -> bool:
    return bool(os.environ.get("CI"))


# --- what is installed, and how ----------------------------------------------------------------------------------------------------------------
def installed_version() -> str:
    from . import __version__

    return __version__


def method() -> str:
    from .install_info import install_method

    return install_method()


def _numbers(version: str | None) -> tuple[int, ...] | None:
    return tuple(int(p) for p in version.split(".")) if isinstance(version, str) and _RELEASE.fullmatch(version) else None


def command() -> str:
    """The one command that fits this install method (a source checkout gets the git pull text)."""
    from .install_info import command_prefix

    kind = method()
    return SOURCE_COMMAND if kind == "source" else f"{command_prefix(kind)} update"


# --- asking PyPI -----------------------------------------------------------------------------------------------------------------------------------
def _check_url(url: str, *, allowed_host: str = ALLOWED_HOST) -> str:
    """https only, only PyPI (the app's network rule, tests/test_definition_of_done.py)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != allowed_host or parts.query:
        raise ValueError("the update check only talks to PyPI over https, with no query string")
    return url


def fetch_latest(opener=urllib.request.urlopen) -> str | None:
    """The latest release on PyPI, or ``None`` for anything else (offline, slow, unreadable, a pre-release)."""
    try:
        _check_url(URL)
        request = urllib.request.Request(URL, headers={"Accept": "application/json"})
        with opener(request, timeout=TIMEOUT_S) as response:
            version = json.loads(response.read().decode("utf-8")).get("info", {}).get("version")
    except Exception:                   # noqa: BLE001 - offline is normal and silent
        return None
    return version if _numbers(version) else None


def refresh(now: float | None = None, fetch=fetch_latest) -> None:
    """Ask PyPI if the last try was over 24 h ago (a failed try also waits a day, and the last good answer stays)."""
    try:
        if not enabled():
            return
        now = time.time() if now is None else now
        data = _read()
        if isinstance(data.get("checked_at"), (int, float)) and now - data["checked_at"] < CHECK_EVERY_S:
            return
        data["checked_at"] = now
        _write(data)                          # before asking: a second process does not ask again at the same time
        found = fetch()
        if found:
            data = _read()
            data["latest"] = found
            _write(data)
    except Exception:                   # noqa: BLE001
        return


def refresh_in_background() -> None:
    """For the long-lived app server: ask without making any request wait."""
    if enabled() and not in_ci():
        threading.Thread(target=refresh, daemon=True).start()


# --- what the person sees ------------------------------------------------------------------------------------------------------------------------
def available(now: float | None = None) -> dict | None:
    """``{"from", "to"}`` when the kept answer is newer than what runs, and the check is on."""
    if not enabled():
        return None
    here, latest = installed_version(), _read().get("latest")
    a, b = _numbers(here), _numbers(latest)
    return {"from": here, "to": latest} if a and b and b > a else None


def _claim(surface: str, now: float | None) -> bool:
    """True the first time on each UTC day for ``surface``."""
    day = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).date().isoformat()
    data = _read()
    shown = data.get("shown") if isinstance(data.get("shown"), dict) else {}
    if shown.get(surface) == day:
        return False
    data["shown"] = {**shown, surface: day}
    _write(data)
    return True


def _report_shown(found: dict, surface: str) -> None:
    data = _read()
    data["pending"] = {"from": found["from"], "to": found["to"]}          # for update_notice_acted
    _write(data)
    usage.emit("update_notice_shown", from_version=found["from"], to_version=found["to"], surface=surface)


def cli_line(now: float | None = None) -> str | None:
    """The one command-line line, at most once a day; ``None`` when there is nothing to say."""
    try:
        found = available(now)
        if not found or not _claim("cli", now):
            return None
        _report_shown(found, "cli")
        return f"Markdown Pipeline {found['to']} is out (you have {found['from']}). Update: {command()}"
    except Exception:                   # noqa: BLE001
        return None


def app_notice(now: float | None = None) -> dict | None:
    """The app page's notice while there is a newer version the person has not hidden. Reported once a day, whatever the redraws."""
    try:
        found = available(now)
        if not found or _read().get("hidden") == found["to"]:
            return None
        if _claim("app", now):
            _report_shown(found, "app")
        return {"text": f"Version {found['to']} is out. You have {found['from']}.", "command": command(),
                "whats_new": RELEASES_URL.format(version=found["to"]), "to": found["to"], "from": found["from"]}
    except Exception:                   # noqa: BLE001
        return None


def dismiss() -> None:
    """Hide on the app page, until the next version."""
    found = available()
    if found:
        data = _read()
        data["hidden"] = found["to"]
        _write(data)


def note_acted() -> None:
    """The first run on (or after) the version a notice named: reported once."""
    try:
        data = _read()
        pending = data.get("pending")
        if not isinstance(pending, dict):
            return
        here, target = _numbers(installed_version()), _numbers(pending.get("to"))
        if here and target and here >= target:
            data.pop("pending", None)
            _write(data)
            usage.emit("update_notice_acted", from_version=pending["from"], to_version=pending["to"])
    except Exception:                   # noqa: BLE001
        return


def after_command(command_name: str) -> None:
    """End of a short CLI run: report an update taken, ask PyPI if a day has passed, and print the line on stderr. Never raises."""
    try:
        if command_name == "mcp":
            return
        note_acted()
        if command_name in QUIET_COMMANDS or in_ci() or not enabled():
            return
        refresh()
        line = cli_line()
        if line:
            print(line, file=sys.stderr)
    except Exception:                   # noqa: BLE001
        return
