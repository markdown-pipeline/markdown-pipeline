"""MD-117 AC1/AC4: scripts/install.sh, tested with stub `uv`, `curl` and `uname` so nothing real is installed."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "install.sh"


def stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def box(tmp_path):
    """A PATH holding only the stubs plus the basic tools the script needs, and a log of what the stubs were asked."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("sh", "cat", "dirname", "mkdir", "printf", "echo", "chmod", "tr", "grep"):
        real = subprocess.run(["sh", "-c", f"command -v {tool}"], capture_output=True, text=True).stdout.strip()
        if real:
            os.symlink(real, bin_dir / tool)
    stub(bin_dir, "uname", "echo Linux\n")
    stub(bin_dir, "uv", f'echo "uv $*" >> {log}\n')
    stub(bin_dir, "mdpipeline", f'echo "mdpipeline $*" >> {log}\n')
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": str(bin_dir), "HOME": str(home)}

    def run(**extra):
        return subprocess.run(["/bin/sh", str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True, timeout=60)

    return type("Box", (), {"bin": bin_dir, "log": log, "run": staticmethod(run), "home": home,
                            "calls": property(lambda self: log.read_text().splitlines() if log.exists() else [])})()


REPO = "https://github.com/markdown-pipeline/markdown-pipeline"
WHEEL = f"{REPO}/releases/download/v0.1.7/mdpipeline-0.1.7-py3-none-any.whl"


def releases_curl(box, final=f"{REPO}/releases/tag/v0.1.7", fail=False):
    """A curl that answers the 'where does releases/latest point' question (-w %{url_effective}) and logs every call."""
    answer = "exit 22\n" if fail else f'printf %s "{final}"\n'
    stub(box.bin, "curl", f'echo "curl $*" >> {box.log}\ncase "$*" in *url_effective*) {answer[:-1]} ;; esac\n')


def test_the_script_exists_and_is_posix_sh():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh") and "set -eu" in text


def test_it_installs_the_latest_release_with_uv_records_how_and_says_how_to_open_the_app(box):
    releases_curl(box)
    done = box.run()
    assert done.returncode == 0, done.stderr
    assert f"uv tool install --force --python 3.12 {WHEEL}" in box.calls
    assert "mdpipeline install-note script" in box.calls
    assert "Open it with" in done.stdout and "mdpipeline" in done.stdout


def test_the_source_can_be_pointed_elsewhere(box):
    box.run(MDPIPELINE_SOURCE="/some/local/app")
    assert box.calls[0] == "uv tool install --force --python 3.12 /some/local/app"


def test_running_it_again_is_the_same_command_so_it_doubles_as_a_repair(box):
    releases_curl(box)
    box.run()
    box.run()
    assert [c for c in box.calls if c.startswith("uv tool install")].count(f"uv tool install --force --python 3.12 {WHEEL}") == 2


def test_running_it_after_a_new_release_moves_to_that_release(box):
    releases_curl(box)
    box.run()
    releases_curl(box, final=f"{REPO}/releases/tag/v0.2.0")
    box.run()
    installs = [c for c in box.calls if c.startswith("uv tool install")]
    assert installs[-1].endswith("/releases/download/v0.2.0/mdpipeline-0.2.0-py3-none-any.whl")


def test_it_needs_no_github_login_token_or_invite(box):
    releases_curl(box)
    box.run(GITHUB_TOKEN="should-not-be-used", GH_TOKEN="nor-this")
    text = SCRIPT.read_text(encoding="utf-8")
    assert not any("token" in c.lower() or "authorization" in c.lower() for c in box.calls if c.startswith("curl"))
    assert "GITHUB_TOKEN" not in text and "GH_TOKEN" not in text and "Authorization" not in text


def test_the_releases_location_can_be_pointed_elsewhere_for_a_mirror_or_a_test(box):
    releases_curl(box, final="http://127.0.0.1:9/m/releases/tag/v1.2.3")
    box.run(MDPIPELINE_RELEASES_URL="http://127.0.0.1:9/m")
    assert "uv tool install --force --python 3.12 http://127.0.0.1:9/m/releases/download/v1.2.3/mdpipeline-1.2.3-py3-none-any.whl" in box.calls


def test_when_the_release_cannot_be_found_it_says_so_plainly_and_installs_nothing(box):
    releases_curl(box, fail=True)
    done = box.run()
    assert done.returncode == 1 and "latest release" in done.stderr and not [c for c in box.calls if c.startswith("uv tool install")]


def test_a_repository_without_a_release_yet_is_not_mistaken_for_one(box):
    releases_curl(box, final=f"{REPO}/releases")
    done = box.run()
    assert done.returncode == 1 and "No release" in done.stderr and not [c for c in box.calls if c.startswith("uv tool install")]


def test_without_uv_it_installs_uv_first_from_the_official_installer(box):
    (box.bin / "uv").unlink()
    stub(box.bin, "curl", f'echo "curl $*" >> {box.log}\ncase "$*" in *url_effective*) printf %s "{REPO}/releases/tag/v0.1.7"; exit 0 ;; esac\ncat <<\'EOS\'\nmkdir -p "$HOME/.local/bin"\nprintf \'#!/bin/sh\\necho "uv $*" >> {box.log}\\n\' > "$HOME/.local/bin/uv"\nchmod +x "$HOME/.local/bin/uv"\nEOS\n')
    done = box.run()
    assert done.returncode == 0, done.stderr
    assert box.calls[0].startswith("curl") and "astral.sh/uv/install.sh" in box.calls[0]
    assert f"uv tool install --force --python 3.12 {WHEEL}" in box.calls


def test_without_uv_and_without_curl_it_stops_with_a_plain_message_and_installs_nothing(box):
    (box.bin / "uv").unlink()
    done = box.run()
    assert done.returncode == 1 and "uv" in done.stderr and "curl" in done.stderr and box.calls == []


def test_on_windows_it_points_to_wsl_instead_of_half_installing(box):
    stub(box.bin, "uname", "echo MINGW64_NT-10.0\n")
    done = box.run()
    assert done.returncode == 1 and "WSL" in done.stderr and box.calls == []


def test_a_failed_install_is_reported_and_nothing_is_recorded(box):
    releases_curl(box)
    stub(box.bin, "uv", f'echo "uv $*" >> {box.log}\nexit 3\n')
    done = box.run()
    assert done.returncode != 0 and "mdpipeline install-note script" not in box.calls


def test_it_never_asks_for_sudo_or_writes_outside_the_users_home():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "sudo" not in text and "/usr/local" not in text and "/etc/" not in text
