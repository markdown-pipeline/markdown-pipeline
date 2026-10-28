"""YAML front-matter handling: merge a ``provenance:`` map without re-serialising the author's keys.

The author's front-matter text is kept verbatim (comments, quoting, ordering); the ``provenance``
block is appended as text. The body after the closing delimiter is never touched.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import yaml

PROVENANCE_KEYS = ("source", "pipeline", "source_label", "source_path", "source_root",
                   "source_modified", "synced_at", "content_hash")
NAME_KEYS = ("original_name", "first_seen", "name_policy", "name_suffix")   # MD-38, only for non-"original" policies


class FrontMatterError(ValueError):
    """The source file's front-matter is malformed or conflicts with provenance."""


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_provenance(*, pipeline: str, source_label: str, source_path: str, source_root: str,
                     source_mtime: float, synced_at: float, source_bytes: bytes, names: dict | None = None,
                     source_type: str = "local", extra: dict | None = None) -> dict:
    """The provenance map for one copied file (``content_hash`` is of the SOURCE bytes).

    ``source_type`` and ``extra`` (MD-45) are the connector's: its name (``local`` for folders) and its extra provenance keys.
    ``names`` (MD-38) holds ``original_name``, ``first_seen``, ``name_policy`` and, when a clash suffix was applied,
    ``name_suffix``; they follow ``source_path``. Without it the map is exactly the Phase 1 one."""
    extra_keys = extra
    extra = {k: names[k] for k in NAME_KEYS if names and names.get(k)}
    return {
        "source": source_type,
        "pipeline": pipeline,
        "source_label": source_label,
        "source_path": source_path,
        **extra,
        **(extra_keys or {}),
        "source_root": source_root,
        "source_modified": iso_utc(source_mtime),
        "synced_at": iso_utc(synced_at),
        "content_hash": content_hash(source_bytes),
    }


def _split(text: str) -> tuple[str, str, str | None, str]:
    """Return (bom, newline, front_matter_text | None, body). Raises on an unterminated block."""
    bom = "﻿" if text.startswith("﻿") else ""
    rest = text[len(bom):]
    lines = rest.splitlines(keepends=True)
    if not lines or lines[0].rstrip(" \t\r\n") != "---":
        return bom, "\n", None, text
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    for index in range(1, len(lines)):
        if lines[index].rstrip(" \t\r\n") in ("---", "..."):
            return bom, newline, "".join(lines[1:index]), "".join(lines[index + 1:])
    raise FrontMatterError("front-matter starts with '---' but has no closing '---'")


def _load_mapping(fm_text: str) -> dict:
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"front-matter is not valid YAML: {str(exc).splitlines()[0]}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontMatterError("front-matter is not a key/value mapping")
    return data


def parse_document(text: str) -> tuple[dict, str]:
    """Tolerantly parse ``text`` into (front-matter mapping, body). Never raises."""
    try:
        _, _, fm_text, body = _split(text)
        if fm_text is None:
            return {}, text
        return _load_mapping(fm_text), body
    except FrontMatterError:
        return {}, text


def merge_provenance(source: bytes, provenance: dict) -> bytes:
    """Return the brain file content: the source with a ``provenance:`` map merged into its front-matter."""
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrontMatterError("file is not valid UTF-8 text") from exc
    bom, newline, fm_text, body = _split(text)
    if fm_text is None:
        fm_text, body = "", text[len(bom):]
    else:
        if "provenance" in _load_mapping(fm_text):
            raise FrontMatterError("the source front-matter already has a 'provenance' key; it is not overwritten")
        if fm_text and not fm_text.endswith("\n"):
            fm_text += newline
    block = yaml.safe_dump({"provenance": provenance}, sort_keys=False, default_flow_style=False,
                           allow_unicode=True, width=10_000)
    if newline != "\n":
        block = block.replace("\n", newline)
    return f"{bom}---{newline}{fm_text}{block}---{newline}{body}".encode("utf-8")
