"""MD-228: writing a Markdown file, the sync state, the vault or the config never fails with ``WinError 5`` because another program has the file open.

On Windows ``os.replace`` onto a file that an editor (Obsidian, VS Code, an AI agent) has open is refused for as long as it stays open. The refusal is simulated
here so Linux CI proves the logic; ``test_a_file_held_open_like_an_editor_does_...`` does it for real and runs on mdp-win-1."""
from __future__ import annotations

import os
import sys

import pytest

from fake_keychain import FakeKeychain
from mdpipeline import fsretry, state as state_mod
from mdpipeline.brain import init_brain
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
from mdpipeline.vault import Vault
from mdpipeline.writer import FileInUse, SafeWriter


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(fsretry.time, "sleep", lambda s: None)
    monkeypatch.setattr(fsretry, "retry_enabled", lambda: True)          # MD-229: the refusal is simulated as Windows


def refuse(monkeypatch, name: str, times: int = 10**6):
    """Make ``os.replace`` refuse (as Windows does for an open file) when the destination's file name is ``name``."""
    real, calls = os.replace, {"n": 0, "on": True}

    def replace(src, dst):
        if calls["on"] and os.path.basename(str(dst)) == name:
            calls["n"] += 1
            if calls["n"] <= times:
                raise PermissionError(5, "Access is denied", str(dst))
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    return calls


# --- writer.py: an output file open in another program must not fail the sync ---------------------------------------
def test_a_write_that_is_refused_for_a_moment_is_retried_and_lands(brain, monkeypatch):
    calls = refuse(monkeypatch, "a.md", times=3)
    SafeWriter(brain).write("raw/p/a.md", b"new")
    assert calls["n"] == 4 and (brain.root / "raw/p/a.md").read_bytes() == b"new"


def test_a_write_that_stays_refused_says_the_file_is_open_in_another_program_and_leaves_the_old_file(brain, monkeypatch):
    (brain.root / "raw/p").mkdir(parents=True)
    (brain.root / "raw/p/a.md").write_bytes(b"old")
    refuse(monkeypatch, "a.md")
    with pytest.raises(FileInUse) as err:
        SafeWriter(brain).write("raw/p/a.md", b"new")
    assert str(err.value) == "a.md is open in another program, so it wasn't written. Close it there, and the next sync will try again."     # marketing's text, verbatim
    assert "WinError" not in str(err.value) and "Access is denied" not in str(err.value)
    assert (brain.root / "raw/p/a.md").read_bytes() == b"old"
    assert not list((brain.root / ".mdpipeline").rglob("*.tmp"))             # no temp file left behind


def test_a_sync_marks_the_busy_file_failed_keeps_going_never_deletes_it_and_retries_it_next_time(brain, add_pipeline, make_source, monkeypatch):
    src = make_source("docs", {"a.md": "one\n", "b.md": "one\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    (src / "a.md").write_text("two\n")
    (src / "b.md").write_text("two\n")
    busy = refuse(monkeypatch, "a.md")
    res = sync_pipeline(brain, "p", trigger="cli")                                # does not raise
    (failed,) = [i for i in res.plan.items() if i.error]
    assert failed.target.endswith("a.md") and failed.reason == "a.md is open in another program, so it wasn't written. Close it there, and the next sync will try again." and "WinError" not in failed.reason
    assert "two" in (brain.root / "raw/p/docs/b.md").read_text()                  # the other file was written
    assert "one" in (brain.root / "raw/p/docs/a.md").read_text()                  # the busy one is untouched, and still there
    assert any(t.endswith("a.md") for t in State.load(brain).pipeline("p").files)      # still tracked: never counted as deleted
    busy["on"] = False
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    assert "two" in (brain.root / "raw/p/docs/a.md").read_text()


# --- state.py: a failed replace leaves the previous state, and the next sync carries on ---------------------------------
def test_a_state_save_that_stays_refused_leaves_the_previous_state_and_no_temp_file(brain, add_pipeline, make_source, monkeypatch):
    src = make_source("docs", {"a.md": "one\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    path = brain.config_dir / "state.json"
    before = path.read_bytes()
    st = State.load(brain)
    st.pipeline("p").files.clear()
    busy = refuse(monkeypatch, "state.json")
    with pytest.raises(PermissionError):
        st.save()
    busy["on"] = False
    assert path.read_bytes() == before and State.load(brain).pipeline("p").files
    assert not list(brain.config_dir.glob("*.tmp")) and not list((brain.config_dir / "backups").glob("*.tmp"))


def test_a_state_save_that_is_refused_for_a_moment_lands(brain, add_pipeline, make_source, monkeypatch):
    src = make_source("docs", {"a.md": "one\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    calls = refuse(monkeypatch, "state.json", times=2)
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok" and calls["n"] >= 3


# --- vault.py: a failed replace never loses a secret -----------------------------------------------------------------------
def test_a_vault_save_that_stays_refused_keeps_the_secrets_already_stored(app_data, monkeypatch):
    v = Vault(app_data.dir / "vault.json", app_data.keychain)
    sid = v.add("github-token", "t", "ghp_SECRETVALUE123456789")
    before = (app_data.dir / "vault.json").read_bytes()
    busy = refuse(monkeypatch, "vault.json")
    with pytest.raises(PermissionError):
        v.add("github-token", "t2", "ghp_OTHERVALUE123456789")
    busy["on"] = False
    assert (app_data.dir / "vault.json").read_bytes() == before and v.get(sid) == "ghp_SECRETVALUE123456789"
    assert not list(app_data.dir.glob("*.tmp"))


# --- real Windows: a file held open the way an editor does ----------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace an open file")
def test_a_file_held_open_like_an_editor_does_fails_that_file_only_and_the_next_sync_writes_it(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "one\n", "b.md": "one\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    (src / "a.md").write_text("two\n")
    (src / "b.md").write_text("two\n")
    with open(brain.root / "raw/p/docs/a.md", "rb"):                              # an editor / Obsidian has the file open
        res = sync_pipeline(brain, "p", trigger="cli")
        assert [i.target for i in res.plan.items() if i.error] == ["raw/p/docs/a.md"]
        assert [i for i in res.plan.items() if i.error][0].reason == "a.md is open in another program, so it wasn't written. Close it there, and the next sync will try again."
    assert "two" in (brain.root / "raw/p/docs/b.md").read_text()
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    assert "two" in (brain.root / "raw/p/docs/a.md").read_text()


def test_a_locked_file_that_should_be_removed_says_it_wasn_t_removed_in_marketings_words(brain, monkeypatch):
    (brain.root / "raw/p").mkdir(parents=True)
    (brain.root / "raw/p/a.md").write_bytes(b"old")
    real = type(brain.root).unlink

    def unlink(self, *a, **k):
        if self.name == "a.md":
            raise PermissionError(5, "Access is denied", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(type(brain.root), "unlink", unlink)
    with pytest.raises(FileInUse) as err:
        SafeWriter(brain).delete("raw/p/a.md")
    assert str(err.value) == "a.md is open in another program, so it wasn't removed. Close it there, and the next sync will try again."
    assert (brain.root / "raw/p/a.md").read_bytes() == b"old"
