"""MD-183: `scripts/install.ps1`, the Windows installer. Marketing's words (MM-135, comment 10800 on MD-183) and product's decision (10803): it installs uv
if missing, installs the wheel of the latest public release, and opens the first screen by the app's full path.

Two kinds of test: the text of the script (what must and must not be in a file that people run with `irm | iex`), and the script RUN under real
PowerShell 7 in a container (`mcr.microsoft.com/powershell`) against the local release stand-in. The container runs Linux, so what only Windows can show
(the uv installer, `.exe` names, Windows PowerShell 5.1, a locked running exe) is for Gate 2 on the VM (MD-184), not for here."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
PS1 = APP / "scripts" / "install.ps1"
IMAGE = "mcr.microsoft.com/powershell:ubuntu-24.04"
TEXT = PS1.read_text(encoding="utf-8") if PS1.exists() else ""


def test_the_script_exists_and_is_plain_ascii():
    """Windows PowerShell 5.1 reads a BOM-less file as the ANSI code page, so anything outside ASCII could change meaning."""
    assert PS1.exists()
    assert TEXT.isascii()


def test_it_never_calls_exit():
    """`irm | iex` runs the text inside the person's own PowerShell: an `exit` would close their window. Failures return and set the exit code."""
    code = "\n".join(line.split("#", 1)[0] for line in TEXT.splitlines())
    assert not re.search(r"\bexit\b", code)


@pytest.mark.parametrize("line", [
    "Installing uv (the Python tool manager) from astral.sh ...",
    "Installing Markdown Pipeline ...",
    "Installed. Opening Markdown Pipeline in your browser ...",
    "Next time, open it with:  mdpipeline   (in a new PowerShell window)",
    "Could not look up the latest release at $ReleasesUrl. Check your network and try again.",
    "No release was found at $ReleasesUrl yet. Nothing was installed.",
])
def test_the_text_it_prints_is_marketings_text(line):
    assert line in TEXT


def test_it_honours_the_same_overrides_as_the_linux_installer_plus_one_to_not_open():
    for name in ("MDPIPELINE_SOURCE", "MDPIPELINE_RELEASES_URL", "MDPIPELINE_NO_OPEN"):
        assert name in TEXT
    assert "https://github.com/markdown-pipeline/markdown-pipeline" in TEXT


def test_uv_is_installed_in_a_child_powershell_so_its_exit_cannot_close_the_persons_window():
    """Found on the Windows VM (MD-184): astral's installer refuses the default execution policy and calls `exit`; run through iex that ended the whole session."""
    assert "-ExecutionPolicy ByPass" in TEXT and "Get-Process -Id $PID" in TEXT
    assert "Invoke-RestMethod -Uri 'https://astral.sh" not in TEXT        # no longer fed to Invoke-Expression in this session


def test_it_asks_for_nothing_that_needs_administrator_rights():
    for needs_admin in ("RunAs", "-Verb", "HKLM:", "Set-ExecutionPolicy", "msiexec"):
        assert needs_admin not in TEXT


def test_it_records_that_this_install_used_the_script_and_starts_the_app_by_its_full_path():
    assert "install-note" in TEXT and "script" in TEXT
    assert "uv tool dir --bin" in TEXT or "tool dir" in TEXT


# --- the script, run for real under PowerShell 7 ---------------------------------------------------------------------------------
def _docker_ready() -> str | None:
    if not shutil.which("docker"):
        return "docker is not installed"
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        return f"the image {IMAGE} is not pulled (docker pull {IMAGE})"
    if not shutil.which("uv"):
        return "uv is not on PATH on the host (it is mounted into the container)"
    return None


needs_pwsh = pytest.mark.skipif(_docker_ready() is not None, reason=_docker_ready() or "")
needs_pwsh_posix = pytest.mark.windows_unsupported("these tests start a Linux container and a local server; Windows is covered by Gate 2 on the VM")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(["uv", "build", "--wheel", "-o", str(out)], cwd=APP, check=True, capture_output=True)
    (path,) = out.glob("mdpipeline-*.whl")
    return path


@pytest.fixture
def releases(wheel, tmp_path):
    """The local stand-in for the GitHub releases pages (scripts/local_releases_server.py), with the freshly built wheel as the latest release."""
    version = re.search(r"mdpipeline-(.+?)-py3", wheel.name).group(1)
    server = subprocess.Popen([sys.executable, str(APP / "scripts" / "local_releases_server.py"), str(wheel.parent), f"v{version}"],
                              stdout=subprocess.PIPE, text=True)
    base = server.stdout.readline().strip()
    yield base
    server.terminate()
    server.wait(timeout=10)


