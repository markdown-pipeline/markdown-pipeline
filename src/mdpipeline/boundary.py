"""The write boundary, pure-string part: which output-folder-relative paths a pipeline may ever target (MD-133).

A target is allowed only if it is a relative POSIX path with no empty, ``.`` or ``..`` segment, and its top-level folder is not the app's own
folder (``.mdpipeline``), ``wiki/`` (curated by people and agents) or a hidden folder (``.git``, ``.obsidian``...); deeper down, a folder called ``wiki`` is just a name (a source may be labelled that). Names are compared
case-insensitively, so ``Wiki/`` cannot reach ``wiki/`` on a case-insensitive disk. There is no required ``raw/`` prefix: the output folder
itself is the root. (The filesystem part, which also defends against symlinked folders, is added for execution time in
``mdpipeline.writer``; never overwriting a file the pipeline does not own is the planner's rule.)
"""
from __future__ import annotations

PROTECTED_FOLDERS = frozenset({".mdpipeline", "wiki"})


def check_target_relpath(rel: str) -> str | None:
    """Return None if ``rel`` is an allowed target, else a short reason."""
    if not rel:
        return "the target path is empty"
    if "\x00" in rel or "\\" in rel:
        return "the target path contains forbidden characters"
    if rel.startswith("/"):
        return "the target path is absolute"
    segments = rel.split("/")
    if any(seg in ("", ".") for seg in segments):
        return "the target path has empty segments"
    if ".." in segments:
        return "the target path contains '..'"
    top = segments[0]
    if len(segments) > 1:                     # only the top level of the output folder is special; deeper, "wiki" is just a name
        if top.casefold() in PROTECTED_FOLDERS:
            return f"'{top}/' is not a place the pipeline may write (the app's own folder and wiki/ are never touched)"
        if top.startswith("."):
            return f"the target path goes into the hidden folder '{top}/'"
    return None


def check_write_target(rel: str) -> str | None:
    """The engine-level write boundary every target gets: an allowed path (see above) naming a ``.md`` file.

    Enforced by the engine before any target is asked to write or delete, so a new target cannot forget it."""
    reason = check_target_relpath(rel)
    if reason:
        return reason
    name = rel.rsplit("/", 1)[-1]
    if not name.endswith(".md") or name == ".md":
        return "only Markdown (.md) files may be written"
    return None
