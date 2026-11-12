"""MD-229 part B: on Linux/macOS a ``PermissionError`` on a write, a delete or a schedule save gets marketing's plain sentence (MM review 2026-11-11, used verbatim).

Real read-only folders, no simulation. Other ``OSError``s keep their "write failed: ..." path, and nothing is retried (part A)."""
from __future__ import annotations

import os

import pytest

from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.schedulestore import SchedulerStore, ScheduleSaveError
from mdpipeline.state import State
from mdpipeline.writer import SafeWriter

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the non-Windows behavior")


@pytest.fixture
def read_only():
    """``read_only(path)`` makes the folder read-only until the test ends; skips when the user ignores folder permissions (root)."""
    locked = []

    def lock(path):
        path.chmod(0o500)
        locked.append(path)
        if os.access(path, os.W_OK):
            pytest.skip("running as a user who ignores folder permissions")
    yield lock
    for path in locked:
        path.chmod(0o700)


def wrote(name, folder):
    return f"{name} wasn't written because you don't have permission to write to {folder}. Make that folder writable for your user, then sync again."


def removed(name, folder):
    return f"{name} wasn't removed because you don't have permission to change files in {folder}. Make that folder writable for your user, then sync again."


def test_a_write_into_a_read_only_folder_says_the_permission_and_names_the_folder(brain, read_only):
    (brain.root / "raw/p").mkdir(parents=True)
    read_only(brain.root / "raw/p")
    with pytest.raises(OSError) as err:
        SafeWriter(brain).write("raw/p/a.md", b"new")
    assert str(err.value) == wrote("a.md", brain.root / "raw/p")


def test_a_delete_in_a_read_only_folder_says_the_permission_and_names_the_folder(brain, read_only):
    (brain.root / "raw/p").mkdir(parents=True)
    (brain.root / "raw/p/a.md").write_bytes(b"old")
    read_only(brain.root / "raw/p")
    with pytest.raises(OSError) as err:
        SafeWriter(brain).delete("raw/p/a.md")
    assert str(err.value) == removed("a.md", brain.root / "raw/p")
    assert (brain.root / "raw/p/a.md").read_bytes() == b"old"


def test_a_new_subfolder_that_cannot_be_created_names_the_folder_that_is_not_writable(brain, read_only):
    (brain.root / "raw").mkdir(parents=True, exist_ok=True)
    read_only(brain.root / "raw")
    with pytest.raises(OSError) as err:
        SafeWriter(brain).write("raw/p/a.md", b"new")
    assert str(err.value) == wrote("a.md", brain.root / "raw")


def test_a_sync_reports_the_permission_text_keeps_the_file_tracked_and_the_next_sync_writes_it(brain, add_pipeline, make_source, read_only):
    src = make_source("docs", {"a.md": "one\n", "b.md": "one\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    (src / "a.md").write_text("two\n")
    folder = brain.root / "raw/p/docs"
    read_only(folder)
    res = sync_pipeline(brain, "p", trigger="cli")
    failed = [i for i in res.plan.items() if i.error]
    assert failed and all(i.reason == wrote(os.path.basename(i.target), folder) for i in failed)
    assert any(t.endswith("a.md") for t in State.load(brain).pipeline("p").files)          # still tracked: never counted as deleted
    folder.chmod(0o700)
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    assert "two" in (folder / "a.md").read_text()


def test_another_os_error_keeps_the_write_failed_text(brain, add_pipeline, make_source, monkeypatch):
    src = make_source("docs", {"a.md": "one\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    real = os.replace

    def replace(a, b):
        if str(b).endswith("a.md"):
            raise OSError(28, "No space left on device")
        return real(a, b)
    monkeypatch.setattr(os, "replace", replace)
    res = sync_pipeline(brain, "p", trigger="cli")
    (failed,) = [i for i in res.plan.items() if i.error]
    assert failed.reason.startswith("write failed: ") and "permission" not in failed.reason


# --- the schedule -------------------------------------------------------------------------------------------------
def test_a_schedule_that_cannot_be_saved_for_permission_names_the_settings_folder_and_keeps_the_old_one(tmp_path, monkeypatch, read_only):
    monkeypatch.setenv("MDPIPELINE_DATA_DIR", str(tmp_path))
    store = SchedulerStore()
    with store.update() as data:
        data["runtime"]["n"] = 1
    read_only(tmp_path)
    with pytest.raises(ScheduleSaveError) as err:
        with store.update() as data:
            data["runtime"]["n"] = 2
    assert str(err.value) == ("Markdown Pipeline couldn't save the schedule because it doesn't have permission to write to its settings folder, "
                              f"{store._path.parent}. Your previous schedule is unchanged. Make that folder writable for your user, then save again.")
    assert store.read()["runtime"]["n"] == 1
