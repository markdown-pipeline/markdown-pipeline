# GitHub target spike (MD-59): API approach, limits and the fake

Status: written from GitHub's REST documentation (Git Database API, Repos, Branches, Rate limits). It has **not** been checked
against a live scratch repository: no scratch repo or throw-away token was available to the developer agent, and the owner's
brain repo must never be used for this. Every "to verify" below is a first thing to try with a throw-away repo before the
release gate. The behaviour is encoded in `tests/fake_github.py`, which is what the whole test suite runs against.

## One commit with adds, updates, deletes and renames
Use the Git Data API; the Contents API creates one commit per file and cannot delete and add atomically.

1. `GET /repos/{o}/{r}/git/ref/heads/{branch}` -> head commit sha (H).
2. `GET /repos/{o}/{r}/git/commits/{H}` -> its tree sha (T).
3. For each added or changed file: `POST /git/blobs` `{content: <base64>, encoding: "base64"}` -> blob sha.
4. `POST /git/trees` `{base_tree: T, tree: [{path, mode: "100644", type: "blob", sha}]}`; a deleted path is an entry with `sha: null`;
   a rename is a delete of the old path plus an add of the new one. Paths with `/` are fine (no nested trees to build by hand).
5. `POST /git/commits` `{message, tree, parents: [H]}` -> commit sha C.
6. `PATCH /git/refs/heads/{branch}` `{sha: C, force: false}`.

Steps 1-5 only create unreachable objects. Nothing is visible until step 6, which is a single atomic ref move. So **any failure
before step 6 leaves the branch unchanged** (the unreachable objects are garbage collected by GitHub). Step 6 is the commit point.

## Detecting that the branch moved, retrying without force
`PATCH` with `force: false` answers **422 "Update is not a fast forward"** when the branch is no longer H's descendant-compatible
(someone committed after step 1). The client never sends `force: true`. On 422 the target raises `ConcurrentChange`; the engine
throws the half-built plan away and plans again from the new head (re-reading files and state), so the other commit is kept and
an owned file somebody edited in the meantime is seen as a brain edit (conflict). After `MAX_ATTEMPTS` (3) the sync stops with
"the branch keeps moving" and writes nothing. A forced update would be the only way to lose a commit, so the fake counts them
(`forced_updates`) and a test asserts 0.

## Limits (documented values, to verify)
* Rate limit: 5000 requests/hour for a fine-grained token (primary); secondary limits for bursts of writes. A rate limit answers 403
  (or 429) with `x-ratelimit-remaining: 0` / `retry-after`; the client maps it to `RateLimited` and writes nothing.
* A sync costs about `3 + (changed files) + 3` requests; a dry run costs `3 + (files read)`. Content is read with one blob request
  per owned file (parallelised, 8 workers). 500 files: roughly 500 requests for planning, 500 blobs + 6 for the first write.
* `GET /git/trees/{T}?recursive=1` returns at most 100,000 entries / 7 MB and sets `truncated: true` beyond that. The target treats
  `truncated` as an error (never as "the other files are gone").
* Blob size: 100 MB per blob via this API (raw files are far smaller). Request body size is not a practical limit for Markdown.
* A tree with 500+ new entries is one `POST /git/trees` call (documented limit 100,000 entries).

## Branch protection and permissions before writing
* `GET /user` -> the token owner (also proves the token is valid: 401 otherwise).
* `GET /repos/{o}/{r}` -> `permissions.push`. A fine-grained token with Contents read-only reports `push: false`; a repo the token
  cannot see answers 404.
* `GET /repos/{o}/{r}/branches/{b}` -> `protected` (bool) and the head sha. `protected: true` means some protection rule exists; it
  does not prove direct pushes are blocked (admins can be exempt), so the message is a warning that suggests a dedicated branch
  (e.g. `brain-sync`) and a failed ref update (403 "Protected branch update failed") is reported with the same advice.

## Reading `raw/` and state efficiently for a dry run
One `GET /git/trees/{T}?recursive=1` lists every path with its blob sha; blobs are fetched only for paths the pipeline owns (and the
state file). No clone is needed (D-GT-1).

## History for state recovery
`GET /repos/{o}/{r}/commits?sha={branch}&path=.mdpipeline/state.json&per_page=N` lists the commits that touched the state file,
newest first; each commit's blob is read with `GET /git/commits/{sha}` -> tree -> blob. This is the GitHub equivalent of the local
state backups (MD-39).

## Risks the stories do not cover
1. **`source_root` in provenance** is an absolute local path and ends up in a shared repo (already noted in the spec, privacy).
2. **Commit identity**: a commit created through the API with only `message/tree/parents` is authored by the token owner. Teams with
   "signed commits required" protection will refuse it (reported as a protected-branch error).
3. **Secondary rate limits** on bursts of content creation can answer 403 with `retry-after`; the client does not wait and retry
   inside a sync (a later sync converges), so a very large first sync may take several runs only if it is rate limited.
4. **Case-insensitive collisions** (macOS/Windows clones) between names that differ only by case: the naming policy already
   de-duplicates case-insensitively, hand-written files are the only risk.
5. **Large first sync is not resumable** within one run: it is all-or-nothing by design (D-GT-6).
6. **Eventual consistency**: right after a ref update a read of the same branch may briefly return the old head; the sync result
   uses the commit created, not a re-read.

## The fake (`tests/fake_github.py`)
An in-memory repo behind the same `transport(method, url, headers, body) -> (status, headers, bytes)` call the real client uses.
Scriptable: `commit_as_other`, `before(method, action)` (a race exactly before a request), `fail`, `rate_limit`, `protect`,
`set_permission`, `max_tree_entries`; inspectable: `requests`, `forced_updates`, `history`, `is_linear_history`,
`commit_files_changed`. Its own tests are `tests/test_fake_github.py`.
