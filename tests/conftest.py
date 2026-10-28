"""Shared fixtures: a real temporary brain and source folders on the real filesystem."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import leakguard
import winsupport

REAL_HOME = Path.home()                       # captured before any test monkeypatches HOME
_real_state_seen = [leakguard.real_state(REAL_HOME)]        # the real home as the previous test left it: a leak fails the test that made it, not every test after it


@pytest.fixture(autouse=True)
def no_leaks(request):
    """MD-169: a test must not leave an `mdpipeline background` process running or touch the real user's home (scheduler registry,
    login items, data dir). The leaked process is killed, then the test fails with its name. See tests/leakguard.py."""
    before = leakguard.background_pids()
    yield
    problems = []
    leaked = leakguard.leaked_since(before)
    if leaked:
        leakguard.kill(leaked)
        problems.append(f"left {len(leaked)} `mdpipeline background` process(es) running (pids {sorted(leaked)}); "
                        "set MDPIPELINE_NO_BACKGROUND_START=1 or stop them in teardown")
    now = leakguard.real_state(REAL_HOME)
    problems += leakguard.real_state_changes(_real_state_seen[0], now)
    _real_state_seen[0] = now
    if problems:
        pytest.fail(f"{request.node.nodeid} leaked: " + "; ".join(problems), pytrace=False)


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """The app's data directory (the vault lives there) and its keychain are fakes for every test: nothing real is touched."""
    from fake_keychain import FakeKeychain
    from mdpipeline import vault

    data = tmp_path / "appdata"
    monkeypatch.setenv("MDPIPELINE_DATA_DIR", str(data))
    keychain = FakeKeychain()
    from mdpipeline import github_signin

    monkeypatch.setattr(github_signin, "shipped_github_app", lambda: {"client_id": "", "slug": ""})     # tests start with no shipped GitHub App (MD-77)
    vault.reset_session()
    vault.set_keychain(keychain)
    # MD-115: no test can reach the analytics host (no transport), and the one-time first-run notices count as already shown so
    # they do not appear in the output of unrelated tests. Tests of those notices (test_cli_usage.py) reset the file.
    from mdpipeline import usage

    monkeypatch.setattr(usage, "get_transport", lambda: None)
    monkeypatch.setenv("MDPIPELINE_POSTHOG_KEY", "")        # also for subprocesses (the MCP server, `python -m mdpipeline`): no key = no transport
    usage.cancel_pending_flush()
    # MD-121: the first sync turns the background process on. No test may write a real login item or start a real process.
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("MDPIPELINE_NO_BACKGROUND_START", "1")
    from mdpipeline import autostart

    monkeypatch.setattr(autostart, "_run", lambda *args: None)       # MD-165: no test reaches the real systemctl/loginctl (None = not available)
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")      # MD-154: a test run is ours; a real user's state is tested in test_usage_internal.py
    data.mkdir(parents=True, exist_ok=True)
    (data / "usage.json").write_text('{"notices": ["cli_ask", "cli_hint"]}', encoding="utf-8")
    yield type("AppData", (), {"dir": data, "keychain": keychain})
    vault.reset_session()
    vault.set_keychain(None)


@pytest.fixture
def brain_dir(tmp_path: Path) -> Path:
    """An empty directory that has a raw/ and a wiki/ folder (not yet registered)."""
    root = tmp_path / "brain"
    (root / "raw").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "work").mkdir()
    return root


def write(path: Path, text: str, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")   # LF on every OS: the app writes LF bytes, and tests compare bytes
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def make_source(tmp_path: Path):
    """Factory: make_source('name', {'a.md': 'text', 'sub/b.md': 'text'}) -> Path."""

    def _make(name: str, files: dict[str, str] | None = None) -> Path:
        root = tmp_path / "sources" / name
        root.mkdir(parents=True, exist_ok=True)
        for rel, text in (files or {}).items():
            write(root / rel, text)
        return root

    return _make


@pytest.fixture
def brain(brain_dir):
    from mdpipeline.brain import init_brain

    return init_brain(brain_dir)


@pytest.fixture
def add_pipeline(brain):
    """add_pipeline(Pipeline) saves it to the brain's config without validation (tests may be hostile)."""
    from mdpipeline.config import load_config, save_config

    def _add(pipeline):
        cfg = load_config(brain)
        cfg.pipelines.append(pipeline)
        save_config(brain, cfg)
        return pipeline

    return _add


def tree_snapshot(*roots: Path) -> dict:
    """Every path under the roots with bytes (files) and mtime_ns: for byte-identical assertions."""
    out: dict = {}
    for root in roots:
        out[str(root)] = root.stat().st_mtime_ns
        for p in sorted(root.rglob("*")):
            st = p.lstat()
            data = p.read_bytes() if p.is_file() and not p.is_symlink() else None
            out[str(p)] = (data, st.st_mtime_ns)
    return out


@pytest.fixture
def gh(tmp_path, monkeypatch):
    """A brain whose target is a fake GitHub repo (no network): ``gh.fake``, ``gh.brain``, ``gh.pipeline(name, files)``."""
    from types import SimpleNamespace

    from fake_github import FakeGitHub
    from mdpipeline import github_api
    from mdpipeline.brain import init_brain
    from mdpipeline.config import Pipeline, load_config, save_config
    from mdpipeline.targetconfig import GitHubSpec

    token = "ghp_SECRETSECRETSECRET"
    fake = FakeGitHub(repo="acme/brain", branch="main", token=token, files={"wiki/w.md": b"W", "README.md": b"hi"})
    github_api.set_transport(fake)
    monkeypatch.setenv("BRAIN_TOKEN", token)
    brain = init_brain(tmp_path / "control", create=True)
    destination = GitHubSpec("acme/brain", "env:BRAIN_TOKEN", "main").to_dict()

    def pipeline(name: str, files: dict[str, str], **rules):
        src = tmp_path / "sources" / name
        src.mkdir(parents=True, exist_ok=True)
        for rel, text in files.items():
            write(src / rel, text)
        cfg = load_config(brain)
        rules.setdefault("destination", dict(destination))
        cfg.pipelines.append(Pipeline.new(name, [str(src)], **rules))
        save_config(brain, cfg)
        return src

    yield SimpleNamespace(fake=fake, brain=brain, pipeline=pipeline, token=token, tmp=tmp_path, destination=destination)
    github_api.set_transport(None)


_NOT_ON_WINDOWS: list[tuple[str, str]] = []


def pytest_collection_modifyitems(config, items):
    """MD-179: on Windows, a test marked `windows_unsupported(reason)` is skipped with that reason and listed in the run summary."""
    for item in items:
        marker = item.get_closest_marker("windows_unsupported")
        if marker is None:
            continue
        reason = winsupport.marker_reason(marker)            # a missing reason fails the whole run, on every platform
        if sys.platform == "win32":
            _NOT_ON_WINDOWS.append((item.nodeid, reason))
            item.add_marker(pytest.mark.skip(reason="Windows: " + reason))


def pytest_terminal_summary(terminalreporter):
    lines = winsupport.summary_lines(_NOT_ON_WINDOWS)
    if lines:
        terminalreporter.write_sep("-", "tests not run on Windows")
        for line in lines:
            terminalreporter.write_line(line)
