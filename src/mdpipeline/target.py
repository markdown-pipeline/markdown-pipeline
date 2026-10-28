"""The target contract (MD-60): where a brain's ``raw/`` files and the pipeline state live.

The engine plans, guards, writes, owns and recovers only through a ``Target``. The local directory
(``LocalTarget``) and a GitHub repository (``mdpipeline.github_target``) are two implementations. See
``docs/target-contract.md`` for the full contract and its error semantics.

Whatever the target, the engine itself enforces the write boundary (``boundary.check_write_target``) before it calls
``write`` or ``delete``; a target never decides what may be written, only how.
"""
from __future__ import annotations

import os
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from .boundary import PROTECTED_FOLDERS
from .brain import Brain
from .writer import SafeWriter


class TargetError(Exception):
    """Reading or writing the target failed. The message is user-facing. A failure is NEVER read as "file deleted"."""

    category = "error"


class TargetAuthError(TargetError):
    category = "auth"


class TargetRateLimited(TargetError):
    category = "rate-limited"


class TargetUnavailable(TargetError):
    category = "network"


class TargetProtected(TargetError):
    category = "protected-branch"


class ConcurrentChange(TargetError):
    """Someone else changed the target while the sync was planning. Nothing was written; plan again."""

    category = "concurrent-change"


class CredentialConflict(TargetError):
    """Two pipelines on one repository and branch name different credentials (MD-97): the sync is blocked, nothing is written."""
    category = "credential-conflict"


class TargetBusy(TargetError):
    """The target kept changing under the sync, retries are exhausted. Nothing was written."""

    category = "head-moved"


@dataclass(frozen=True)
class FileRead:
    """Result of reading one brain path. ``kind``: absent, file, or other (symlink, directory, ...)."""

    kind: Literal["absent", "file", "other"]
    data: bytes | None = None


@dataclass(frozen=True)
class CommitInfo:
    """What a ``commit`` produced (a repository target): shown in the sync result, the log and the UI."""

    sha: str
    url: str
    message: str = ""

    def to_dict(self) -> dict:
        return {"sha": self.sha, "url": self.url}


class Target(ABC):
    """The contract. ``begin`` -> reads -> ``write``/``delete``/``write_state`` -> ``commit``."""

    kind = "target"
    atomic = False            # True: staged changes become visible all at once at ``commit``
    state_in_repo = False     # True: the state is stored in the target and shared by every machine

    @abstractmethod
    def describe(self) -> str:
        """A short label for ``status`` and the UI. Never contains a credential."""

    def begin(self) -> None:
        """Start an operation on a fresh view of the target (a repository target re-reads its head)."""

    def prefetch(self, rels: list[str]) -> None:
        """Optional hint: these paths will be read soon."""

    @abstractmethod
    def read_file(self, rel: str) -> FileRead:
        """Read a brain path. Raises ``TargetError`` when it cannot be told (never returns 'absent' on failure)."""

    @abstractmethod
    def list_markdown(self) -> list[str]:
        """Every ``.md`` path under ``raw/`` (POSIX, brain-relative). Raises ``TargetError`` on failure."""

    @abstractmethod
    def read_state(self) -> bytes | None:
        """The bytes of the state file, or None when there is none yet."""

    @abstractmethod
    def write_state(self, data: bytes) -> None:
        """Store the state bytes (staged until ``commit`` when ``atomic``)."""

    @abstractmethod
    def write(self, rel: str, data: bytes) -> None:
        """Write one file under ``raw/`` (staged until ``commit`` when ``atomic``)."""

    @abstractmethod
    def delete(self, rel: str) -> None:
        """Delete one file under ``raw/`` (staged until ``commit`` when ``atomic``)."""

    @abstractmethod
    def commit(self, message: str) -> CommitInfo | None:
        """Make the staged changes real, all or nothing when ``atomic``. Returns None when there was nothing to commit.

        Raises ``ConcurrentChange`` when someone else changed the target meanwhile (nothing was written)."""

    def clean_temp(self) -> None:
        """Remove leftovers of an interrupted run."""


class LocalTarget(Target):
    """A directory on this machine: ``raw/`` files through the one ``SafeWriter``, state in ``.mdpipeline/state.json``."""

    kind = "local"

    def __init__(self, brain: Brain) -> None:
        self.brain = brain
        self._writer = SafeWriter(brain)

    def describe(self) -> str:
        return f"local directory {self.brain.root}"

    def read_file(self, rel: str) -> FileRead:
        path = self.brain.root / rel
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            return FileRead("absent")
        if not stat.S_ISREG(mode):
            return FileRead("other")
        try:
            return FileRead("file", path.read_bytes())
        except OSError:
            return FileRead("other")

    def list_markdown(self) -> list[str]:
        out = []
        for root, dirs, files in os.walk(self.brain.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d))
                             and d.casefold() not in PROTECTED_FOLDERS and not d.startswith("."))      # MD-133: the whole output folder, not just raw/
            for name in sorted(files):
                path = os.path.join(root, name)
                if name.endswith(".md") and not os.path.islink(path):
                    out.append(os.path.relpath(path, self.brain.root).replace(os.sep, "/"))
        return out

    def read_state(self) -> bytes | None:
        path = self.brain.config_dir / "state.json"
        if not path.exists():
            return None
        return path.read_bytes()

    def write_state(self, data: bytes) -> None:
        from .state import write_local_state

        write_local_state(self.brain, data)

    def write(self, rel: str, data: bytes) -> None:
        self._writer.write(rel, data)

    def delete(self, rel: str) -> None:
        self._writer.delete(rel)

    def commit(self, message: str) -> CommitInfo | None:
        return None                      # every change is already on disk

    def clean_temp(self) -> None:
        self._writer.clean_temp()


def make_target(brain: Brain) -> Target:
    """The brain's own target: its folder. Pipelines with another destination get theirs from ``destinations.bind_target``."""
    return LocalTarget(brain)
