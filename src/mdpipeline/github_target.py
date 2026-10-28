"""The GitHub repository target (MD-61..MD-65): the brain's ``raw/`` files and the pipeline state live in a repo branch.

Reads come from one snapshot of the branch head (``begin``): a recursive tree listing, plus blobs fetched on demand and
cached by sha. Writes are staged in memory and become ONE commit at ``commit``: blobs, a tree built on the head's tree,
a commit whose parent is that head, then a fast-forward ref update (``force`` is never sent). Nothing is visible before
the ref update, so a failure at any step leaves the branch unchanged; if the branch moved, ``ConcurrentChange`` is raised
and the engine plans again on the new head, so a commit by someone else is never dropped.

The target only ever changes ``raw/**/*.md`` and the state file (``STATE_PATH``).
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

from .boundary import check_write_target
from .github_api import GitHubApi
from .target import CommitInfo, FileRead, Target, TargetError
from .writer import WriteRefused

STATE_PATH = ".mdpipeline/state.json"
PREFETCH_WORKERS = 8
PLAN_READ_WORKERS = 16        # MD-128: reads only; ~80 files at ~0.4 s per request plan in ~2 s instead of ~32 s
REGULAR_MODES = ("100644", "100755")


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class GitHubTarget(Target):
    kind = "github"
    atomic = True
    state_in_repo = True

    def __init__(self, api: GitHubApi, repo: str, branch: str, *, label: str = "", clone: str = "") -> None:
        self.api, self.repo, self.branch = api, repo, branch
        self.clone = clone                       # optional local clone for agents (MD-67)
        self._label = label or f"github {repo} ({branch})"
        self.head: str | None = None
        self.tree_sha: str | None = None
        self.entries: dict[str, tuple[str, str, str]] = {}      # path -> (type, mode, blob sha)
        self._blobs: dict[str, bytes] = {}                       # blob sha -> content (content addressed, safe to keep)
        self._writes: dict[str, bytes] = {}
        self._deletes: set[str] = set()
        self._state: bytes | None = None
        self.last_commit: CommitInfo | None = None

    def describe(self) -> str:
        return self._label

    # -- reading ---------------------------------------------------------------------
    def begin(self) -> None:
        self._writes, self._deletes, self._state = {}, set(), None
        self.api.forget_token()
        self.head = self.api.head(self.repo, self.branch)
        self.tree_sha = self.api.commit(self.repo, self.head)["tree"]["sha"]
        self.entries = {e["path"]: (e["type"], e.get("mode", ""), e["sha"]) for e in self.api.tree(self.repo, self.tree_sha)}

    def _ensure(self) -> None:
        if self.head is None:
            self.begin()

    def _blob(self, sha: str) -> bytes:
        if sha not in self._blobs:
            self._blobs[sha] = self.api.blob(self.repo, sha)
        return self._blobs[sha]

    def prefetch(self, rels: list[str]) -> None:
        """Download the contents of these paths in parallel. A failed download is left for ``read_file`` to raise where the file is
        needed, so it is reported exactly as before and is never taken for an absent file."""
        self._ensure()
        wanted = sorted({self.entries[r][2] for r in rels if r in self.entries and self.entries[r][0] == "blob"} - set(self._blobs))
        if len(wanted) < 2:
            return

        def fetch(sha: str) -> bytes | None:
            try:
                return self.api.blob(self.repo, sha)
            except TargetError:
                return None

        with ThreadPoolExecutor(PLAN_READ_WORKERS) as pool:
            for sha, data in zip(wanted, pool.map(fetch, wanted)):
                if data is not None:
                    self._blobs[sha] = data

    def read_file(self, rel: str) -> FileRead:
        self._ensure()
        if rel in self._writes:
            return FileRead("file", self._writes[rel])
        if rel in self._deletes:
            return FileRead("absent")
        entry = self.entries.get(rel)
        if entry is None:
            prefix = rel + "/"
            return FileRead("other") if any(p.startswith(prefix) for p in self.entries) else FileRead("absent")
        kind, mode, sha = entry
        if kind != "blob" or mode not in REGULAR_MODES:
            return FileRead("other")
        return FileRead("file", self._blob(sha))

    def list_markdown(self) -> list[str]:
        self._ensure()
        paths = {p for p, (kind, mode, _) in self.entries.items()
                 if check_write_target(p) is None and kind == "blob" and mode in REGULAR_MODES}          # MD-133: the whole repo/branch, not just raw/
        paths = (paths | {p for p in self._writes if check_write_target(p) is None}) - self._deletes
        return sorted(paths)

    def read_state(self) -> bytes | None:
        self._ensure()
        if self._state is not None:
            return self._state
        entry = self.entries.get(STATE_PATH)
        return self._blob(entry[2]) if entry and entry[0] == "blob" else None

    def history_of_state(self, limit: int = 10) -> list[dict]:
        """Recent commits that touched the state file (newest first), for recovery."""
        return self.api.history(self.repo, self.branch, STATE_PATH, limit)

    def state_at(self, commit_sha: str) -> bytes | None:
        """The state file as it was in ``commit_sha`` (None when the commit has none)."""
        tree = self.api.commit(self.repo, commit_sha)["tree"]["sha"]
        entry = {e["path"]: e for e in self.api.tree(self.repo, tree)}.get(STATE_PATH)
        return self.api.blob(self.repo, entry["sha"]) if entry and entry["type"] == "blob" else None

    # -- staging ---------------------------------------------------------------------
    @staticmethod
    def _check(rel: str) -> None:
        reason = check_write_target(rel)
        if reason:
            raise WriteRefused(f"Refused {rel!r}: {reason}.")

    def write(self, rel: str, data: bytes) -> None:
        self._check(rel)
        self._deletes.discard(rel)
        self._writes[rel] = data

    def delete(self, rel: str) -> None:
        self._check(rel)
        self._writes.pop(rel, None)
        self._deletes.add(rel)

    def write_state(self, data: bytes) -> None:
        self._state = data

    # -- the one commit --------------------------------------------------------------
    def _changes(self) -> tuple[dict[str, bytes], list[str]]:
        """Net changes against the head: files whose bytes differ, deletions of files that exist."""
        writes = dict(self._writes)
        if self._state is not None:
            writes[STATE_PATH] = self._state
        changed = {p: d for p, d in writes.items()
                   if not (p in self.entries and self.entries[p][0] == "blob" and self.entries[p][2] == git_blob_sha(d) and self.entries[p][1] in REGULAR_MODES)}
        deleted = sorted(p for p in self._deletes if p in self.entries)
        return changed, deleted

    def commit(self, message: str) -> CommitInfo | None:
        self._ensure()
        changed, deleted = self._changes()
        if not changed and not deleted:
            self._writes, self._deletes, self._state = {}, set(), None
            return None
        paths = sorted(changed)
        with ThreadPoolExecutor(PREFETCH_WORKERS) as pool:
            shas = list(pool.map(lambda p: self.api.create_blob(self.repo, changed[p]), paths)) if len(paths) > 1 else \
                [self.api.create_blob(self.repo, changed[p]) for p in paths]
        tree_entries = [{"path": p, "mode": "100644", "type": "blob", "sha": s} for p, s in zip(paths, shas)]
        tree_entries += [{"path": p, "mode": "100644", "type": "blob", "sha": None} for p in deleted]
        tree = self.api.create_tree(self.repo, self.tree_sha or "", tree_entries)
        created = self.api.create_commit(self.repo, message, tree, [self.head or ""])
        self.api.update_ref(self.repo, self.branch, created["sha"])       # the commit point: fast-forward only, never forced
        # the branch is now `created`: move the snapshot there (no re-read needed)
        for p, s in zip(paths, shas):
            self.entries[p] = ("blob", "100644", s)
            self._blobs[s] = changed[p]
        for p in deleted:
            self.entries.pop(p, None)
        self.head, self.tree_sha = created["sha"], tree
        self._writes, self._deletes, self._state = {}, set(), None
        self.last_commit = CommitInfo(created["sha"], created.get("html_url") or f"https://github.com/{self.repo}/commit/{created['sha']}", message)
        return self.last_commit
