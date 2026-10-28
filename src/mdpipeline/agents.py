"""Connect the person's coding agent to the app's MCP server in one click (MD-120, FR-S6).

The app never edits an agent's config file itself: it runs the agent's own ``mcp add`` / ``mcp remove`` command, only on the
person's click, and shows the exact command first. It also writes a short instructions file (``AGENTS.md``) into the folder the
files went to; an existing file is never overwritten without asking. What was added is remembered (one JSON file in the app
data directory) so Disconnect removes exactly that and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .brain import Brain
from .snippets import SERVER_NAME, _args
from .vault import data_dir

# agent id -> (command on the PATH, the name the person knows)
AGENTS = {"claude_code": ("claude", "Claude Code"), "codex": ("codex", "Codex")}
AGENTS_FILE = "AGENTS.md"
ALTERNATIVE_FILE = "MARKDOWN-PIPELINE.md"
BACKUP_SUFFIX = ".before-markdown-pipeline"
MARKER = "<!-- written by Markdown Pipeline -->"
COMMAND_TIMEOUT_S = 30


def label(agent: str) -> str:
    return AGENTS[agent][1]


def _known(agent: str) -> str:
    if agent not in AGENTS:
        raise ValueError(f"unknown agent '{agent}'")
    return AGENTS[agent][0]


def detect() -> list[str]:
    """The supported agents whose command is on the PATH, Claude Code first."""
    return [agent for agent, (command, _name) in AGENTS.items() if shutil.which(command)]


def _server(brain: Brain) -> list[str]:
    return [sys.executable, *_args(brain)]


def _add_command(agent: str, brain: Brain) -> list[str]:
    command = _known(agent)
    scope = ["--scope", "user"] if agent == "claude_code" else []
    return [command, "mcp", "add", *scope, SERVER_NAME, "--", *_server(brain)]


def _remove_command(agent: str) -> list[str]:
    command = _known(agent)
    scope = ["--scope", "user"] if agent == "claude_code" else []
    return [command, "mcp", "remove", *scope, SERVER_NAME]


# --- AGENTS.md ---------------------------------------------------------------------------------------------------
def agents_text(brain: Brain) -> str:
    """What the files are, the provenance fields, and how to search them: for an agent that opens this folder cold."""
    return f"""{MARKER}
# Markdown Pipeline files

These files were written by Markdown Pipeline. Each one is a copy of an item in a source (a Jira issue, a document, a file in a
folder), converted to Markdown. Read them, never edit them: the next sync overwrites changes, and the source stays the truth.

## Provenance
Every file starts with a front-matter block named `provenance` that says where it came from:

- `source`, `source_label`, `source_path`: the system, the source and the item's path or key in it. Cite `source_path` in answers.
- `pipeline`: the Markdown Pipeline pipeline that wrote the file.
- `source_modified`: when the item last changed in the source. `synced_at`: when this copy was written.
- `content_hash`: a hash of the source content; it changes when the item changes.

A Jira issue file also has a `jira` block (key, type, status, status_category and more) before the title.

## How to search
The `{SERVER_NAME}` MCP server gives you three read-only tools: `search` (full text, ranked, with provenance),
`read` (one file) and `list` (folders and files). `sync_status` tells you how fresh the files are, and `recent_changes` lists the files created, changed or deleted since a time
(use it for "what changed since yesterday?"). You write the answer yourself.
"""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_ours(path: Path) -> bool:
    try:
        return MARKER in path.read_text(encoding="utf-8")[:200]
    except (OSError, UnicodeDecodeError):
        return False


# --- the exact change -----------------------------------------------------------------------------------------------
def exact_change(brain: Brain, agent: str, folder: str) -> list[str]:
    """The lines shown before the click: the command that will run and the file that will be written."""
    target = Path(folder) / AGENTS_FILE
    if not target.exists():
        file_line = f"# new file {target}"
    elif _is_ours(target):
        file_line = f"# updated {target}"
    else:
        file_line = f"# {AGENTS_FILE} already exists in {folder}: yours is kept unless you choose otherwise"
    return [shlex.join(_add_command(agent, brain)), file_line]


# --- what was added (so Disconnect removes only that) ------------------------------------------------------------------
def _store_path() -> Path:
    return data_dir() / "agents.json"


def connected() -> dict:
    """``{agent: record}`` for the agents this app connected and the person has not disconnected."""
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {a: r for a, r in data.items() if a in AGENTS and isinstance(r, dict)} if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _run(command: list[str]) -> str:
    """Run an agent command; "" when it worked, otherwise the reason in the agent's own words."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return f"{command[0]} isn't installed or isn't on the PATH"
    except subprocess.TimeoutExpired:
        return f"{command[0]} didn't answer within {COMMAND_TIMEOUT_S} seconds"
    except OSError as exc:
        return str(exc)
    if done.returncode == 0:
        return ""
    return (done.stderr or done.stdout or f"{command[0]} exited with {done.returncode}").strip()[:300]


