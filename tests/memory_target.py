"""An in-memory target used to prove the engine works with any target type (MD-60).

It is deliberately atomic like a repository: writes, deletes and the state are staged and only ``commit`` applies them.
"""
from __future__ import annotations

from mdpipeline.target import CommitInfo, FileRead, Target, TargetError


class MemoryTarget(Target):
    kind = "memory"
    atomic = True
    state_in_repo = True

    def __init__(self, files: dict[str, bytes] | None = None, state: bytes | None = None) -> None:
        self.files = dict(files or {})
        self.state = state
        self.staged_writes: dict[str, bytes] = {}
        self.staged_deletes: list[str] = []
        self.staged_state: bytes | None = None
        self.calls: list[tuple] = []
        self.commits: list[str] = []
        self.fail_reads = False
        self.fail_commit: Exception | None = None

    def describe(self) -> str:
        return "memory"

    def begin(self) -> None:
        self.calls.append(("begin",))
        self.staged_writes, self.staged_deletes, self.staged_state = {}, [], None

    def read_file(self, rel: str) -> FileRead:
        self.calls.append(("read_file", rel))
        if self.fail_reads:
            raise TargetError("the repository is unreachable")
        if rel in self.staged_writes:
            return FileRead("file", self.staged_writes[rel])
        return FileRead("file", self.files[rel]) if rel in self.files else FileRead("absent", None)

    def list_markdown(self) -> list[str]:
        return sorted(p for p in self.files if p.startswith("raw/") and p.endswith(".md"))

    def read_state(self) -> bytes | None:
        if self.fail_reads:
            raise TargetError("the repository is unreachable")
        return self.staged_state if self.staged_state is not None else self.state

    def write_state(self, data: bytes) -> None:
        self.staged_state = data

    def write(self, rel: str, data: bytes) -> None:
        self.calls.append(("write", rel))
        self.staged_writes[rel] = data

    def delete(self, rel: str) -> None:
        self.calls.append(("delete", rel))
        self.staged_deletes.append(rel)

    def commit(self, message: str) -> CommitInfo | None:
        self.calls.append(("commit", message))
        if self.fail_commit:
            raise self.fail_commit
        if not (self.staged_writes or self.staged_deletes or self.staged_state != self.state and self.staged_state is not None):
            return None
        self.files.update(self.staged_writes)
        for rel in self.staged_deletes:
            self.files.pop(rel, None)
        if self.staged_state is not None:
            self.state = self.staged_state
        self.commits.append(message)
        self.staged_writes, self.staged_deletes, self.staged_state = {}, [], None
        return CommitInfo(sha=f"m{len(self.commits)}", url=f"memory://commit/{len(self.commits)}", message=message)
