"""Sources. A source turns a location into a list of candidate files: the source-adapter contract (MD-45).

``LocalFolderSource`` is the folder source; a connector (Jira, ...) registers itself with ``register_source_type`` and is
built by ``adapter_for``. The engine depends only on the shapes ``SourceScan`` / ``SourceEntry`` below, never on a concrete
source. See ``docs/source-contract.md``.

Safety rules implemented here:
* the source is opened read-only and never modified;
* symlinks are never followed (a symlinked file is reported as skipped);
* any error while walking marks the whole source ``unreadable`` so files can never look "deleted".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from typing import Callable, Protocol

from .config import Source
from .globs import first_match, matches_any

PRUNED_DIR_NAMES = {".git", "node_modules"}


@dataclass
class SourceEntry:
    """One item found in a source.

    ``relpath`` is the display path under the source (it feeds ``{relpath}``, ``{dir}``, ``{name}`` and may change over time);
    ``identity`` is the stable id of the item (default: ``relpath``) and is what ownership, names and provenance follow.
    ``created`` (epoch seconds) is the date the source carries (it wins over the first-seen date for ``{date}``). ``version`` is an
    opaque change marker: when it equals the one recorded for the file, the content is not fetched. ``provenance`` holds extra
    provenance keys of the connector. ``content`` is the bytes or a callable that fetches them (used instead of ``abs_path``)."""

    relpath: str
    abs_path: str
    mtime: float
    size: int
    status: Literal["candidate", "skip"] = "candidate"
    reason: str = ""
    identity: str = ""
    created: float | None = None
    version: str = ""
    provenance: dict = field(default_factory=dict)
    content: "bytes | Callable[[], bytes] | None" = None
    warnings: int = 0

    def __post_init__(self) -> None:
        if not self.identity:
            self.identity = self.relpath

    def read_bytes(self) -> bytes:
        if self.content is not None:
            return self.content() if callable(self.content) else self.content
        with open(self.abs_path, "rb") as handle:
            return handle.read()


@dataclass
class SourceScan:
    """Result of scanning one source. A failure is ``status`` missing/unreadable with NO entries: never an empty success.

    ``guard_empty``: a successful scan with zero items for a source that owns files always asks the guard (connectors set it)."""

    label: str
    path: str
    status: Literal["ok", "missing", "unreadable", "off"] = "ok"
    error: str = ""
    entries: list[SourceEntry] = field(default_factory=list)
    kind: str = "folder"
    guard_empty: bool = False
    warnings: int = 0
    category: str = ""           # a connector failure: auth, permission, rate-limited, network, bad-query, ...


class SourceError(Exception):
    """A connector could not read its source (message is user-facing; no secrets). ``category`` names the kind of failure:
    auth, permission, rate-limited, network, bad-query, site-not-found, malformed, item-gone, error."""

    category = "error"

    def __init__(self, message: str, category: str = "error") -> None:
        super().__init__(message)
        self.category = category


class SourceAdapter(Protocol):
    """What a source type provides: ``scan`` lists every current item (identity, display path, dates, version) and how to read it.

    ``known`` maps identity -> the version recorded for it, so a connector can skip fetching what did not change."""

    def scan(self, include: list[str], exclude: list[str], known: dict[str, str] | None = None) -> SourceScan: ...


_REGISTRY: dict[str, Callable[[Source], SourceAdapter]] = {}


def register_source_type(name: str, factory: Callable[[Source], SourceAdapter]) -> None:
    """Make ``Source(type=name)`` buildable. A connector calls this once when it is imported."""
    _REGISTRY[name] = factory


def adapter_for(source: Source) -> SourceAdapter:
    if source.type == "folder":
        return LocalFolderSource(source)
    if source.type not in _REGISTRY:
        import mdpipeline.connectors  # noqa: F401  (registers the built-in connector types)
    if source.type not in _REGISTRY:
        raise KeyError(f"unknown source type {source.type!r}")
    return _REGISTRY[source.type](source)


def scan_source(source: Source, include: list[str], exclude: list[str], known: dict[str, str] | None = None) -> SourceScan:
    """Scan one source; an adapter that raises is an unreadable source, never an empty one (nothing may look deleted).

    An Off source (MD-103) is not scanned at all: no folder walk, no request, no credential read. Its scan has no entries and the
    status ``off``, which the planner treats like an unreadable source (its files are kept), but which is not a problem."""
    if not source.enabled:
        return SourceScan(label=source.label, path=source.path, status="off", kind=source.type)
    try:
        return adapter_for(source).scan(include, exclude, known)
    except Exception as exc:                      # noqa: BLE001 - any connector failure must degrade to 'unreadable'
        return SourceScan(label=source.label, path=source.path, status="unreadable", error=f"cannot read {source.label}: {exc}",
                          kind=source.type, category=getattr(exc, "category", "error") if isinstance(exc, SourceError) else "error")


class LocalFolderSource:
    """A local directory tree (read-only)."""

    def __init__(self, source: Source) -> None:
        self.source = source

    def scan(self, include: list[str], exclude: list[str], known: dict[str, str] | None = None) -> SourceScan:
        src = self.source
        result = SourceScan(label=src.label, path=src.path)
        root = Path(src.path)
        if not root.exists() and not root.is_symlink():
            result.status, result.error = "missing", f"source folder not found: {src.path}"
            result.category = "source-missing"          # MD-121: the notices tell a missing source from other failures
            return result
        if not root.is_dir():
            result.status, result.error = "unreadable", f"source is not a readable folder: {src.path}"
            return result
        try:
            self._walk(root, "", include, exclude, result)
        except OSError as exc:
            result.status, result.error, result.entries = "unreadable", f"cannot read {src.path}: {exc}", []
        result.entries.sort(key=lambda e: e.relpath)
        return result

    def _walk(self, directory: Path, prefix: str, include: list[str], exclude: list[str], result: SourceScan) -> None:
        with os.scandir(directory) as it:
            children = sorted(it, key=lambda e: e.name)
        for child in children:
            rel = f"{prefix}{child.name}"
            if child.is_symlink():
                if matches_any(include, rel):
                    result.entries.append(SourceEntry(rel, child.path, 0.0, 0, "skip", "symlink not followed"))
                continue
            if child.is_dir(follow_symlinks=False):
                if child.name in PRUNED_DIR_NAMES and matches_any(exclude, rel + "/x"):
                    continue
                self._walk(Path(child.path), rel + "/", include, exclude, result)
                continue
            if not child.is_file(follow_symlinks=False) or not matches_any(include, rel):
                continue
            st = child.stat(follow_symlinks=False)
            entry = SourceEntry(rel, child.path, st.st_mtime, st.st_size)
            rule = first_match(exclude, rel)
            if rule is not None:
                entry.status, entry.reason = "skip", f"excluded by rule {rule}"
            elif not rel.endswith(".md"):
                entry.status, entry.reason = "skip", "not a .md file"
            result.entries.append(entry)
