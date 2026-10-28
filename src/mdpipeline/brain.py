"""The brain: any directory (MD-133: ``raw/`` is no longer required) plus a ``.mdpipeline/`` bookkeeping folder.

The configuration is stored *inside* the brain (``.mdpipeline/config.yaml``), is human readable and
can be committed to git. Runtime files (state, log, lock) sit next to it and are git-ignored.
The bookkeeping folder is not brain content: it is outside ``raw/`` and ``wiki/`` and never read by MCP.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR_NAME = ".mdpipeline"
CONFIG_FILE_NAME = "config.yaml"
RUNTIME_FILES = ("state.json", "log.jsonl", "lock", "backups/", "runtime.json", "tmp/")
EMPTY_CONFIG = "version: 1\npipelines: []\n"


class BrainError(Exception):
    """A brain-level problem the user must fix (message is user-facing)."""


class ConfigReadError(BrainError):
    """The configuration file exists but cannot be read (a runtime problem, not a usage error)."""


@dataclass(frozen=True)
class Brain:
    """A registered brain (all paths absolute and resolved)."""

    root: Path
    _cache: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    @property
    def target(self):
        """Where the brain's content and state live (MD-60): the local directory, or what the configuration names.

        One instance per ``Brain`` object, so the staged changes of a sync and the state it reads are the same object."""
        if "target" not in self._cache:
            from .target import make_target

            self._cache["target"] = make_target(self)
        return self._cache["target"]

    @property
    def content_root(self) -> Path:
        """Where agents read the brain's own files (MCP): the folder itself. Other targets: see ``destinations``."""
        return self.root

    def use_target(self, target) -> None:
        """Replace the target (tests, and runners that bring their own)."""
        self._cache["target"] = target

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def config_dir(self) -> Path:
        return self.root / CONFIG_DIR_NAME

    @property
    def config_path(self) -> Path:
        return self.config_dir / CONFIG_FILE_NAME


def ensure_config_dir(brain: Brain) -> None:
    """Make the ``.mdpipeline/`` folder (with its ``.gitignore``) if it is missing: a directory target gets one on its first write."""
    if not brain.config_dir.is_dir():
        brain.config_dir.mkdir(parents=True, exist_ok=True)
    ignore = brain.config_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("\n".join(RUNTIME_FILES) + "\n", encoding="utf-8")


def init_brain(path: str | Path, create: bool = False) -> Brain:
    """Register ``path`` as the brain. Idempotent: an existing configuration is never touched.

    ``create``: make the directory first. MD-133: any folder is a valid output folder; ``raw/`` is neither required nor created
    (a pipeline whose template starts with ``raw/`` creates it when it first writes)."""
    root = Path(path).expanduser()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise BrainError(f"Directory does not exist: {root}")
    root = root.resolve()
    brain = Brain(root)
    if not brain.config_path.exists():
        brain.config_dir.mkdir(exist_ok=True)
        brain.config_path.write_text(EMPTY_CONFIG, encoding="utf-8")
    ignore = brain.config_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("\n".join(RUNTIME_FILES) + "\n", encoding="utf-8")
    return brain


def find_brain(start: str | Path) -> Brain:
    """Find the registered brain at or above ``start``."""
    current = Path(start).expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_DIR_NAME / CONFIG_FILE_NAME).is_file():
            return Brain(candidate)
    raise BrainError(f"No output folder is set up at or above {current}. Run `mdpipeline init` in the folder where your files go.")
