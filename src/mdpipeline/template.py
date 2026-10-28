"""Target path templates.

Variables (all values come from the source file, never from user text beyond the label):

* ``{pipeline}``  pipeline name
* ``{source}``    the source folder's label
* ``{relpath}``   POSIX path of the file relative to its source folder, with directories, file
                  name and extension (``docs/pricing.md``)
* ``{dir}``       the directory part of ``relpath`` (empty for files at the folder root)
* ``{name}``      file name without its final extension (``pricing``)
* ``{ext}``       extension without the dot (``md``)
* ``{mtime:FMT}`` the source file's modification time in UTC; FMT may contain only ``YYYY``,
                  ``MM``, ``DD`` and the separators ``-`` ``_`` ``.``

The template is relative to the brain root (so it normally starts with ``raw/``). Empty path
segments (for example from an empty ``{dir}``) collapse. This module does not decide whether the
result is allowed: see ``mdpipeline.boundary``.
"""
from __future__ import annotations

import posixpath
import re
from datetime import datetime, timezone

VARIABLES = ("pipeline", "source", "relpath", "dir", "name", "ext")
_MTIME_FORMAT = re.compile(r"(?:YYYY|MM|DD|[-_.])+\Z")
_VAR = re.compile(r"\{([^{}]*)\}")


class TemplateError(ValueError):
    """The template text is invalid."""


def _format_mtime(fmt: str, mtime: float) -> str:
    if not _MTIME_FORMAT.match(fmt):
        raise TemplateError(
            f"Invalid {{mtime:{fmt}}}: only YYYY, MM, DD and the separators - _ . are supported"
        )
    when = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return fmt.replace("YYYY", f"{when.year:04d}").replace("MM", f"{when.month:02d}").replace("DD", f"{when.day:02d}")


def render_target(template: str, *, pipeline: str, source: str, relpath: str, mtime: float) -> str:
    """Resolve ``template`` for one source file; returns a POSIX path relative to the brain root."""
    directory, filename = posixpath.split(relpath)
    stem, dot_ext = posixpath.splitext(filename)
    values = {
        "pipeline": pipeline,
        "source": source,
        "relpath": relpath,
        "dir": directory,
        "name": stem,
        "ext": dot_ext[1:],
    }

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key.startswith("mtime:"):
            return _format_mtime(key[len("mtime:"):], mtime)
        if key in values:
            return values[key]
        raise TemplateError(f"Unknown template variable {{{key}}}. Known: {', '.join('{' + v + '}' for v in VARIABLES)}, {{mtime:YYYY-MM-DD}}")

    # Balance is a property of the template text, never of the substituted values: a source file name
    # may legitimately contain braces (MD-151: Jira titles such as "Fix {x} parser").
    leftover = _VAR.sub("", template)
    if "{" in leftover or "}" in leftover:
        raise TemplateError(f"Unbalanced braces in template: {template}")
    resolved = _VAR.sub(substitute, template)
    return "/".join(seg for seg in resolved.split("/") if seg not in ("", "."))