def _place_file(brain: Brain, agent: str, folder: str, how: str | None, previous: dict) -> tuple[dict | None, dict | None]:
    """Decide where our instructions file goes. ``(plan, conflict)``: a conflict means the person must choose first."""
    target = Path(folder) / AGENTS_FILE
    if not target.exists() or _is_ours(target):
        carried = previous.get("backup") if previous.get("file") == str(target) else None
        return {"file": str(target), "verb": "updated" if target.exists() else "created", "backup": carried}, None
    if how == "keep":
        return {"file": str(Path(folder) / ALTERNATIVE_FILE), "verb": "created", "backup": None}, None
    if how == "replace":
        return {"file": str(target), "verb": "replaced", "backup": str(target) + BACKUP_SUFFIX}, None
    return None, {"file": AGENTS_FILE, "alternative": ALTERNATIVE_FILE}


def connect(brain: Brain, agent: str, folder: str, agents_md: str | None = None) -> dict:
    """Register the MCP server with the agent and write the instructions file. Called only from the person's click."""
    command = _known(agent)
    if not shutil.which(command):
        return {"ok": False, "agent": agent, "error": f"{label(agent)} isn't installed on this computer (no `{command}` command found)."}
    stored = connected()
    plan, conflict = _place_file(brain, agent, folder, agents_md, stored.get(agent, {}))
    if conflict:
        return {"ok": False, "agent": agent, "conflict": conflict}
    if agent in stored:
        _run(_remove_command(agent))              # a second click refreshes the registration instead of failing on "already exists"
    failure = _run(_add_command(agent, brain))
    if failure:
        return {"ok": False, "agent": agent, "error": failure}
    text = agents_text(brain)
    target = Path(plan["file"])
    if plan["verb"] == "replaced" and target.exists():
        os.replace(target, plan["backup"])
    target.write_text(text, encoding="utf-8")
    shown = {"replaced": f"replaced {plan['file']} (yours is saved as {Path(plan['backup'] or '').name})"}.get(plan["verb"], f"{plan['verb']} {plan['file']}")
    changes = [shlex.join(_add_command(agent, brain)), shown]
    record = {"connected_at": datetime.now(timezone.utc).isoformat(), "brain": str(brain.root), "folder": folder, "file": plan["file"],
              "digest": _digest(text), "backup": plan["backup"], "changes": changes}
    _save({**stored, agent: record})
    return {"ok": True, "agent": agent, "changes": changes, "record": record}


def disconnect(brain: Brain, agent: str) -> dict:
    """Undo ``connect``: remove the registration, then only the file we wrote (and put back the one it replaced)."""
    _known(agent)
    stored = connected()
    record = stored.get(agent)
    if record is None:
        return {"ok": True, "agent": agent, "changes": []}
    failure = _run(_remove_command(agent))
    if failure:
        return {"ok": False, "agent": agent, "error": failure}
    changes = [shlex.join(_remove_command(agent))]
    target = Path(record.get("file", ""))
    try:
        unchanged = target.is_file() and _digest(target.read_text(encoding="utf-8")) == record.get("digest")
    except (OSError, UnicodeDecodeError):
        unchanged = False
    if unchanged:
        backup = Path(record["backup"]) if record.get("backup") else None
        if backup and backup.exists():
            os.replace(backup, target)
            changes.append(f"restored {target}")
        else:
            target.unlink()
            changes.append(f"removed {target}")
    _save({a: r for a, r in stored.items() if a != agent})
    return {"ok": True, "agent": agent, "changes": changes}


