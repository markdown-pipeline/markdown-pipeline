"""Minimal glob matching on POSIX relative paths.

``*`` and ``?`` never cross ``/``; ``**/`` matches zero or more directories; ``dir/**`` matches
everything below ``dir``.
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def first_match(patterns: list[str], relpath: str) -> str | None:
    """Return the first pattern that matches ``relpath`` (POSIX, relative), or None."""
    for pattern in patterns:
        if _compile(pattern).match(relpath):
            return pattern
    return None


def matches_any(patterns: list[str], relpath: str) -> bool:
    return first_match(patterns, relpath) is not None
