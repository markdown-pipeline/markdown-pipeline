"""The login item that starts the background process (MD-121). Written only in the person's own startup folders and removable
at any time (the opt-out in Settings). ``home``, ``platform`` and ``python`` are parameters so tests never touch the real ones."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.markdownpipeline.background"
COMMAND_ARGS = ["-m", "mdpipeline", "background"]
SERVICE = "mdpipeline-background.service"
LINGER_LINE = ("Background sync stops when you log out, and after a reboot it waits until you log in. To keep it running, run this once: "
               "`loginctl enable-linger $USER` (if it says access denied: `sudo loginctl enable-linger $USER`).")
LINGER_LINE_PLAIN = LINGER_LINE.replace("`", "")      # the CLI does not render code style (MD-167 L1)
HOWS = ("login_item", "user_service", "app_start", "manual")       # how a background process was started (MD-165, background_started)


class AutostartError(Exception):
    """The platform has no supported login item."""


def _kind(platform: str) -> str:
    if platform.startswith("linux"):
        return "linux"
    if platform == "darwin":
        return "darwin"
    if platform in ("win32", "cygwin"):
        return "win32"
    raise AutostartError(f"No login item is supported on {platform}. Start it yourself with: python -m mdpipeline background")


def item_path(*, home: Path | None = None, platform: str | None = None) -> Path:
    """Where the login item lives on this platform."""
    home = Path(home) if home else Path.home()
    kind = _kind(platform or sys.platform)
    if kind == "linux":
        return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "autostart" / "mdpipeline-background.desktop"
    if kind == "darwin":
        return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "mdpipeline-background.vbs"


def detached_kwargs() -> dict:
    """``Popen`` arguments that detach a child from this process and its terminal. POSIX: a new session. Windows (MD-181): no console window
    (CREATE_NO_WINDOW) and its own process group, so closing the terminal or the browser does not end it. The numbers are the Win32 flags
    (``subprocess.CREATE_*`` exist only on Windows)."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000 | 0x00000200}
    return {"start_new_session": True}


def _vbs_quote(text: str) -> str:
    return text.replace('"', '""')


