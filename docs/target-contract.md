# The target contract (MD-60)

A **target** is where a brain's files and the pipeline state live. The engine (planner, guard, ownership, naming,
recovery) knows only this contract (`src/mdpipeline/target.py`). Implementations: `LocalTarget` (a directory, `SafeWriter`)
and `GitHubTarget` (`src/mdpipeline/github_target.py`, one atomic commit per sync). `brain.target` returns the one instance for
a brain; `brain.use_target(t)` replaces it (tests, runners that bring their own).

## Methods
| Method | Meaning |
|---|---|
| `begin()` | Start an operation on a fresh view (a repository target re-reads its head). Called by every sync and dry run. |
| `read_file(rel) -> FileRead` | `absent`, `file` (with bytes) or `other` (symlink, directory...). Reads the current content, including changes staged in this operation. |
| `list_markdown() -> list[str]` | Every `.md` path in the output folder (not `.mdpipeline/`, `wiki/` or hidden folders), for rebuild. |
| `read_state() -> bytes or None` | The state file; `None` when there is none yet. |
| `write_state(bytes)` | Store the state. |
| `write(rel, bytes)` / `delete(rel)` | Change one file in the output folder. |
| `commit(message) -> CommitInfo or None` | Make the staged changes real. `None`: there was nothing to commit. |
| `clean_temp()` | Remove leftovers of an interrupted run. |
| `prefetch(rels)` | Optional hint that these paths will be read. |

Attributes: `atomic` (staged changes become visible all at once at `commit`) and `state_in_repo` (the state is stored in the
target and shared by every machine; the guard's pending approval then stays in a local `.mdpipeline/runtime.json`).

## Applying a change set atomically
The engine stages every `write`, `delete` and `write_state` and calls `commit(message)` once at the end of a sync. For an
`atomic` target nothing is visible before `commit`, and `commit` is all-or-nothing: a failure at any point leaves the target as it
was. The local directory target applies each call immediately (its crash safety comes from the state's `pending_hash`, as before)
and its `commit` does nothing. A sync blocked by the guard, a dry run and a sync with nothing to do never call `commit` with changes.

## Reporting a concurrent change
If someone else changed the target between `begin` and `commit`, `commit` raises `ConcurrentChange` and writes nothing. The
engine then plans again from a fresh `begin` (so the other change is kept, and an owned file edited by the other party is seen as a
brain edit), at most `MAX_ATTEMPTS` times; after that the sync stops with `TargetBusy` ("head-moved") and nothing was written.

## Error semantics
Every failure is a `TargetError` (`TargetAuthError`, `TargetRateLimited`, `TargetUnavailable`, `TargetProtected`,
`ConcurrentChange`, `TargetBusy`), each with a `category` and a user-facing message. A failed read is **never read as** "the file
is not there": `read_file`, `list_markdown` and `read_state` raise instead of returning `absent`/`None`, so a network or auth
failure can never turn into a deletion plan. The engine turns a `TargetError` into a sync result `error` with the message and
category, and writes nothing.

## The write boundary is the engine's
Before any `write` or `delete` the engine checks `boundary.check_write_target`: a relative POSIX path inside the output folder (its top level is never `.mdpipeline/`, `wiki/` or a hidden folder), no `..`,
no empty segments, never `wiki/`, only `.md` files. A target cannot forget it, and the symlink rules of the local writer
(`SafeWriter`) are an additional filesystem-level check of the local target. A repository target has no symlinks to follow and
writes only regular-file blobs.

## Adding a target
Implement the methods above and return it from `make_target`; no planner, guard, naming, state or engine change is needed.
`tests/memory_target.py` is a minimal atomic implementation and `tests/test_target_contract.py` runs the engine on it.
