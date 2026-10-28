"""File-name policies for ``raw/``: the slug rule and the ``{date}`` rule (pure functions).

The owner has not yet confirmed the field separator (``-`` or ``_``). It lives in exactly one place,
``SEPARATOR``, so the decision is a one-line change. ``CLASH_SEPARATOR`` joins the 6-hex clash suffix.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as _date

SEPARATOR = "_"          # between {date} and {slug} in the presets (pending owner decision: "-" or "_")
CLASH_SEPARATOR = "-"    # between the slug and the 6-hex clash suffix (spec example: 2026-10-04_readme-3f9a1c.md)
MAX_SLUG = 80
EMPTY_SLUG = "untitled"

_LEADING_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[-_.\s]+(?=.)|(\d{4})(\d{2})(\d{2})[-_.\s]+(?=.)", re.S)


def _keep(char: str) -> bool:
    return unicodedata.category(char)[0] in "LNM"   # letters, digits, combining marks (Hebrew niqqud)


def slugify(stem: str) -> str:
    """Lowercase NFC slug: letters/digits of any script kept, other runs become one ``-``; max 80 chars."""
    text = unicodedata.normalize("NFC", unicodedata.normalize("NFC", stem).lower())
    out: list[str] = []
    for char in text:
        if _keep(char):
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")
    if len(slug) > MAX_SLUG:
        cut = slug[:MAX_SLUG]
        if slug[MAX_SLUG] != "-":
            boundary = cut.rfind("-")
            if boundary > 0:
                cut = cut[:boundary]
        slug = cut.strip("-")
    return slug or EMPTY_SLUG


def split_leading_date(stem: str) -> tuple[str | None, str]:
    """A leading ``YYYY-MM-DD`` or ``YYYYMMDD`` followed by a separator -> (ISO date, rest of the name)."""
    match = _LEADING_DATE.match(stem)
    if not match:
        return None, stem
    y, m, d = (match.group(1), match.group(2), match.group(3)) if match.group(1) else match.group(4, 5, 6)
    try:
        iso = _date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None, stem
    return iso, stem[match.end():]


@dataclass(frozen=True)
class NameParts:
    date: str    # YYYY-MM-DD
    slug: str
    name: str    # original name without extension (NFC)


def derive_name_parts(filename: str, first_seen: str) -> NameParts:
    """The ``{date}``, ``{slug}`` and ``{name}`` values for a source file name (``first_seen`` is ISO)."""
    stem = unicodedata.normalize("NFC", posixpath.splitext(filename)[0])
    name_date, rest = split_leading_date(stem)
    return NameParts(name_date or first_seen, slugify(rest), stem)


# --- policies ----------------------------------------------------------------------------------------
ORIGINAL = "original"
CUSTOM = "custom"
DEFAULT_NAME_POLICY = "date-name"     # default for NEW pipelines; pipelines saved before MD-33 stay ORIGINAL
PRESETS: dict[str, str] = {
    "date-name": "{date}" + SEPARATOR + "{slug}",
    "name": "{slug}",
    "source-name": "{source}--{slug}",
    "date-source-name": "{date}" + SEPARATOR + "{source}" + SEPARATOR + "{slug}",
}
POLICY_NAMES = (ORIGINAL, *PRESETS, CUSTOM)
VARIABLES = ("date", "slug", "name", "source", "pipeline")
_VAR = re.compile(r"\{([^{}]*)\}")
_LITERAL_OK = re.compile(r"[A-Za-z0-9._-]*\Z")


def check_name_policy(policy: str, pattern: str = "") -> list[str]:
    """Save-time validation. Returns clear messages; an empty list means the policy is valid."""
    if policy == ORIGINAL or policy in PRESETS:
        return []
    if policy != CUSTOM:
        return [f"Unknown file-name policy {policy!r}. Use one of: {', '.join(POLICY_NAMES)}."]
    if not pattern.strip():
        return ["A custom file-name pattern is required, for example {date}_{slug}."]
    if "/" in pattern or "\\" in pattern:
        return [f"File-name pattern {pattern!r} must not contain a path separator: folders come from the target template."]
    if ".." in pattern:
        return [f"File-name pattern {pattern!r} must not contain '..'."]
    problems: list[str] = []
    for key in _VAR.findall(pattern):
        if key not in VARIABLES:
            problems.append(f"Unknown variable {{{key}}} in the file-name pattern. Known: {', '.join('{' + v + '}' for v in VARIABLES)}.")
    leftover = _VAR.sub("", pattern)
    if "{" in leftover or "}" in leftover:
        problems.append(f"Unbalanced brace in the file-name pattern {pattern!r}.")
    elif not _LITERAL_OK.match(leftover):
        bad = sorted({c for c in leftover if not _LITERAL_OK.match(c)})
        problems.append(f"The file-name pattern may only use the separators - _ . besides variables; unsupported character(s): {' '.join(repr(c) for c in bad)}.")
    if not problems and "{slug}" not in pattern and "{name}" not in pattern:
        problems.append("The file-name pattern must contain {slug} or {name}, otherwise every file would get the same name.")
    return problems


def render_file_name(policy: str, pattern: str = "", *, filename: str, first_seen: str, source: str,
                     pipeline: str, suffix: str = "") -> str:
    """The file name (with ``.md``) for ``filename`` under a policy. ``original`` returns it unchanged."""
    if policy == ORIGINAL:
        return filename
    template = pattern if policy == CUSTOM else PRESETS[policy]
    parts = derive_name_parts(filename, first_seen)
    values = {"date": parts.date, "slug": parts.slug, "name": parts.name, "source": unicodedata.normalize("NFC", source),
              "pipeline": pipeline}
    stem = _VAR.sub(lambda m: values[m.group(1)], template)
    if suffix:
        stem += CLASH_SEPARATOR + suffix
    return stem + ".md"


def apply_name_policy(target: str, policy: str, pattern: str = "", *, relpath: str, first_seen: str, source: str,
                      pipeline: str, suffix: str = "") -> str:
    """Replace only the file-name segment of a template-rendered ``target``; directories stay as the template made them."""
    if policy == ORIGINAL:
        return target
    directory = posixpath.dirname(target)
    name = render_file_name(policy, pattern, filename=posixpath.basename(relpath), first_seen=first_seen,
                            source=source, pipeline=pipeline, suffix=suffix)
    return posixpath.join(directory, name) if directory else name


def policy_label(policy: str, pattern: str = "") -> str:
    """What is stored in state and provenance: the preset name, or the custom pattern itself."""
    return pattern if policy == CUSTOM else policy


def collision_key(path: str) -> str:
    """Two target paths clash when this key is equal: NFC-normalized and case-insensitive (safe on macOS/Windows)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())


