"""Updating and uninstalling the app (MD-117): one command each. The app's data (configuration, vault, synced files) lives outside the
program, so replacing the program changes none of it; the first plan after an update is the plan the old version would have made."""
from __future__ import annotations

import subprocess
import sys

PACKAGE = "mdpipeline"
INSTALL_URL = "https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.sh"      # the public install script (MD-131)
INSTALL_COMMAND = f"curl -fsSL {INSTALL_URL} | sh"
INSTALL_PS1_URL = "https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.ps1"      # the Windows installer (MD-183), next to install.sh
INSTALL_COMMAND_WINDOWS = f'powershell -ExecutionPolicy ByPass -c "irm {INSTALL_PS1_URL} | iex"'             # marketing's form (MM-135)


def update_command(method: str) -> list[str] | None:
    """The command that updates this install, or ``None`` when there is none to run (a checkout of the source)."""
    if method == "script":       # installed from a release wheel: `uv tool upgrade` cannot see newer releases, running the installer again can
        if sys.platform == "win32":      # MD-183: there is no `sh`; the same installer in PowerShell, told not to open the app (an update must not open a window)
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-Command",
                    f"$env:MDPIPELINE_NO_OPEN='1'; irm {INSTALL_PS1_URL} | iex; exit $LASTEXITCODE"]
        return ["sh", "-c", INSTALL_COMMAND]
    if method == "uv":
        return ["uv", "tool", "upgrade", PACKAGE]
    if method == "pipx":
        return ["pipx", "upgrade", PACKAGE]
    if method in ("uvx", "registry"):      # MD-197: nothing is installed; clearing uv's copy makes the next `uvx mdpipeline` fetch the newest release
        return ["uv", "cache", "clean", PACKAGE]
    if method in ("pip", "other"):
        return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]
    return None


def uninstall_command(method: str) -> str:
    """The last step, which only the package manager can do (it removes the program that is running)."""
    return {"uvx": "uv cache clean mdpipeline   (uvx keeps only a cached copy of the program; this removes it)", "registry": "uv cache clean mdpipeline   (if you run it with uvx; with pipx: pipx uninstall mdpipeline)",
            "uv": "uv tool uninstall mdpipeline", "script": "uv tool uninstall mdpipeline", "pipx": "pipx uninstall mdpipeline",
            "source": "uv tool uninstall mdpipeline   (and delete your checkout of the source if you no longer need it)"}.get(
        method, f"{sys.executable} -m pip uninstall mdpipeline")


def run_command(command: list[str]) -> int:
    """Run an update command with its output shown to the person. Returns the exit code."""
    return subprocess.run(command).returncode
