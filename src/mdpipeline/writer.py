"""The only code that writes to or deletes from the output folder (the brain's folder; MD-133: no forced ``raw/`` prefix).

Every call re-checks the boundary at execution time (not only at configuration time):

* the target must be an allowed relative path (see ``boundary``), inside the output folder, never ``wiki/`` or ``.mdpipeline/``;
* only ``.md`` files are written;
* no path component may be a symlink, and the final file may not be one;
* writes are atomic: data goes to a temp file under ``.mdpipeline/tmp/`` (outside the content), is
  fsynced, then renamed into place, so an interrupted run never leaves a half-written Markdown file.
"""
from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

from .boundary import check_write_target
from .brain import Brain, ensure_config_dir


class WriteRefused(Exception):
    """A write or delete was refused by the boundary (message is user-facing)."""


class SafeWriter:
    def __init__(self, brain: Brain) -> None:
        self.brain = brain
        self.tmp_dir = brain.config_dir / "tmp"

    # -- validation -----------------------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        reason = check_write_target(rel)
        if reason:
            raise WriteRefused(f"Refused {rel!r}: {reason}.")
        root = self.brain.root
        current = root
        segments = rel.split("/")
        for index, seg in enumerate(segments):
            current = current / seg
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                break
            if stat.S_ISLNK(mode):
                raise WriteRefused(f"Refused {rel!r}: '{'/'.join(segments[: index + 1])}' is a symlink.")
            is_last = index == len(segments) - 1
            if is_last and not stat.S_ISREG(mode):
                raise WriteRefused(f"Refused {rel!r}: the target exists and is not a regular file.")
            if not is_last and not stat.S_ISDIR(mode):
                raise WriteRefused(f"Refused {rel!r}: '{'/'.join(segments[: index + 1])}' is not a folder.")
        target = root / rel
        root_real = os.path.realpath(self.brain.root)
        parent_real = os.path.realpath(target.parent)
        if parent_real != root_real and not parent_real.startswith(root_real + os.sep):
            raise WriteRefused(f"Refused {rel!r}: it resolves outside the output folder.")
        return target

    # -- operations -----------------------------------------------------------------
    def write(self, rel: str, data: bytes) -> None:
        target = self._resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._resolve(rel)  # re-check after creating folders
        ensure_config_dir(self.brain)
        self.tmp_dir.mkdir(exist_ok=True)
        tmp = self.tmp_dir / f"{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def delete(self, rel: str) -> None:
        target = self._resolve(rel)
        try:
            target.unlink()
        except FileNotFoundError:
            return
        parent = target.parent
        keep = {self.brain.root, self.brain.raw_dir}              # the output folder itself, and a raw/ that a brain expects
        while parent not in keep and parent.is_relative_to(self.brain.root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def clean_temp(self) -> None:
        """Remove temp files left by an interrupted run."""
        if self.tmp_dir.is_dir():
            for leftover in self.tmp_dir.glob("*.tmp"):
                leftover.unlink(missing_ok=True)
