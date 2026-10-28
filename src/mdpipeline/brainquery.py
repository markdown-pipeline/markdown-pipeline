"""Read-only retrieval over the brain: ``search``, ``read`` and ``list``.

Scope (MD-133): Markdown files under ``raw/`` and ``wiki/``, plus every Markdown file a pipeline wrote anywhere in the output folder
(recognised by the provenance block at the top of the file). A person's own files in the same folder (a README, ``work/``), the
``.mdpipeline/`` folder, hidden folders and everything outside the brain are not readable. Symlinks are never followed. Nothing here writes.

Roots (MD-86): the brain's own folder, plus every other place a pipeline's files live locally: another folder target, or the
local clone of a GitHub target. Those are reached under ``@<pipeline>/`` (the first pipeline, by name, that uses the place), for
example ``@docs/raw/projects/x.md``; the same scope rules hold inside each. A GitHub target without a clone cannot be searched
locally and is listed as such.
Search is plain keyword/full-text ranking (BM25-style); there is no LLM and there are no embeddings.
"""
from __future__ import annotations

import math
import os
import re
import stat
from collections import Counter
from pathlib import Path

from .brain import Brain, ConfigReadError
from .frontmatter import parse_document

SCOPE = ("raw", "wiki")
DEFAULT_LIMIT, MAX_LIMIT = 10, 50
PROVENANCE_SUMMARY_KEYS = ("pipeline", "source_label", "source_path", "synced_at", "issue_key")


class QueryError(ValueError):
    """The request is invalid or outside the readable scope (message is user-facing)."""


def _roots(brain: Brain) -> dict[str, tuple[Path, str]]:
    """``{"@id": (folder, label)}`` for every local place besides the brain's own folder where pipelines' files live."""
    from .config import load_config
    from .destinations import describe_destination, destination_type, directory_root

    try:
        pipelines = sorted(load_config(brain).pipelines, key=lambda p: p.name)
    except ConfigReadError:
        return {}
    out: dict[str, tuple[Path, str]] = {}
    seen = {brain.root.resolve()}
    for p in pipelines:
        dest = p.destination
        if destination_type(dest) == "directory":
            folder, label = directory_root(brain, dest), describe_destination(brain, p)["label"]
        elif destination_type(dest) == "github" and (dest or {}).get("clone"):
            folder, label = Path(dest["clone"]).expanduser(), f"clone of {describe_destination(brain, p)['label']}"
        else:
            continue
        try:
            real = folder.resolve()
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        out["@" + p.name] = (folder, label)
    return out


def _not_searchable(brain: Brain) -> list[dict]:
    from .config import load_config
    from .destinations import describe_destination, destination_type

    try:
        pipelines = load_config(brain).pipelines
    except ConfigReadError:
        return []
    return [{"pipeline": p.name, "target": describe_destination(brain, p)["label"],
             "reason": "GitHub target without a local clone: set one with `pipeline edit NAME --clone DIR` to let agents search it"}
            for p in sorted(pipelines, key=lambda p: p.name) if destination_type(p.destination) == "github" and not (p.destination or {}).get("clone")]


def _split(brain: Brain, rel: str) -> tuple[str, Path, str, str]:
    """``(prefix, root folder, path inside the root, target label)``: an ``@id/...`` path selects another root."""
    if rel and rel.startswith("@"):
        prefix, _, inner = rel.partition("/")
        roots = _roots(brain)
        if prefix not in roots:
            raise QueryError(f"Not readable: {rel!r} (unknown location; use list to see the locations)")
        return prefix, roots[prefix][0], inner, roots[prefix][1]
    return "", brain.content_root, rel, ""


