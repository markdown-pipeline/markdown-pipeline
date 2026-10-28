"""What the activity log may say about files of connector sources (MD-71).

A Jira issue's title is issue content, and it is part of the file name. The log is shared in support requests, so for files that
come from a connector it records the issue KEY (and the action and counts), never the title or a title-derived file name.
Interactive output of a person's own sync (CLI, UI) may still show paths; only what is persisted is made safe here.
"""
from __future__ import annotations

import posixpath
import re

KEY = re.compile(r"\b[A-Z][A-Z0-9_]*-\d+\b")


def issue_key(*candidates: str, fallback: str = "") -> str:
    """The first issue key found in the file name (or display path) of an item; the source id (``#123``) when there is none."""
    for text in candidates:
        found = KEY.search(posixpath.basename(text or ""))
        if found:
            return found.group(0)
    return f"#{fallback}" if fallback else "unknown"


def connector_labels(pipeline) -> set[str]:
    return {s.label for s in pipeline.sources if s.type != "folder"}


def item_key(item) -> str:
    return issue_key(item.display, item.target, item.from_target or "", fallback=item.source_path)


def _reason(item) -> str:
    """A reason is kept only when it does not repeat a file name (a reason may quote the path, and so the title)."""
    reason = item.reason or ""
    stem = posixpath.splitext(posixpath.basename(item.display or item.target or ""))[0]
    for text in (item.target, item.from_target, item.display, stem):
        if text and len(text) > 4 and text in reason:
            return ""
    return reason


def log_file(item, connector: bool) -> dict:
    """The log form of a plan item: for a connector source the key, never the path."""
    if not connector:
        return item.to_dict()
    out = {"source_label": item.source_label, "source_path": item.source_path, "action": item.action, "key": item_key(item)}
    if _reason(item) and not item.error and item.action not in ("conflict", "rename"):
        out["reason"] = _reason(item)
    for flag, value in (("conflict_type", item.conflict_type), ("metadata_only", item.metadata_only or None), ("error", item.error or None)):
        if value:
            out[flag] = value
    return out


def log_conflict(item, connector: bool) -> dict:
    if not connector:
        return {"type": item.conflict_type, "path": item.target}
    return {"type": item.conflict_type, "path": item_key(item), "key": item_key(item), "source": item.source_label, "source_path": item.source_path}


def log_error(item, connector: bool) -> dict:
    if not connector:
        return {"source": item.source_label, "path": item.source_path, "error": item.reason}
    key = item_key(item)
    reason = _reason(item) or "could not be copied"
    return {"source": item.source_label, "path": key, "key": key, "error": reason}


def log_paths(pipeline, files: dict) -> list[str]:
    """Paths (``{target: FileRecord}``) as the log may show them: connector files by key."""
    connectors = connector_labels(pipeline)
    return [issue_key(t, fallback=r.source_path) if r.source_label in connectors else t for t, r in files.items()]
