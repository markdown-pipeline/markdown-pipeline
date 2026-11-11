"""MD-229 part A: only Windows treats a refused replace/unlink as "the file is open in another program".

On Linux and macOS replacing or deleting an open file succeeds, so a ``PermissionError`` there is real (a read-only folder). It is raised at once, as in
0.1.20: no ~1 s retry per file and no "open in another program" text."""
from __future__ import annotations

import os
import time

import pytest

from mdpipeline import fsretry
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.writer import FileInUse, SafeWriter

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the non-Windows behavior")


def test_retry_is_off_where_the_os_is_not_windows():
    assert fsretry.retry_enabled() is False


def test_a_refused_operation_is_run_once_and_raised_at_once(monkeypatch):
    slept, calls = [], {"n": 0}
    monkeypatch.setattr(fsretry.time, "sleep", slept.append)

    def op():
        calls["n"] += 1
        raise PermissionError(13, "Permission denied")
    with pytest.raises(PermissionError):
        fsretry.retry_permission(op)
    assert calls["n"] == 1 and slept == []


def test_a_read_only_target_folder_raises_the_original_permission_error_not_file_in_use(brain):
    (brain.root / "raw/p").mkdir(parents=True)
    (brain.root / "raw/p/a.md").write_bytes(b"old")
    (brain.root / "raw/p").chmod(0o500)
    try:
        if os.access(brain.root / "raw/p", os.W_OK):
            pytest.skip("running as a user who ignores folder permissions")
        with pytest.raises(PermissionError) as err:
            SafeWriter(brain).write("raw/p/a.md", b"new")
        assert not isinstance(err.value, FileInUse) and "open in another program" not in str(err.value)
        with pytest.raises(PermissionError) as err2:
            SafeWriter(brain).delete("raw/p/a.md")
        assert not isinstance(err2.value, FileInUse) and "open in another program" not in str(err2.value)
    finally:
        (brain.root / "raw/p").chmod(0o700)


def test_five_files_in_a_read_only_folder_do_not_wait_a_second_each(brain):
    (brain.root / "raw/p").mkdir(parents=True)
    (brain.root / "raw/p").chmod(0o500)
    try:
        if os.access(brain.root / "raw/p", os.W_OK):
            pytest.skip("running as a user who ignores folder permissions")
        start = time.monotonic()
        for i in range(5):
            with pytest.raises(PermissionError):
                SafeWriter(brain).write(f"raw/p/f{i}.md", b"x")
        assert time.monotonic() - start < 1.0
    finally:
        (brain.root / "raw/p").chmod(0o700)