def _segments(rel: str, what: str) -> list[str]:
    if not rel or rel in (".", "/"):
        raise QueryError(f"{what} is required")
    if rel.startswith("/") or "\x00" in rel or "\\" in rel:
        raise QueryError(f"Not readable: {rel!r} (paths are relative to the brain)")
    segs = rel.rstrip("/").split("/")
    if any(s in ("", ".", "..") for s in segs):
        raise QueryError(f"Not readable: {rel!r} (no '..' or empty segments)")
    if any(seg.startswith(".") for seg in segs):
        raise QueryError(f"Not readable: {rel!r} (hidden folders and the app's own folder are not readable)")
    return segs


def _resolve(brain: Brain, rel: str, kind: str) -> Path:
    """Validate ``rel`` and return its absolute path. kind: 'file' or 'dir'."""
    _, root, inner, _ = _split(brain, rel)
    segs = _segments(inner, "path")
    current = root
    for i, seg in enumerate(segs):
        current = current / seg
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            raise QueryError(f"Not found: {rel} (use list to see the existing folders and files)") from None
        if stat.S_ISLNK(mode):
            raise QueryError(f"Not readable: {rel!r} passes through a symlink")
    if kind == "file":
        if not stat.S_ISREG(mode) or not segs[-1].endswith(".md"):
            raise QueryError(f"Not a Markdown file: {rel}")
        if segs[0] not in SCOPE and not _written_by_a_pipeline(current):
            raise QueryError(f"Not readable: {rel!r} (only files a pipeline wrote, and raw/ and wiki/, are readable)")
    elif not stat.S_ISDIR(mode):
        raise QueryError(f"Not a folder: {rel}")
    elif segs[0] not in SCOPE and not _holds_pipeline_files(current, "/".join(segs)):
        raise QueryError(f"Not readable: {rel!r} (only files a pipeline wrote, and raw/ and wiki/, are readable)")
    return current


def _written_by_a_pipeline(path: Path) -> bool:
    """True when the file starts with the provenance block the app writes (a person's own file does not)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8192).decode("utf-8", errors="replace")
    except OSError:
        return False
    return head.startswith("---") and "provenance:" in head.split("\n---", 1)[0]


def _holds_pipeline_files(directory: Path, rel: str) -> bool:
    stack = [directory]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for e in entries:
            if e.is_symlink() or e.name.startswith("."):
                continue
            if e.is_dir(follow_symlinks=False):
                stack.append(Path(e.path))
            elif e.is_file(follow_symlinks=False) and e.name.endswith(".md") and _written_by_a_pipeline(Path(e.path)):
                return True
    return False


def _readable(rel: str, path: Path) -> bool:
    return rel.split("/", 1)[0] in SCOPE or _written_by_a_pipeline(path)


def _iter_markdown(brain: Brain, prefix: str | None):
    """Yield (relpath, abs Path, target label) for every readable Markdown file, optionally under ``prefix`` (``@id/raw/...`` for
    another root; relpaths of another root carry its ``@id/`` prefix)."""
    if prefix:
        start = _resolve(brain, prefix, "dir")
        pre, _, inner, label = _split(brain, prefix)
        bases = [(start, "/".join(_segments(inner, "path")), pre, label)]
    else:
        bases = [(brain.content_root, "", "", "")] + [(folder, "", pre, label) for pre, (folder, label) in _roots(brain).items()]
    for base, base_rel, pre, label in bases:
        if base.is_symlink():
            continue
        stack = [(base, base_rel)]
        while stack:
            directory, rel = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                if e.is_symlink() or e.name.startswith("."):
                    continue
                child_rel = f"{rel}/{e.name}" if rel else e.name
                if e.is_dir(follow_symlinks=False):
                    stack.append((Path(e.path), child_rel))
                elif e.is_file(follow_symlinks=False) and e.name.endswith(".md") and _readable(child_rel, Path(e.path)):
                    yield (f"{pre}/{child_rel}" if pre else child_rel), Path(e.path), label


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _provenance(text: str) -> dict | None:
    prov = parse_document(text)[0].get("provenance")
    return prov if isinstance(prov, dict) else None


def _summary(prov: dict | None) -> dict | None:
    if prov is None:
        return None
    return {k: prov[k] for k in PROVENANCE_SUMMARY_KEYS if k in prov}


# --- read / list ---------------------------------------------------------------------------
def read_file(brain: Brain, path: str) -> dict:
    """The file's content plus its parsed provenance (None when it has none)."""
    abs_path = _resolve(brain, path, "file")
    text = _read_text(abs_path)
    jira = parse_document(text)[0].get("jira")
    pre, _, inner, _ = _split(brain, path)
    shown = "/".join(_segments(inner, "path"))
    return {"path": f"{pre}/{shown}" if pre else shown, "content": text, "provenance": _provenance(text),
            **({"jira": jira} if isinstance(jira, dict) else {}),
            "size": abs_path.stat().st_size}


