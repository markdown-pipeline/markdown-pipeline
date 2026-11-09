"""``mdpipeline update`` on Windows (MD-183).

A running program's files are locked on Windows. ``uv tool install --force`` started by the app's own python.exe failed with "Access is denied" and left a
BROKEN install (found on the real Windows VM, MD-184). So on Windows the app does not touch its own folder: it writes a small PowerShell helper, starts it
detached, asks background sync to stop, and exits. The helper waits until no process runs from the tool folder, runs the installer, and brings background
sync back (``schedule background restart``, MD-204). If something still holds the folder after ``WAIT_SECONDS`` it changes nothing and says so in the log.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import autostart, background
from .vault import data_dir

WAIT_SECONDS = 120          # how long the helper waits for the app and its background process to let go of the tool folder
LOG_NAME = "update.log"
SCRIPT_NAME = "update-helper.ps1"
STARTED_TEXT = (          # words reviewed by marketing: MD-183 comment 11360
    "Update started. It is not finished yet: it takes about a minute, and begins once this program has closed.\n"
    'Close any other Markdown Pipeline window (including "mdpipeline start"), or the update waits and then gives up after 2 minutes without changing anything.\n'
    'The log says "Updated." when it is done: {log}\n'
    "Background sync, if it is on, pauses and restarts by itself.")


def _quote(text: str) -> str:
    """A PowerShell single-quoted string: nothing inside it is ever interpreted."""
    return "'" + str(text).replace("'", "''") + "'"


def helper_script(*, command: list[str], watch: list[str], log: str, restart: list[str], wait_seconds: int = WAIT_SECONDS) -> str:
    """The PowerShell text of the helper. ``watch`` are paths (a folder or an exe): a process whose program file starts with one of them is still using the app."""
    from .lifecycle import INSTALL_COMMAND_WINDOWS

    run = lambda argv: "& " + " ".join(_quote(a) for a in argv)          # noqa: E731
    return f"""# Markdown Pipeline update helper (MD-183): started by `mdpipeline update`, which then exits. Safe to delete.
$Watch = @({", ".join(_quote(w) for w in watch)})
$Log = {_quote(log)}
$Wait = {int(wait_seconds)}
function Write-Log($Message) {{ Add-Content -Path $Log -Value ("{{0}}  {{1}}" -f (Get-Date -Format 's'), $Message) }}
function Test-InUse {{
  foreach ($p in Get-Process -ErrorAction SilentlyContinue) {{
    $path = $null
    try {{ $path = $p.Path }} catch {{ }}
    if ($path) {{ foreach ($w in $Watch) {{ if ($path.StartsWith($w, [StringComparison]::OrdinalIgnoreCase)) {{ return $true }} }} }}
  }}
  return $false
}}
Write-Log 'Update helper started.'
$deadline = (Get-Date).AddSeconds($Wait)
while ((Test-InUse) -and ((Get-Date) -lt $deadline)) {{ Start-Sleep -Milliseconds 500 }}
$code = 0
if (Test-InUse) {{
  Write-Log 'Not updated: Markdown Pipeline was still running after 2 minutes, so nothing was changed. Close it (including any open "mdpipeline start" window), then run: mdpipeline update'
  $code = 1
}} else {{
  {run(command)}
  $installed = $LASTEXITCODE
  if ($installed -eq 0) {{ Write-Log 'Updated.' }}
  else {{
    Write-Log "The update failed (installer exit $installed). Markdown Pipeline may not start now. To repair it, run: " + {_quote(INSTALL_COMMAND_WINDOWS)}
    $code = 1
  }}
}}
{run(restart)}
if ($LASTEXITCODE -eq 0) {{ Write-Log 'Background sync restarted (if it is on).' }}
else {{ Write-Log 'Could not restart background sync. If you use it, run: mdpipeline schedule background restart' }}
exit $code
"""


def start(command: list[str]) -> Path:
    """Write the helper, start it detached, then ask background sync to stop (the helper brings it back). Returns the log path. Raises ``OSError`` when the
    helper could not be started; then nothing was stopped or changed."""
    folder = Path(data_dir())
    folder.mkdir(parents=True, exist_ok=True)
    log, script = folder / LOG_NAME, folder / SCRIPT_NAME
    shim = shutil.which("mdpipeline")
    text = helper_script(command=command, watch=[sys.prefix, *([shim] if shim else [])], log=str(log),
                         restart=[shim or "mdpipeline", "schedule", "background", "restart"])
    script.write_text(text, encoding="utf-8" if text.isascii() else "utf-8-sig")          # Windows PowerShell 5.1 reads a BOM-less file as the ANSI code page
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-File", str(script)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **autostart.detached_kwargs())
    background.stop()
    return log
