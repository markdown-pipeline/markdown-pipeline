"""Render a Jira issue (REST JSON + its comments) as one Markdown file (MD-48).

Deterministic: the same data always gives the same bytes. Nothing is silently dropped: an ADF node the renderer does not know keeps
its text under a visible ``[unsupported content: <type>]`` marker and is counted as a conversion warning. People appear by display
name only (never an e-mail address or account id). The result has a ``jira:`` front-matter mapping, then the title, description,
sub-tasks, links, attachments and comments (oldest first). The engine merges the provenance block into the front-matter.

``RENDER_VERSION`` is part of the version recorded for every issue: raising it rewrites every Jira file once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml

RENDER_VERSION = 1


class RenderError(ValueError):
    """The issue data cannot be converted (message names the problem; that issue fails alone)."""


@dataclass(frozen=True)
class RenderedIssue:
    text: str
    warnings: int


# ---- text helpers -------------------------------------------------------------------------------
def esc(text: str) -> str:
    text = text.replace("\\", "\\\\")
    for ch in "*`[]<>":
        text = text.replace(ch, "\\" + ch)
    return re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", text)


def _guard_line_start(line: str) -> str:
    """A paragraph line that would read as a heading, list or quote is escaped at its start."""
    if re.match(r"#{1,6}(\s|$)", line) or re.match(r"[-+]\s", line):
        return "\\" + line
    return re.sub(r"^(\d+)\. ", r"\1\\. ", line)


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None


def _iso(value: str | None) -> str | None:
    moment = _utc(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else (value or None)


# ---- ADF -> Markdown ----------------------------------------------------------------------------
class _Adf:
    def __init__(self) -> None:
        self.warnings = 0

    # inline
    def inline(self, nodes) -> str:
        return "".join(self._inline_node(n) for n in self._children(nodes))

    @staticmethod
    def _children(nodes) -> list:
        if nodes is None:
            return []
        if not isinstance(nodes, list):
            raise RenderError("an ADF 'content' is not a list")
        for n in nodes:
            if not isinstance(n, dict):
                raise RenderError("an ADF node is not an object")
        return nodes

    def _text(self, node: dict) -> str:
        text = node.get("text", "")
        marks = [m.get("type") for m in node.get("marks", []) if isinstance(m, dict)]
        link = next((m for m in node.get("marks", []) if isinstance(m, dict) and m.get("type") == "link"), None)
        if "code" in marks:
            fence = "``" if "`" in text else "`"
            out = f"{fence}{text}{fence}"
        else:
            out = esc(text)
        core = out.strip()
        if not core:
            return out
        lead, trail = (len(out) - len(out.lstrip())), (len(out) - len(out.rstrip()))
        if core:
            if "strong" in marks:
                core = f"**{core}**"
            if "em" in marks:
                core = f"*{core}*"
            if "strike" in marks:
                core = f"~~{core}~~"
            if link and (link.get("attrs") or {}).get("href"):
                core = f"[{core}]({link['attrs']['href']})"
        return out[:lead] + core + (out[len(out) - trail:] if trail else "")

    def _inline_node(self, node: dict) -> str:
        kind, attrs = node.get("type"), node.get("attrs") or {}
        if kind == "text":
            return self._text(node)
        if kind == "hardBreak":
            return "  \n"
        if kind == "mention":
            name = str(attrs.get("text") or "").lstrip("@") or "unknown user"
            return "@" + esc(name)
        if kind == "emoji":
            return str(attrs.get("text") or attrs.get("shortName") or "")
        if kind == "date":
            try:
                return datetime.fromtimestamp(int(attrs["timestamp"]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except (KeyError, ValueError, TypeError, OverflowError):
                return str(attrs.get("timestamp", ""))
        if kind == "status":
            return "\\[" + esc(str(attrs.get("text", ""))) + "\\]"
        if kind in ("inlineCard", "blockCard", "embedCard"):
            return f"<{attrs['url']}>" if attrs.get("url") else "[card]"
        if kind in ("media", "mediaInline"):
            return "[media: " + str(attrs.get("alt") or attrs.get("id") or attrs.get("collection") or "file") + "]"
        if kind in _BLOCKS:
            return self.block(node)
        return self._unsupported(node)

    def _unsupported(self, node: dict) -> str:
        self.warnings += 1
        text = self._plain(node)
        return f"**[unsupported content: {node.get('type')}]**" + (f" {esc(text)}" if text else "")

    def _plain(self, node) -> str:
        if isinstance(node, dict):
            if node.get("type") == "text":
                return node.get("text", "")
            parts = [self._plain(c) for c in node.get("content", []) or []]
            return " ".join(p for p in parts if p)
        return ""

    # blocks
    def blocks(self, nodes) -> str:
        parts = [self.block(n) for n in self._children(nodes)]
        return "\n\n".join(p for p in parts if p != "")

    def block(self, node: dict) -> str:
        kind, attrs, content = node.get("type"), node.get("attrs") or {}, node.get("content")
        if kind == "paragraph":
            return "\n".join(_guard_line_start(l) for l in self.inline(content).split("\n"))
        if kind == "heading":
            return "#" * min(max(int(attrs.get("level", 1)), 1), 6) + " " + self.inline(content)
        if kind == "bulletList":
            return "\n".join(self._item("- ", i) for i in self._children(content))
        if kind == "orderedList":
            start = int(attrs.get("order", 1))
            return "\n".join(self._item(f"{start + n}. ", i) for n, i in enumerate(self._children(content)))
        if kind == "taskList":
            return "\n".join(self._item("- [x] " if (i.get("attrs") or {}).get("state") == "DONE" else "- [ ] ", i) for i in self._children(content))
        if kind == "decisionList":
            return "\n".join(self._item("- Decision: ", i) for i in self._children(content))
        if kind == "codeBlock":
            text = "".join(c.get("text", "") for c in self._children(content))
            fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", text)), default=0) + 1)
            return f"{fence}{attrs.get('language') or ''}\n{text}\n{fence}"
        if kind == "blockquote":
            return self._quote(self.blocks(content))
        if kind == "panel":
            label = str(attrs.get("panelType") or "note").capitalize()
            return self._quote(f"**{label}**\n\n{self.blocks(content)}")
        if kind == "rule":
            return "---"
        if kind in ("blockCard", "embedCard"):
            return self._inline_node(node)
        if kind == "table":
            return self._table(node)
        if kind in ("mediaSingle", "mediaGroup"):
            return " ".join(self._inline_node(c) for c in self._children(content))
        if kind == "caption":
            return self.inline(content)
        if kind in ("expand", "nestedExpand"):
            title = str(attrs.get("title") or "").strip()
            return (f"**{esc(title)}**\n\n" if title else "") + self.blocks(content)
        if kind in ("layoutSection", "layoutColumn"):
            return self.blocks(content)
        if kind in ("listItem", "taskItem", "decisionItem"):
            return self._item("", node)
        return self._unsupported(node)

    def _item(self, marker: str, item: dict) -> str:
        children = self._children(item.get("content"))
        if children and all(c.get("type") in _INLINE for c in children):      # taskItem / decisionItem hold inline nodes
            body = self.inline(children)
        else:
            parts = [self.block(c) for c in children]
            parts = [p for p in parts if p != ""]
            tight = all(c.get("type") in ("bulletList", "orderedList", "taskList") for c in children[1:])
            body = ("\n" if tight else "\n\n").join(parts)
        pad = " " * len(marker)
        lines = body.split("\n") or [""]
        return marker + lines[0] + "".join("\n" + (pad + l if l else "") for l in lines[1:])

    @staticmethod
    def _quote(text: str) -> str:
        return "\n".join(("> " + l) if l else ">" for l in text.split("\n"))

    def _cell(self, cell: dict) -> str:
        text = "<br>".join(p for p in (self.block(c) if c.get("type") not in _INLINE else self._inline_node(c)
                                       for c in self._children(cell.get("content"))) if p)
        return text.replace("|", "\\|").replace("  \n", "<br>").replace("\n", "<br>").strip()

    def _table(self, node: dict) -> str:
        rows = [[(c.get("type"), self._cell(c)) for c in self._children(r.get("content"))] for r in self._children(node.get("content"))]
        rows = [r for r in rows if r]
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        header_row = rows[0] if all(k == "tableHeader" for k, _ in rows[0]) else None
        body = rows[1:] if header_row else rows
        head = [t for _, t in header_row] if header_row else [""] * width
        line = lambda cells: "| " + " | ".join((cells + [""] * (width - len(cells)))) + " |"      # noqa: E731
        return "\n".join([line(head), "| " + " | ".join(["---"] * width) + " |", *[line([t for _, t in r]) for r in body]])


_BLOCKS = {"paragraph", "heading", "bulletList", "orderedList", "taskList", "decisionList", "codeBlock", "blockquote", "panel", "rule", "table",
           "mediaSingle", "mediaGroup", "expand", "nestedExpand", "layoutSection", "layoutColumn", "caption"}
_INLINE = {"text", "hardBreak", "mention", "emoji", "date", "status", "inlineCard", "mediaInline"}


def adf_to_markdown(doc) -> tuple[str, int]:
    """Convert an ADF document (or a plain string from an old API) to Markdown. Returns (markdown, conversion warnings)."""
    if isinstance(doc, str):
        return "\n".join(_guard_line_start(l) for l in esc(doc).split("\n")), 0
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        raise RenderError("the text field is not an ADF document")
    converter = _Adf()
    return converter.blocks(doc.get("content")), converter.warnings


# ---- the issue ----------------------------------------------------------------------------------
def _name(person) -> str | None:
    return (person or {}).get("displayName") if isinstance(person, dict) else None


def _size(n) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _sprints(fields: dict) -> list[str]:
    for key in sorted(fields):
        value = fields[key]
        if key.startswith("customfield_") and isinstance(value, list) and value and all(isinstance(v, dict) and "name" in v and ("boardId" in v or "state" in v) for v in value):
            return [v["name"] for v in value]
    return []


def render_issue(issue: dict, comments: list[dict], site: str) -> RenderedIssue:
    try:
        key, f = issue["key"], issue["fields"]
        warnings = 0
        meta: dict = {"key": key, "type": (f.get("issuetype") or {}).get("name"), "status": (f.get("status") or {}).get("name"),
                      "status_category": ((f.get("status") or {}).get("statusCategory") or {}).get("name"),
                      "priority": (f.get("priority") or {}).get("name"), "labels": list(f.get("labels") or []),
                      "components": [c["name"] for c in f.get("components") or []], "assignee": _name(f.get("assignee")),
                      "reporter": _name(f.get("reporter")), "parent": (f.get("parent") or {}).get("key"),
                      "created": _iso(f.get("created")), "updated": _iso(f.get("updated")), "resolved": _iso(f.get("resolutiondate")),
                      "due": f.get("duedate"), "fix_versions": [v["name"] for v in f.get("fixVersions") or []], "sprints": _sprints(f)}
        meta = {k: v for k, v in meta.items() if v not in (None, [], "")}
        front = yaml.safe_dump({"jira": meta}, sort_keys=False, default_flow_style=False, allow_unicode=True, width=10_000)
        out = [f"---\n{front}---", f"# {esc(key)}: {esc(f.get('summary') or '')}"]
        if f.get("description"):
            text, w = adf_to_markdown(f["description"])
            warnings += w
            if text:
                out.append(text)
        subtasks = [f"- {esc(s['key'])}: {esc((s.get('fields') or {}).get('summary', ''))} ({esc(((s.get('fields') or {}).get('status') or {}).get('name', ''))})"
                    for s in f.get("subtasks") or []]
        if subtasks:
            out.append("## Sub-tasks\n\n" + "\n".join(subtasks))
        links = []
        for link in f.get("issuelinks") or []:
            if link.get("outwardIssue"):
                relation, other = (link.get("type") or {}).get("outward", "relates to"), link["outwardIssue"]
            else:
                relation, other = (link.get("type") or {}).get("inward", "relates to"), link.get("inwardIssue") or {}
            links.append(f"- {esc(relation)} {esc(other.get('key', ''))}: {esc((other.get('fields') or {}).get('summary', ''))}")
        if links:
            out.append("## Links\n\n" + "\n".join(links))
        attachments = [f"- {esc(a.get('filename', 'file'))} ({_size(a.get('size'))})" + (f" <{a['content']}>" if a.get("content") else "") for a in f.get("attachment") or []]
        if attachments:
            out.append("## Attachments\n\n" + "\n".join(attachments))
        if comments:
            blocks = []
            for c in sorted(comments, key=lambda c: (c.get("created") or "", str(c.get("id")))):
                created, updated = _utc(c.get("created")), _utc(c.get("updated"))
                when = created.strftime("%Y-%m-%d %H:%M") + " UTC" if created else "unknown time"
                edited = " (edited)" if created and updated and updated > created else ""
                text, w = adf_to_markdown(c.get("body"))
                warnings += w
                blocks.append(f"### {esc(_name(c.get('author')) or 'Unknown user')} · {when}{edited}\n\n{text}".rstrip())
            out.append("## Comments\n\n" + "\n\n".join(blocks))
        return RenderedIssue("\n\n".join(out) + "\n", warnings)
    except RenderError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise RenderError(f"the issue data is malformed ({type(exc).__name__}: {exc})") from exc