def _count(brain: Brain, shown: str) -> int:
    """Readable Markdown files under a folder; a folder with none (nothing a pipeline wrote) counts as 0, it is not an error."""
    try:
        return sum(1 for _ in _iter_markdown(brain, shown))
    except QueryError:
        return 0


def _top_entries(brain: Brain, root: Path, pre: str) -> tuple[list[dict], list[dict]]:
    """The top of one root: its folders that hold readable Markdown (raw/ and wiki/ always, when they exist) and its readable files."""
    folders, files = [], []
    try:
        entries = sorted(os.scandir(root), key=lambda e: (e.name not in SCOPE, SCOPE.index(e.name) if e.name in SCOPE else 0, e.name))
    except OSError:
        return folders, files
    for e in entries:
        if e.is_symlink() or e.name.startswith("."):
            continue
        shown = f"{pre}/{e.name}" if pre else e.name
        if e.is_dir(follow_symlinks=False):
            count = _count(brain, shown)
            if count or e.name in SCOPE:
                folders.append({"path": shown, "md_files": count})
        elif e.is_file(follow_symlinks=False) and e.name.endswith(".md") and _written_by_a_pipeline(Path(e.path)):
            files.append({"path": shown, "size": e.stat().st_size, "provenance": _summary(_provenance(_read_text(Path(e.path))))})
    return folders, files


def list_path(brain: Brain, path_prefix: str | None = None) -> dict:
    """Immediate subfolders (with Markdown counts) and Markdown files (with a provenance summary)."""
    if not path_prefix or path_prefix == ".":
        folders, files = _top_entries(brain, brain.content_root, "")
        for pre, (folder, label) in _roots(brain).items():
            sub_folders, sub_files = _top_entries(brain, folder, pre)
            if sub_folders or sub_files:
                folders.append({"path": pre, "md_files": sum(f["md_files"] for f in sub_folders) + len(sub_files), "target": label})
        out = {"path": "", "folders": folders, "files": files}
        missing = _not_searchable(brain)
        if missing:
            out["not_searchable"] = missing
        return out
    pre, root, inner, label = _split(brain, path_prefix)
    if pre and not inner.strip("/"):                                      # the top of another root
        sub_folders, sub_files = _top_entries(brain, root, pre)
        return {"path": pre, "folders": sub_folders, "files": sub_files, "target": label}
    directory = _resolve(brain, path_prefix, "dir")
    rel = "/".join(_segments(inner, "path"))
    shown = f"{pre}/{rel}" if pre else rel
    folders, files = [], []
    for e in sorted(os.scandir(directory), key=lambda e: e.name):
        if e.is_symlink() or e.name.startswith("."):
            continue
        child = f"{shown}/{e.name}"
        if e.is_dir(follow_symlinks=False):
            count = _count(brain, child)
            if count:
                folders.append({"path": child, "md_files": count})
        elif e.is_file(follow_symlinks=False) and e.name.endswith(".md") and _readable(f"{rel}/{e.name}", Path(e.path)):
            text = _read_text(Path(e.path))
            files.append({"path": child, "size": e.stat().st_size, "provenance": _summary(_provenance(text))})
    return {"path": shown, "folders": folders, "files": files}


# --- search --------------------------------------------------------------------------------
_PHRASE = re.compile(r'"([^"]+)"')
_WORD = re.compile(r"\w+", re.UNICODE)