def _content(kind: str, python: str) -> bytes:
    if kind == "linux":
        text = ("[Desktop Entry]\nType=Application\nName=Markdown Pipeline (background sync)\n"
                f"Exec={python} {' '.join(COMMAND_ARGS)} --started-by login_item\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
        return text.encode("utf-8")
    if kind == "darwin":
        return plistlib.dumps({"Label": LABEL, "ProgramArguments": [python, *COMMAND_ARGS, "--started-by", "login_item"], "RunAtLoad": True, "KeepAlive": True})
    # MD-181: a .cmd runs inside a console window; wscript runs this with no window at all (style 0) and does not wait. The command line is
    # a VBScript string, so each quote is doubled once for the command line and once more for the string.
    command = f'"{python}" {" ".join(COMMAND_ARGS)} --started-by login_item'
    return (f'CreateObject("WScript.Shell").Run "{_vbs_quote(command)}", 0, False\r\n').encode("utf-8")


def enable(*, home: Path | None = None, platform: str | None = None, python: str | None = None) -> Path:
    """Create (or refresh) the login item. Idempotent. Returns its path."""
    kind = _kind(platform or sys.platform)
    path = item_path(home=home, platform=platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_content(kind, python or sys.executable))
    return path


def refresh_if_outdated(*, home: Path | None = None, platform: str | None = None, python: str | None = None) -> bool:
    """MD-171: rewrite an EXISTING login item whose content differs from what ``enable`` writes today (an item made before ``--started-by``
    existed makes the background process report ``how=manual``). Never creates one, and leaves a current one untouched (no write at all).
    Returns whether it rewrote. A platform without login items, or an unreadable item, changes nothing."""
    try:
        kind = _kind(platform or sys.platform)
        path = item_path(home=home, platform=platform)
        wanted = _content(kind, python or sys.executable)
        if not path.is_file() or path.read_bytes() == wanted:
            return False
        path.write_bytes(wanted)
    except (AutostartError, OSError):
        return False
    return True


def disable(*, home: Path | None = None, platform: str | None = None) -> bool:
    """Remove the login item. Returns whether there was one."""
    path = item_path(home=home, platform=platform)
    if not path.exists():
        return False
    path.unlink()
    return True


def is_enabled(*, home: Path | None = None, platform: str | None = None) -> bool:
    return item_path(home=home, platform=platform).exists()


def start_now(python: str | None = None, how: str = "app_start") -> bool:
    """Start the background process right away, detached (the login item only takes effect at the next login). Best effort."""
    if os.environ.get("MDPIPELINE_NO_BACKGROUND_START"):        # a kill switch for tests, CI and people who want only the login item
        return False
    try:
        subprocess.Popen([python or sys.executable, *COMMAND_ARGS, "--started-by", how], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **detached_kwargs())
        return True
    except OSError:
        return False


# --- MD-165: a systemd user service, so a Linux box with no desktop login still gets the background process back ----------------
def _run(*args: str):
    """Run ``systemctl --user ...`` / ``loginctl ...``. ``None`` when there is no such program or systemd does not answer. Tests replace this."""
    try:
        return subprocess.run(list(args), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def _systemd_ok() -> bool:
    result = _run("systemctl", "--user", "show-environment")
    return result is not None and result.returncode == 0


def service_path(*, home: Path | None = None) -> Path:
    home = Path(home) if home else Path.home()
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "systemd" / "user" / SERVICE


def enable_user_service(*, home: Path | None = None, platform: str | None = None, python: str | None = None) -> bool:
    """Linux with a working ``systemctl --user``: install and enable a user service that runs the background process whenever the
    user's systemd manager starts (the first login by any means, SSH included; at boot too when lingering is on). Returns whether it did.
    Never runs a privileged command."""
    if not (platform or sys.platform).startswith("linux") or not _systemd_ok():
        return False
    path = service_path(home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Unit]\nDescription=Markdown Pipeline (background sync)\n\n[Service]\n"
                    f"ExecStart={python or sys.executable} {' '.join(COMMAND_ARGS)} --started-by user_service\n"
                    "Restart=on-failure\nRestartSec=30\n\n[Install]\nWantedBy=default.target\n", encoding="utf-8")
    _run("systemctl", "--user", "daemon-reload")
    result = _run("systemctl", "--user", "enable", SERVICE)
    return result is not None and result.returncode == 0


def disable_user_service(*, home: Path | None = None) -> bool:
    """Undo ``enable_user_service``: disable it and remove the unit. Returns whether there was one."""
    path = service_path(home=home)
    if not path.exists():
        return False
    _run("systemctl", "--user", "disable", SERVICE)
    path.unlink()
    _run("systemctl", "--user", "daemon-reload")
    return True


def linger_state() -> str:
    """MD-168: ``yes`` / ``no`` / ``unknown`` (not Linux, no systemd, no answer). Reads only (``loginctl show-user``): the app never turns it on."""
    if not sys.platform.startswith("linux") or not _systemd_ok():
        return "unknown"
    result = _run("loginctl", "show-user", os.environ.get("USER") or str(os.getuid()), "--property=Linger")
    out = (result.stdout or "") if result is not None and result.returncode == 0 else ""
    return "no" if "Linger=no" in out else "yes" if "Linger=yes" in out else "unknown"


def linger_hint() -> bool:
    """Whether Settings should explain ``loginctl enable-linger``: systemd works here but the user's services only start once they log in."""
    return linger_state() == "no"


def desktop_session() -> bool:
    """Whether this process runs inside a desktop session (a display is set). A server over SSH has none."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _graphical_session_for_user() -> str:
    """``yes`` / ``no`` / ``unknown`` from logind (read only: ``loginctl list-sessions`` and ``show-session``). A graphical session is one whose
    Type is x11, wayland or mir and whose Name is this user (another user's does not count)."""
    listing = _run("loginctl", "list-sessions", "--no-legend")
    if listing is None or listing.returncode != 0:
        return "unknown"
    user = os.environ.get("USER") or str(os.getuid())
    for line in (listing.stdout or "").splitlines()[:50]:
        parts = line.split()
        if not parts:
            continue
        info = _run("loginctl", "show-session", parts[0], "-p", "Type", "-p", "Name")
        if info is None or info.returncode != 0:
            continue
        fields = dict(row.split("=", 1) for row in (info.stdout or "").splitlines() if "=" in row)
        if fields.get("Name") == user and fields.get("Type") in ("x11", "wayland", "mir"):
            return "yes"
    return "no"


def desktop_state() -> str:
    """MD-168: ``yes`` / ``no`` / ``unknown``. Linux only (elsewhere ``unknown``: a Mac has no DISPLAY). ``yes`` when this process has a display,
    or logind reports a graphical session for this user (a systemd user service has no DISPLAY even on a desktop). ``no`` when logind was asked
    and found none. ``unknown`` when there is no display and logind cannot be asked."""
    if not sys.platform.startswith("linux"):
        return "unknown"
    if desktop_session():
        return "yes"
    return _graphical_session_for_user()


def linger_notice() -> bool:
    """MD-166: tell the person where they are (first run, Home, the CLI) when linger is off on a box with no desktop session."""
    return desktop_state() == "no" and linger_hint()