# --- the suggested first question (AC3) ----------------------------------------------------------------------------------
JIRA_FALLBACK = "Which issues were updated most recently, and what changed in them?"
FOLDER_FALLBACK = "Which files changed most recently, and what do they say?"
SCAN_LIMIT = 5000          # a question needs a sample, not every file of a huge folder


def start_command(agent: str, question: str) -> str:
    """The one-line command that starts the agent with the question (copy and paste into a terminal)."""
    return shlex.join([_known(agent), question])


def _scan(root: Path) -> list[tuple[dict, str]]:
    from .frontmatter import parse_document

    found = []
    for path in sorted(root.rglob("*.md"))[:SCAN_LIMIT]:
        try:
            found.append(parse_document(path.read_text(encoding="utf-8", errors="replace")))
        except Exception:               # noqa: BLE001 - one unreadable file must not hide the question
            continue
    return found


def suggest_question(brain: Brain, pipeline: str) -> dict | None:
    """A question the synced files can answer, or None when nothing is synced.

    Built only from what exists: Jira asks about the largest project, and only if one of its issues is in progress; a folder
    asks about open decisions, and only if some file mentions one. Otherwise the question that always works, with the reason.
    ``evidence`` is how many files the question can be answered from.
    """
    from collections import Counter

    from .config import load_config
    from .destinations import directory_root

    p = load_config(brain).get(pipeline)
    if p is None:
        return None
    docs = _scan(directory_root(brain, p.destination))
    if not docs:
        return None
    issues = [(front["jira"], body) for front, body in docs if isinstance(front.get("jira"), dict) and front["jira"].get("key")]
    if issues:
        project = Counter(str(j["key"]).rsplit("-", 1)[0] for j, _ in issues).most_common(1)[0][0]
        active = [j for j, _ in issues if str(j["key"]).rsplit("-", 1)[0] == project and j.get("status_category") == "In Progress"]
        if active:
            return {"question": f"What is in progress in {project} right now, and what is blocked?", "fallback": False, "reason": "", "evidence": len(active)}
        return {"question": JIRA_FALLBACK, "fallback": True, "evidence": len(issues),
                "reason": f"No issue in {project} is in progress right now, so this question works on any synced files."}
    mentions = [body for _front, body in docs if "decision" in body.lower()]
    if mentions:
        return {"question": f"Summarize {pipeline} and list the open decisions.", "fallback": False, "reason": "", "evidence": len(mentions)}
    return {"question": FOLDER_FALLBACK, "fallback": True, "evidence": len(docs),
            "reason": f"No file in {pipeline} mentions a decision, so this question works on any synced files."}


# --- the local read count (AC7) and the view the UI draws --------------------------------------------------------------
READ_TOOLS = {"search", "read", "list"}


def reads_since_connect(brain: Brain, agent: str) -> int:
    """How many times an agent read through the MCP server since the person connected: from the local activity log only.

    The log keeps tool names and times, so this counts calls (a search, a read or a list), and cannot tell which agent made them:
    it is shown as "your agent". Nothing here is sent anywhere.
    """
    from .log import ActivityLog

    since = connected().get(agent, {}).get("connected_at")
    if not since:
        return 0
    start = datetime.fromisoformat(since)
    count = 0
    for event in ActivityLog(brain).read(types={"mcp_call"}):
        try:
            when = datetime.fromisoformat(str(event.get("ts", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= start and event.get("tool") in READ_TOOLS:
            count += 1
    return count


def view(brain: Brain, pipeline: str) -> dict:
    """Everything the Ready screen and Settings need to draw the agent step for one pipeline."""
    from .config import load_config
    from .destinations import directory_root
    from .snippets import all_snippets

    p = load_config(brain).get(pipeline)
    folder = str(directory_root(brain, p.destination)) if p is not None else ""
    question = suggest_question(brain, pipeline)
    return {
        "folder": folder,
        "found": [{"agent": a, "label": label(a), "exact": exact_change(brain, a, folder)} for a in detect()],
        "connected": {a: {"label": label(a), "changes": r.get("changes", []), "folder": r.get("folder", ""), "reads": reads_since_connect(brain, a)}
                      for a, r in connected().items()},
        "question": question,
        "commands": {a: start_command(a, question["question"]) for a in AGENTS} if question else {},
        "manual": all_snippets(brain),
    }
