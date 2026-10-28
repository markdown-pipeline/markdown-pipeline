"""The local folder browser (MD-142): list folders so a person can pick one instead of typing a path.

Rules (all tested): folder NAMES only, never a file name or content; one level at a time, capped and paged; symlinks are followed
only to directories and never recursed into, so a loop cannot hang a listing; ``..`` is resolved before anything is listed; nothing is
reported to usage events or logged here (the one event, ``folder_pick``, carries a method and no path). It shows what the operating
system user running this app can already see: it has no access of its own, and the HTTP layer only serves it on the loopback address
with the session token and an Origin check.
"""
from __future__ import annotations

import os

from .brain import Brain
from .config import load_config

PAGE = 200
MAX_NAME = 255
SHORTCUTS = (("home", "Home", ""), ("documents", "Documents", "Documents"), ("desktop", "Desktop", "Desktop"))


def wsl_drives() -> list[str]:
    """Windows drives mounted under /mnt when running inside WSL (empty elsewhere)."""
    try:
        with open("/proc/version", encoding="utf-8") as fh:
            if "microsoft" not in fh.read().lower():
                return []
    except OSError:
        return []
    try:
        return sorted(f"/mnt/{d}" for d in os.listdir("/mnt") if len(d) == 1 and d.isalpha() and os.path.isdir(f"/mnt/{d}"))
    except OSError:
        return []


def _home() -> str:
    return os.path.abspath(os.path.expanduser("~"))


def _readable(path: str) -> bool:
    return os.access(path, os.R_OK | os.X_OK)


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=True)           # a link to a file, or a loop (ELOOP), is simply not a folder
    except OSError:
        return False


def _note(kind: str, message: str, nxt: str) -> dict:
    return {"kind": kind, "message": message, "next": nxt}


def _resolve(path: str) -> tuple[str, dict | None]:
    """The folder to show: ``path`` when it is an absolute folder this user can open, else home with a calm note saying why."""
    raw = (path or "").strip()
    if not raw:
        return _home(), None
    if "\x00" in raw:
        return _home(), _note("not_found", "That path can't be used.", "Showing your home folder. Choose a folder from the list.")
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        return _home(), _note("not_absolute", "That is not a full path.", "Showing your home folder. Choose a folder from the list.")
    full = os.path.abspath(expanded)                          # resolves ".." and "." text; symlinks stay as they are
    if not os.path.exists(full):
        return _home(), _note("not_found", "We can't find that folder.", "Showing your home folder. Choose a folder from the list.")
    if not os.path.isdir(full):
        return _home(), _note("not_a_folder", "That is a file, not a folder.", "Showing your home folder. Choose a folder from the list.")
    if not _readable(full):
        parent = os.path.dirname(full) or os.sep
        shown = parent if os.path.isdir(parent) and _readable(parent) else _home()
        return shown, _note("permission", "This computer doesn't let this app open that folder.", "Choose another folder, or change its permissions.")
    return full, None


def _crumbs(path: str) -> list[dict]:
    out, built = [{"name": os.sep, "path": os.sep}], os.sep
    for part in [p for p in path.split(os.sep) if p]:
        built = os.path.join(built, part)
        out.append({"name": part, "path": built})
    return out


def _recent(brain: Brain | None) -> list[str]:
    if brain is None:
        return []
    found: list[str] = []
    try:
        for p in load_config(brain).pipelines:
            paths = [s.path for s in p.sources if s.type == "folder" and s.path]
            if p.destination and p.destination.get("type") == "directory" and p.destination.get("path"):
                paths.append(str(p.destination["path"]))
            for path in paths:
                full = os.path.abspath(os.path.expanduser(path))
                if full not in found and os.path.isdir(full) and _readable(full):
                    found.append(full)
    except Exception:                                         # noqa: BLE001  (a broken config must not break browsing)
        return []
    return found[:5]


def shortcuts(brain: Brain | None) -> list[dict]:
    home, out = _home(), []
    for sid, label, sub in SHORTCUTS:
        full = os.path.join(home, sub) if sub else home
        if os.path.isdir(full):
            out.append({"id": sid, "label": label, "path": full})
    out += [{"id": "recent", "label": os.path.basename(p) or p, "path": p} for p in _recent(brain)]
    out += [{"id": "drive", "label": f"Drive {os.path.basename(p).upper()}:", "path": p} for p in wsl_drives()]
    return out


def list_dir(brain: Brain | None, path: str, *, show_hidden: bool = False, start: int = 0) -> dict:
    """One page of the folders inside ``path`` (see ``_resolve`` for what is shown when ``path`` can't be used)."""
    shown, note = _resolve(path)
    names: list[tuple[str, bool]] = []
    try:
        with os.scandir(shown) as it:
            for entry in it:
                if (show_hidden or not entry.name.startswith(".")) and _is_dir(entry):
                    names.append((entry.name, _readable(entry.path)))
    except OSError:
        note = note or _note("permission", "This computer doesn't let this app open that folder.", "Choose another folder.")
    names.sort(key=lambda n: (n[0].lower(), n[0]))
    start = max(int(start or 0), 0)
    page = names[start:start + PAGE]
    parent = os.path.dirname(shown)
    out = {"ok": True, "path": shown, "parent": parent if parent and parent != shown else None, "crumbs": _crumbs(shown),
           "dirs": [{"name": n, "path": os.path.join(shown, n), "readable": ok,
                     **({} if ok else {"reason": "This computer doesn't let this app open it."})} for n, ok in page],
           "total": len(names), "next_start": start + len(page), "is_last": start + len(page) >= len(names),
           "shortcuts": shortcuts(brain), "show_hidden": bool(show_hidden)}
    if note:
        out["note"] = note
    return out


class NewFolderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def new_folder(parent: str, name: str) -> dict:
    """Create ONE directory called ``name`` inside the existing folder ``parent`` (the output case only). Never overwrites."""
    plain = str(name or "").strip()
    if not plain or plain in (".", "..") or len(plain) > MAX_NAME or any(c in plain for c in ("/", "\\", "\x00")):
        raise NewFolderError(400, "Use a single folder name, without slashes.")
    base = os.path.abspath(os.path.expanduser(str(parent or "")))
    if "\x00" in str(parent) or not os.path.isdir(base):
        raise NewFolderError(400, "Choose an existing folder to create it in.")
    target = os.path.join(base, plain)
    try:
        os.mkdir(target)
    except FileExistsError:
        raise NewFolderError(409, f"A folder named {plain} is already there. Choose it from the list instead.") from None
    except OSError:
        raise NewFolderError(400, "This computer doesn't let this app create a folder there.") from None
    return {"ok": True, "path": target}