def clash_suffix(label: str, relpath: str, length: int = 6) -> str:
    """Deterministic hex suffix from the source identity (label + relative path), NFC-normalized."""
    identity = unicodedata.normalize("NFC", f"{label}:{relpath}")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:length]


# --- names Windows cannot create (MD-182 AC2) ---------------------------------------------------------------------
WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})
RESERVED_MARK = "_"      # one underscore after the reserved stem: ``con.md`` -> ``con_.md``, folder ``aux`` -> ``aux_``


def windows_safe_segment(segment: str) -> str:
    """One path segment Windows can create. A reserved device name is reserved with ANY extension, in any case (``Nul.MD``)."""
    stem, dot, rest = segment.partition(".")
    if stem.rstrip(" ").casefold() in WINDOWS_RESERVED:
        return stem + RESERVED_MARK + dot + rest
    return segment


def owned_prefixes(recorded: Iterable[str]) -> frozenset[str]:
    """Every path and every folder prefix the brain already holds (``raw/p/aux/a.md`` -> ``raw``, ``raw/p``, ``raw/p/aux``, itself)."""
    out: set[str] = set()
    for path in recorded:
        parts = path.split("/")
        out.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return frozenset(out)


def windows_safe_target(target: str, owned: frozenset[str] | set[str] = frozenset()) -> str:
    """``target`` with every reserved segment changed, on EVERY OS (a brain copied between machines keeps its names).

    A segment under a path the brain already holds (``owned``, from :func:`owned_prefixes`) is left exactly as it is: an existing
    file or folder is never renamed, so a Linux brain made before this rule does not churn."""
    out: list[str] = []
    raw = ""
    for segment in target.split("/"):
        raw = f"{raw}/{segment}" if raw else segment
        out.append(segment if raw in owned else windows_safe_segment(segment))
    return "/".join(out)