_ISSUE_KEY = re.compile(r"\b[A-Za-z][A-Za-z0-9]*-\d+\b")


def _parse_query(query: str) -> tuple[list[str], list[str]]:
    phrases = [p.lower().strip() for p in _PHRASE.findall(query) if p.strip()]
    rest = _PHRASE.sub(" ", query)
    keys = [k.lower() for k in _ISSUE_KEY.findall(rest)]       # an issue key (MD-32) is searched as one exact term
    words = _WORD.findall(_ISSUE_KEY.sub(" ", rest).lower())
    return words, phrases + keys


def _snippets(text: str, needles: list[str], max_passages: int = 3) -> list[dict]:
    """Matching lines, preferring the body over front-matter metadata."""
    _, body = parse_document(text)
    offset = text[: len(text) - len(body)].count("\n") if text.endswith(body) else 0
    found = _scan_lines(body, needles, max_passages, offset)
    return found or _scan_lines(text, needles, max_passages, 0)


def _scan_lines(text: str, needles: list[str], max_passages: int, offset: int) -> list[dict]:
    out = []
    for number, line in enumerate(text.splitlines(), start=1 + offset):
        low = line.lower()
        hits = [low.find(n) for n in needles if n in low]
        if not hits:
            continue
        pos = min(hits)
        start = max(0, pos - 100)
        snippet = line[start:start + 240].strip()
        out.append({"line": number, "text": ("..." if start else "") + snippet})
        if len(out) >= max_passages:
            break
    return out


def search(brain: Brain, query: str, path_prefix: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Ranked Markdown files with matching passages and provenance. Files with all terms rank first."""
    words, phrases = _parse_query(query or "")
    if not words and not phrases:
        raise QueryError("query is empty")
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    docs = []
    labels: dict[str, str] = {}
    for rel, abs_path, label in _iter_markdown(brain, path_prefix):
        text = _read_text(abs_path)
        low = text.lower()
        tokens = _WORD.findall(low)
        docs.append((rel, text, low, Counter(tokens), tokens))
        if label:
            labels[rel] = label
    if not docs:
        return []
    n_docs = len(docs)
    avg_len = sum(len(d[4]) for d in docs) / n_docs or 1.0

    def tf_of(term_kind: str, term: str, low: str, counter: Counter) -> float:
        if term_kind == "phrase":
            return float(low.count(term))
        exact = counter.get(term, 0)
        prefix = sum(c for tok, c in counter.items() if tok != term and tok.startswith(term)) if len(term) >= 3 else 0
        return exact + 0.5 * prefix

    terms = [("word", w) for w in words] + [("phrase", p) for p in phrases]
    tfs = [[tf_of(k, t, d[2], d[3]) for (k, t) in terms] for d in docs]
    dfs = [sum(1 for row in tfs if row[i] > 0) for i in range(len(terms))]
    scored = []
    k1, b = 1.2, 0.75
    for (rel, text, low, counter, tokens), row in zip(docs, tfs):
        if not any(row):
            continue
        score = 0.0
        for i, tf in enumerate(row):
            if tf <= 0:
                continue
            idf = math.log(1 + (n_docs - dfs[i] + 0.5) / (dfs[i] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / avg_len))
            if terms[i][1] in rel.lower() or terms[i][1] in " ".join(tokens[:12]):
                score += idf  # a hit in the path or the opening words (title) counts extra
        matched = sum(1 for tf in row if tf > 0)
        score *= 1 + matched / len(terms)          # files containing all terms rank first
        if matched == len(terms):
            score += 100.0
        scored.append((score, rel, text))
    scored.sort(key=lambda s: (-s[0], s[1]))
    needles = [t for _, t in terms]
    return [{"path": rel, "score": round(score, 3), "passages": _snippets(text, needles),
             "provenance": _provenance(text), **({"target": labels[rel]} if rel in labels else {})} for score, rel, text in scored[:limit]]