def run_ps1(base_url: str | None, *, extra_env: dict | None = None, timeout: int = 240) -> subprocess.CompletedProcess:
    """Run install.ps1 as a person does (`irm | iex`, here: the text fed to iex), in a fresh container HOME, with the host's uv mounted. Prints a marker with the exit code."""
    env = {"MDPIPELINE_NO_OPEN": "1", **({"MDPIPELINE_RELEASES_URL": base_url} if base_url else {}), **(extra_env or {})}
    flags = [arg for k, v in env.items() for arg in ("-e", f"{k}={v}")]
    script = ("$env:HOME = (New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid()))).FullName; "
              "$env:PATH = \"$env:HOME/.local/bin:/usr/local/bin:/usr/bin:/bin\"; "
              "Invoke-Expression (Get-Content -Raw /w/install.ps1); "
              "Write-Host \"EXIT=$LASTEXITCODE\"; "
              "if (Get-Command mdpipeline -ErrorAction SilentlyContinue) { Write-Host \"NOTE=$(& mdpipeline install-note script 2>&1 | Out-String)\" }; "
              "Write-Host \"METHOD=$(Get-Content -ErrorAction SilentlyContinue ($env:HOME + '/.local/share/mdpipeline/install-method'))\"")
    cmd = ["docker", "run", "--rm", "--network", "host", *flags, "-v", f"{APP / 'scripts'}:/w:ro", "-v", f"{shutil.which('uv')}:/usr/local/bin/uv:ro", IMAGE,
           "pwsh", "-NoProfile", "-Command", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


@needs_pwsh
@needs_pwsh_posix
def test_it_installs_the_latest_release_from_the_stand_in_and_records_the_script_install(releases):
    done = run_ps1(releases)
    out = done.stdout
    assert "Installing Markdown Pipeline ..." in out and "Installing uv" not in out                  # uv was already there
    assert "Installed." in out and "Next time, open it with:  mdpipeline   (in a new PowerShell window)" in out
    assert "Opening Markdown Pipeline" not in out                                                        # MDPIPELINE_NO_OPEN: the update path must not claim to open it
    assert "EXIT=0" in out and "METHOD=script" in out, done.stdout + done.stderr


@needs_pwsh
@needs_pwsh_posix
def test_a_release_page_that_is_not_a_release_says_nothing_was_installed(tmp_path):
    server = subprocess.Popen([sys.executable, "-c", "import http.server\nclass H(http.server.BaseHTTPRequestHandler):\n"
                               "    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b'no releases')\n"
                               "    do_HEAD = do_GET\n    def log_message(self, *a): pass\n"
                               "s = http.server.HTTPServer(('127.0.0.1', 0), H); print(f'http://127.0.0.1:{s.server_port}', flush=True); s.serve_forever()"],
                              stdout=subprocess.PIPE, text=True)
    try:
        base = server.stdout.readline().strip()
        done = run_ps1(base)
    finally:
        server.terminate()
    assert f"No release was found at {base} yet. Nothing was installed." in done.stderr
    assert "EXIT=1" in done.stdout and "METHOD=" in done.stdout and "METHOD=script" not in done.stdout and "Installing Markdown Pipeline" not in done.stdout


@needs_pwsh
@needs_pwsh_posix
def test_an_unreachable_release_page_says_so_and_installs_nothing():
    done = run_ps1("http://127.0.0.1:9/m")
    assert "Could not look up the latest release at http://127.0.0.1:9/m. Check your network and try again." in done.stderr
    assert "EXIT=1" in done.stdout and "Installing Markdown Pipeline" not in done.stdout


@needs_pwsh
@needs_pwsh_posix
def test_a_source_given_in_the_environment_skips_the_release_lookup():
    done = run_ps1(None, extra_env={"MDPIPELINE_SOURCE": "/w/../nonexistent-package-xyz-123"})
    assert "Could not look up" not in done.stderr and "No release was found" not in done.stderr       # it went straight to installing the given source
    assert "Installing Markdown Pipeline ..." in done.stdout and "EXIT=0" not in done.stdout          # uv could not install the nonsense source, and its code came back


def _port_8765_is_free() -> bool:
    import socket

    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", 8765)) != 0


@needs_pwsh
@needs_pwsh_posix
@pytest.mark.skipif(not _port_8765_is_free(), reason="the app itself serves on its default port 8765 (the container shares the host network), and something here already holds it")
def test_without_the_no_open_switch_it_opens_the_app_by_its_full_path(releases):
    """Not NO_OPEN: the last step is the app itself (`mdpipeline start`), which serves until stopped. It must say where it is serving."""
    flags = ["-e", f"MDPIPELINE_RELEASES_URL={releases}"]
    script = ("$env:HOME = (New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid()))).FullName; "
              "$env:PATH = \"$env:HOME/.local/bin:/usr/local/bin:/usr/bin:/bin\"; $env:BROWSER = 'true'; "
              "Invoke-Expression (Get-Content -Raw /w/install.ps1)")
    cmd = ["docker", "run", "--rm", "--network", "host", *flags, "-v", f"{APP / 'scripts'}:/w:ro", "-v", f"{shutil.which('uv')}:/usr/local/bin/uv:ro", IMAGE,
           "timeout", "120", "pwsh", "-NoProfile", "-Command", script]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seen, deadline = [], time.monotonic() + 110
    try:
        for line in proc.stdout:
            seen.append(line)
            if "Markdown Pipeline UI: http://" in line or time.monotonic() > deadline:
                break
    finally:
        proc.terminate()          # the container's own `timeout 120` ends the app if this process is gone first
    out = "".join(seen)
    assert "Installed. Opening Markdown Pipeline in your browser ..." in out and "Next time, open it with:" in out
    assert re.search(r"Markdown Pipeline UI: http://127\.0\.0\.1:\d+", out), out
