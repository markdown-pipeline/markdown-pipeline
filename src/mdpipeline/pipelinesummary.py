"""MD-135: one plain sentence saying what a pipeline does, generated from its configuration (never typed, never a secret).

Only fixed words, paths, site hosts, project keys, repository names and the user's own filter globs go in: no credential references,
tokens or emails. The "what the next sync will change" line comes from the dry run and is built by the page."""
from __future__ import annotations

import os
import re

from .brain import Brain
from .config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, Pipeline, Source
from .destinations import describe_destination

SCHEDULE_WORDS = {"15m": "Checks for changes every 15 minutes.", "1h": "Checks for changes every hour.", "6h": "Checks for changes every 6 hours.", "daily": "Checks for changes once a day.", "off": "Only when you sync it."}
JQL_SHOWN = 40
_PROJECT_ONLY = re.compile(r'^\s*project\s*=\s*"?([A-Za-z][A-Za-z0-9_]*)"?\s*$', re.IGNORECASE)


def _tilde(path: str) -> str:
    home = os.path.expanduser("~")
    if home and home != "~" and (path == home or path.startswith(home.rstrip("/") + "/")):
        return "~" + path[len(home.rstrip("/")):]
    return path


def _jira_text(s: Source) -> str:
    site = str(s.options.get("site") or s.path or "").strip().rstrip("/")
    host = re.sub(r"^https?://", "", site)
    jql = str(s.options.get("jql") or "").strip()
    match = _PROJECT_ONLY.match(jql)
    if match:
        scope = f"project {match.group(1)}"
    elif jql:
        scope = "JQL: " + (jql if len(jql) <= JQL_SHOWN else jql[:JQL_SHOWN].rstrip() + "…")
    else:
        scope = ""
    return f"Jira {host}" + (f" ({scope})" if scope else "")


def _sources_text(p: Pipeline) -> str:
    names = [_jira_text(s) if s.type == "jira" else _tilde(s.path) for s in p.sources if s.enabled]
    if not names:
        return "no source (all are switched off)"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]}, {names[1]} and {len(names) - 2} more"


def _where(target: str) -> str:
    t = (target or "").strip()
    if t in ("", "{relpath}"):
        return "at the root of the output folder"
    if t.endswith("/{relpath}"):
        return f"under {t[: -len('{relpath}')]}"
    return f"using the path pattern {t}"


def summary_sentence(brain: Brain, p: Pipeline) -> str:
    """'Turns <sources> into Markdown files in <destination>, under raw/. Checks for changes every hour.' plus pause and filter notes."""
    dest = describe_destination(brain, p)
    to = f"GitHub {dest['repo']} ({dest['branch']})" if dest["type"] == "github" else _tilde(dest["path"])
    parts = []
    if p.paused:
        parts.append("Paused.")
    parts.append(f"Turns {_sources_text(p)} into Markdown files in {to}, {_where(p.target)}.")
    parts.append(SCHEDULE_WORDS.get(p.schedule, f"Checks for changes every {p.schedule}."))
    if list(p.include) != list(DEFAULT_INCLUDE):
        parts.append("Only files matching " + ", ".join(p.include) + ".")
    extra = [g for g in p.exclude if g not in DEFAULT_EXCLUDE]
    if extra:
        parts.append("Skips " + ", ".join(extra) + ".")
    return " ".join(parts)
