# Markdown Pipeline

Markdown Pipeline turns your team's tools into clean Markdown files **on your own computer**, and keeps them up to date while the computer is
on. Your coding agent (Claude Code, Codex) then reads those files through a local server. Early access: Jira Cloud and folders on your
computer are supported now.

- **Local.** It runs on your computer. Your files stay in a folder you choose. Nothing is uploaded unless you choose a GitHub repository as the destination.
- **Read-only.** It reads your sources and never changes them. Your Jira data and your source folders are only read.
- **Raw layer only.** It does not summarize or analyze anything. Your agent does that, from the files.
- **What is sent.** Anonymous usage data only if you choose to share it, and the words you type in a feedback or source request when you press
  Send. The full list, in plain words: [What we send](what-we-send.md). Turn it off any time: `mdpipeline usage off`.

## Get started (a few minutes)

1. **Install** (Linux; one command, nothing system-wide, no administrator rights):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.sh | sh
   ```
   Problems? [Troubleshooting: install](troubleshooting-install.md).
2. **Open it.** Type `mdpipeline`. Your browser opens the app.
3. **Choose a source.** The first screen has three paths, with no setup to read first:
   - **Try with sample data**: a made-up team's Jira and docs. No sign-in, no network. Good for seeing what your agent will get.
   - **Folder on this computer**: Markdown and text files, no sign-in.
   - **Jira Cloud**: you need an API token, about 2 minutes. [Which token to create, and what the errors mean](troubleshooting-jira-token.md).

   Then choose where the files go: a folder on this computer (suggested: `~/markdown-pipeline/<name>`) or your team's GitHub repo. Review what will be written and press the button.
4. **Connect your agent.** On the last screen press **Connect Claude Code** (or Codex). The app shows the exact command first and only runs it
   when you press the button. Then ask the suggested question. [Troubleshooting: connect your agent](troubleshooting-agent.md).
5. **What runs in the background.** After the first sync a small background process checks for changes every hour while the computer is on
   (including deletions) and starts when you log in to your desktop. On a server or in WSL 2 it stops when you log out unless you run `loginctl enable-linger $USER` once, and after a reboot logging in or running any `mdpipeline` command starts it again (see Scheduled sync). You are told in the app when it turns on. Change the interval on the Pipelines screen, or
   turn it off: `mdpipeline schedule background off`. Nothing syncs by itself after that.
6. **Uninstall.** `mdpipeline uninstall` turns background sync off, removes its login item and prints the one command that removes the
   program. The Markdown files it wrote stay where they are. [More](troubleshooting-install.md#update-and-uninstall).

The rest of this page is the reference for the command line, the settings and the safety rules.

## Platforms and network rule

- Platforms: **Linux is the tested platform** for this release. macOS has the same code paths but is untested; Windows works only through WSL 2 (the app needs POSIX file locking). Python 3.12+ (the installer brings its own) and [uv](https://docs.astral.sh/uv/), or a plain Python 3.12 venv (below).
- Network rule: Network calls come only from the sources and destinations you connect (your Atlassian site; api.github.com and the GitHub sign-in pages if you use a GitHub repository), and, only if you chose "Share usage" or press Send, from usage data to us.i.posthog.com. With folder sources, a local folder and sharing off, the app makes zero network calls. Everything is https; the window listens on 127.0.0.1 only.

## Install

**Linux (tested): one command.** It installs `uv` if you don't have it, then the app with its own Python 3.12, all under your home folder
(no administrator rights, nothing system-wide):

```bash
curl -fsSL https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.sh | sh
```

It installs the latest public release. No GitHub account, token or sign-in is needed. Run the same command again any time to move to the newest release
(your settings and pipelines are kept).

**Without uv: a plain Python venv.** You need Python 3.12 or newer and its `venv` module (on Debian and Ubuntu: `sudo apt install python3-venv`).
Nothing else is installed, nothing is system-wide:

```bash
python3 -m venv ~/.mdpipeline-venv
~/.mdpipeline-venv/bin/pip install ./mdpipeline-<version>-py3-none-any.whl     # the file from https://github.com/markdown-pipeline/markdown-pipeline/releases/latest
~/.mdpipeline-venv/bin/mdpipeline                          # opens the app; put ~/.mdpipeline-venv/bin on your PATH to type just: mdpipeline
```

The dependencies are read from `pyproject.toml` (exact versions are pinned in `uv.lock` for development), so there is no separate
`requirements.txt` to keep in step. `mdpipeline update` and `mdpipeline uninstall` recognise this install as `pip`
(`~/.mdpipeline-venv/bin/pip uninstall mdpipeline`, or just delete the venv folder).

**macOS:** the code paths are the same and should work with the command above, but it has not been tested on a Mac for this release.
**Windows:** the app needs a POSIX system (file locking, the background process), so use **WSL 2** (Ubuntu): in a Windows terminal run
`wsl --install -d Ubuntu`, open Ubuntu, and run the Linux command above inside it. Your Windows files are under `/mnt/c/...` there.
A native Windows installer is not planned for early access.

**Open it:** type `mdpipeline` (or `mdpipeline start`). With no brain registered it makes the default one in `~/markdown-pipeline` and opens
the UI at the first screen. Measured with `scripts/clean_install_check.sh` on Linux: install to first screen in about 4 s (empty package cache,
needs the network) and about 10 s (warm cache).

**Update:** `mdpipeline update` runs the install command again for an install made by the script (for other installs it runs your package manager's
upgrade: `uv tool upgrade`, `pipx upgrade` or `pip install --upgrade`; `--dry-run` shows it). Your configuration, vault and synced files are outside the program and are not touched; the first plan after an update
is what the old version would have planned (tested). Background sync picks up the new version at your next login.

**Uninstall:** `mdpipeline uninstall` turns background sync off, stops the background process and removes its login item, then prints the one
command that removes the program (for example `uv tool uninstall mdpipeline`). Your synced Markdown files, your brains and their
configuration stay where they are. The app's own data (vault, settings) is in the folder it names: delete it too if you want everything gone.

**Troubleshooting:** *`mdpipeline: command not found`* run `uv tool update-shell` and open a new terminal. *Python is too old* the installer
brings its own 3.12, so this only happens with a manual `pip install`: run the install command above instead. *Port 8765 is busy*
`mdpipeline start --port 0` picks a free one. *Background sync is on but not running* `mdpipeline background` starts it (or "Start it" on Home). *Where are my tokens without a keychain?* This computer has no system keychain (common in containers, servers and WSL 2), so the app keeps your tokens in `vault.json` in its data folder (`~/.local/share/mdpipeline`), a file only your user can read (mode 0600); there is no passphrase, and syncs carry on by themselves after a restart. If the file is open to other users the app refuses to use it and shows the `chmod 600` command that fixes it. An older install (v0.1.1 to v0.1.3) asked for a passphrase: enter it once and your tokens move to that file. To use the system keychain instead, install `libsecret-tools` (it provides `secret-tool`; on Debian/Ubuntu: `sudo apt install libsecret-tools`) and a Secret Service such as GNOME Keyring.

For development: `cd app && uv sync`, then run commands with `uv run mdpipeline ...`.

## CLI quickstart (a few minutes)

This is the complete flow with a throw-away demo (it is executed by `tests/test_quickstart.py`). Replace the demo
folders with your own. The brain is simply the output folder you choose (in the real setup, your knowledge repo); new pipelines write under `raw/{relpath}` by default.

```bash quickstart
# a demo brain and a demo project with two folders
mkdir -p ~/demo/brain ~/demo/proj/docs ~/demo/proj/specs
printf '# Pricing\nWe chose usage based pricing.\n' > ~/demo/proj/docs/pricing.md
printf -- '---\ntitle: API spec\n---\n# API\n' > ~/demo/proj/specs/api.md

cd ~/demo/brain
mdpipeline init                                   # register this directory as the brain
mdpipeline pipeline add proj \
    --source docs=~/demo/proj/docs \
    --source specs=~/demo/proj/specs              # one pipeline, two labelled folders
mdpipeline sync proj --dry-run                    # see exactly what would happen; writes nothing
mdpipeline sync proj                              # copy it: raw/<date>_pricing.md and raw/<date>_api.md, inside the brain folder (the output folder you chose)
mdpipeline status                                 # last run, conflicts, guard state, brain age
mdpipeline connect --agent claude                 # prints the MCP snippet for your agent
```

Connect your agent (the snippet already contains your brain's path and the exact command):

- **Claude Code:** run the `claude mcp add mdpipeline -- ...` command that `mdpipeline connect` prints, then ask
  "search the brain for pricing".
- **Codex:** paste the `[mcp_servers.mdpipeline]` block that `mdpipeline connect --agent codex` prints into
  `~/.codex/config.toml`.

The agent client starts the server itself when needed (`mdpipeline --brain DIR mcp`); there is no daemon.

Where the files land is chosen **per pipeline**: by default the brain folder (as above), or add `--directory PATH` (another folder) or
`--github OWNER/REPO` (a repository; sign in with GitHub or give a token, see "GitHub repository as a pipeline's target").

A pipeline can also read **only Jira** (no folder needed): see "A Jira-only pipeline" in the Jira section.

Prefer a browser? `mdpipeline ui` opens the local UI (pipelines, editor with a live dry-run, activity and approvals).

### What the guard and conflicts mean

- **Conflict (a):** a file already exists at the target path and this pipeline did not create it (for example a
  note you wrote by hand). It is skipped and reported, never overwritten, and `--force` does not change that.
  If its provenance says it was copied from this very source (for example after a state rebuild), the message adds
  the next step: **To let the pipeline own it again:** move your copy out of the output folder if it has edits you want to keep (or delete it), then sync; the copy is recreated from the source. **Deleting loses any edits made in the brain**; moving keeps them.
- **Conflict (b):** a file the pipeline created was edited in the brain since the last sync. It is skipped and
  reported. `mdpipeline sync NAME --force` overwrites it with the source version.
- **Big-change guard:** if a sync would delete or overwrite more than 40% of a pipeline's files (and at least 5
  files), for example because a source folder was renamed, it stops and writes nothing. You see the file list and
  approve exactly that change set with `mdpipeline sync NAME --approve`, or cancel it with `mdpipeline cancel NAME`
  (or use the UI). The MCP `sync` tool can never approve.
- A **missing or unreadable source folder** is reported as an error and nothing it owns is deleted.

## Concepts

- **Brain:** any directory (the output folder; `raw/` is not required). Its configuration lives in `<brain>/.mdpipeline/config.yaml` (human readable,
  commit it to git). Runtime files (`state.json`, `log.jsonl`, `lock`, `tmp/`) are git-ignored by a
  `.mdpipeline/.gitignore`. There is no global registry: the brain is the directory that holds `.mdpipeline/`
  (found by walking up from the current directory, or `--brain DIR`). Several brains on one machine simply work
  independently (nothing is shared); one is the supported setup.
- **Pipeline:** one or more sources (folders, Jira) to a **target** (where the files land) and an optional path template (by default each file goes to `raw/` plus its relative path; leave the template empty to write to the root of the output folder),
  with include/exclude rules, an on-delete policy and a guard threshold.
- **Target:** part of each pipeline: the brain's own folder (the default), another folder (`--directory`), or a GitHub repository
  branch (`--github`). Each pipeline can have a different target; they sync independently. State (ownership) lives with each target.
  See "GitHub repository as a pipeline's target". Changing it later is `pipeline change-target`.
- **Vault:** the app's own store for secrets on this computer (encrypted with an OS keychain; without one, a file only your user can read). It holds a GitHub token or sign-in, a Jira email and API token. You enter a secret in the
  app once (never in an environment variable or a file); the configuration holds only `vault:<id>`. See "The app vault".
- **Ownership:** a pipeline may only update or delete files it created. Ownership is recorded in
  `.mdpipeline/state.json` (target path to source and the hash of the bytes last written) and is also visible in
  the file's `provenance:`. Without a state record a file is *unowned*, whatever its provenance says.

### Config example

```yaml
version: 1
pipelines:
- name: proj
  sources:
  - {path: /home/me/proj/docs, label: docs}
  - {path: /home/me/proj/specs, label: specs}
  target: 'raw/{relpath}'           # the default for new pipelines; empty = the root of the output folder; older pipelines keep their own template
  include: ['**/*.md']
  exclude: ['.git/**', 'node_modules/**', '.obsidian/**', '**/.*/**', '**/node_modules/**']
  on_source_delete: delete        # or keep
  guard: {threshold_percent: 40, min_files: 5}
  paused: false
  # destination: {type: github, repo: acme/team-brain, branch: main, auth: sign-in, credential: 'vault:v_0a1b2c3d'}   # optional: where the files land
```

An older configuration with one brain-wide `target:` is converted on first use (a backup is kept in `.mdpipeline/backups/`; the first plan shows 0 changes).

### Target template variables

The template is relative to the **output folder** and optional: leave it empty (the default for new pipelines) and each file goes to its relative path at the root of the folder, or write your own, `raw/{pipeline}/{source}/{relpath}` included. It must resolve inside the output folder, never into `.mdpipeline/`, `wiki/` or a hidden folder, The app only creates and changes files it created. Your own files in the same folder are never overwritten or deleted; if a file would collide with one of yours, you are told and your file is left alone. Pipelines created in earlier versions keep their `raw/...` template: no file moves.

| Variable | Meaning (example for `docs/pricing.md` in a folder labelled `main`) |
|---|---|
| `{pipeline}` | the pipeline name |
| `{source}` | the folder's label: `main` |
| `{relpath}` | path relative to the source folder, **with** directories, file name and extension: `docs/pricing.md` |
| `{dir}` | the directory part of `relpath`: `docs` (empty for files at the folder root) |
| `{name}` | file name without its final extension: `pricing` |
| `{ext}` | extension without the dot: `md` |
| `{mtime:FMT}` | the source file's modification time in UTC; FMT may contain `YYYY`, `MM`, `DD` and `-` `_` `.` only |

Empty path segments collapse. Include globs: `*` and `?` stay inside a path segment, `**/` matches any number of
directories. Files matching an include glob but not ending in `.md` are skipped with the reason "not a .md file";
they are never renamed.

### File-name policy (what the copied files are called)

The target template decides the **folders**; the file-name policy decides only the **file name**. Choose it per
pipeline with `--name-policy PRESET` or `--name-pattern PATTERN` on `pipeline add` / `pipeline edit`, or in the
UI editor ("File names"). `pipeline list` shows it.

| Policy | Result for `Meeting Notes.md`, first copied 2026-10-04 |
|---|---|
| `date-name` (default for new pipelines) | `2026-10-04_meeting-notes.md` |
| `name` | `meeting-notes.md` |
| `source-name` | `docs--meeting-notes.md` |
| `date-source-name` | `2026-10-04_docs_meeting-notes.md` |
| `original` | `Meeting Notes.md` (the source name unchanged; what pipelines created before this feature use) |
| custom | your pattern, e.g. `{date}.{slug}` |

Custom pattern variables: `{date}` `{slug}` `{name}` (original name, no extension) `{source}` `{pipeline}`. Between
them only `-`, `_` and `.` (and letters/digits) are allowed; a pattern without `{slug}` or `{name}`, with a `/`,
or with `..` is rejected when you save. The file always ends in `.md` and stays inside the output folder.

`{slug}` is the source name lowercased (Unicode NFC), keeping letters and digits of any script (Hebrew
included); every run of other characters becomes one `-`, `_` never appears, at most 80 characters. `{date}` is the
UTC date the pipeline first copied the file, unless the source name starts with `YYYY-MM-DD` or `YYYYMMDD`
followed by a separator: that date is used and removed from the slug.

**Names stay stable.** The name is chosen once, when the file is first copied, and stored in
`.mdpipeline/state.json` with the first-seen date. Editing the source file updates the brain file in place under
the same name; renaming or moving a source file is a delete + create (per your on-delete policy), and the new file
gets a new name.

**Name clashes.** If two source files (in any pipeline of the brain) would get the same name, compared
case-insensitively and Unicode-normalized, the file that already owns the name keeps it and the newcomer gets a
6-hex suffix from its source label and path, for example `2026-10-04_readme-3f9a1c.md`. If both are new in the
same sync, the one whose label and path sort first keeps the clean name. The suffix is stored and never changes,
and it stays even if the other file is later deleted. A clash never overwrites a file the pipeline does not own.

**Changing the policy of a pipeline that already has files** renames the files it owns (and only those). `mdpipeline
pipeline edit NAME --name-policy POLICY` only saves the setting; `mdpipeline sync NAME --dry-run` then lists every
rename (old path -> new path), and the next sync applies it. The renames count toward the big-change guard, so
above its threshold you approve exactly that plan (`--approve`, or in the UI). A rename writes the new file before
removing the old one, so an interrupted run converges when you run it again. A hand-written file, or an owned file
you edited, is never renamed or overwritten. Links inside raw files are **not** rewritten.

Defaults: every new pipeline starts with `date-name`, whether you create it from the first run, the sample, the editor or the
CLI (the policy is saved explicitly in the configuration). `original` stays available as a choice.

Upgrading changes nothing: pipelines saved before this feature stay on `original`, and no file is renamed.

### Provenance block

```yaml
provenance:
  source: local
  pipeline: proj
  source_label: docs
  source_path: pricing.md              # relative to the source folder, POSIX
  original_name: pricing.md            # (file-name policies other than `original`) the name at the source
  first_seen: '2026-10-01'             # (same) the UTC date the pipeline first copied the file
  name_policy: date-name               # (same) the preset name, or the custom pattern
  name_suffix: 3f9a1c                  # (same) only when a clash suffix was applied
  source_root: /home/me/proj/docs      # the source folder as configured (a GitHub target shows `local:<label>` instead: no machine path in a shared repo)
  source_modified: '2026-09-30T14:02:00Z'   # ISO 8601 UTC
  synced_at: '2026-10-01T09:00:00Z'         # changes only when the file is written
  content_hash: sha256:...              # of the SOURCE file's bytes
```

The author's own front-matter text is kept verbatim and the block is appended; the body is copied byte for byte.
A source file that already has a `provenance` key, or whose front-matter is malformed (unterminated, invalid YAML,
not a mapping, not UTF-8), is reported as an error and skipped.

## CLI

`mdpipeline [--brain DIR] COMMAND`. Use `--help` on any command.

| Command | Does |
|---|---|
| `init [DIR]` | register a directory (any folder) as the brain; safe to repeat |
| `init [DIR] [--create]` | (with `--create`: make the folder first) |
| `pipeline change-target NAME (--directory PATH \| --github OWNER/REPO [...] \| --brain-folder) [--apply]` | move a pipeline to another target: the plan first (0 changes when the files are already there), then ownership is carried over; the old target is never touched |
| `pipeline sign-in NAME` / `pipeline sign-out NAME` | sign in with GitHub again / remove the sign-in from the vault |
| `github-app show` / `github-app set --client-id ID --slug SLUG` / `github-app reset` | the GitHub App behind "Sign in with GitHub": show it and whether it is the shipped default or your own, use your own, go back to the shipped default |
| `pipeline test-target NAME` | test the pipeline's target: for GitHub the user, repo, branch head, whether a write would be allowed (writes nothing) |
| `pipeline add NAME ... [--directory PATH \| --github OWNER/REPO [--branch B] [--auth token\|sign-in] [--clone DIR] [--stdin] [--use-secret ID]]` | choose where the pipeline's files land |
| `pipeline edit NAME [--clone DIR \| --remove-clone] [--replace-token] [--remove-token]` | change the GitHub target's clone or token |
| `vault add\|replace\|remove\|list\|status` | the app vault: add (hidden prompt or `--stdin`), replace, remove, list (never shows values) |
| `pipeline add NAME --source [LABEL=]PATH ... [--target T --include G --exclude G --on-delete delete\|keep --name-policy P \| --name-pattern PATTERN --guard-percent N --guard-min-files N]` | create a pipeline (validated) |
| `pipeline show NAME [--json]` | one pipeline's whole setup and status, read-only (the CLI twin of the UI's view mode); credentials only as method, set / not set and name |
| `pipeline list [--json]` | sources (each with its type and label), template, policy, status, last sync |
| `pipeline edit NAME [--source-off LABEL] [--source-on LABEL [--dry-run]]` | switch a source Off or back On (keeps its setup; an Off source is not read and its files are kept) |
| `pipeline edit NAME [--add-source ..] [--remove-source LABEL [--dry-run] [--delete-files] [--approve]] [--rename-label OLD NEW] [rule flags]` | change a pipeline (same validation). `--remove-source` works for a folder or a Jira source; a pipeline always keeps at least one source |
| `pipeline add NAME [--source ...] --jira-label L --jira-site URL --jira-jql JQL --jira-email REF --jira-token REF` | add a Jira source (alone or next to folders); validated against Jira, nothing saved on failure |
| `pipeline edit NAME --jira-label L [--jira-jql JQL ...]` | change a Jira source's query (or add another with all the `--jira-*` flags) |
| `jira test (--pipeline P --source L \| --jira-site ... )` | the account's display name and how many issues the JQL matches (read-only) |
| `pipeline remove NAME [--purge] [--yes]` | remove a pipeline; **its files stay**, unowned, unless `--purge` |
| `pipeline pause NAME` / `pipeline resume NAME` | pause or resume (a paused pipeline is skipped by every trigger) |
| `sync [NAME \| --all] [--dry-run] [--approve] [--force] [--json]` | sync |
| `state rebuild [--pipeline NAME] [--apply] [--approve]` | rebuild the state from the provenance in the output folder when no backup is usable |
| `state restore [--pipeline NAME] [--generation N] [--apply]` | show what a backup of the ownership state would do (writes nothing); `--apply` restores it |
| `cancel NAME` | cancel the change set a guard-blocked sync showed |
| `status [NAME] [--json]` | last run, result, counts, conflicts, guard state, brain age |
| `log [--pipeline P] [--limit N] [--json]` | the local activity log |
| `connect [--agent claude\|codex]` | print the MCP config snippet |
| `mcp` | run the MCP server on stdio (your agent client starts this) |
| `start [--port N] [--no-browser]` (or just `mdpipeline`) | open the app; makes the default brain `~/markdown-pipeline` on a clean machine |
| `ui [--port N] [--no-browser]` | open the local web UI for the brain at or above here |
| `update [--dry-run]` | update the app with the package manager you installed it with |
| `uninstall` | stop background sync, remove its login item, print the command that removes the program (files are kept) |
| `schedule [show]` | each pipeline's interval, last sync and next sync, and whether background sync is on |
| `schedule set NAME 15m\|1h\|6h\|daily\|off` | how often one pipeline syncs by itself (default `1h`) |
| `schedule background on\|off` | the opt-out: turn the background process (and its login item) off or on |
| `background` | run the background process in this terminal (the login item runs this; only one runs at a time) |

**Exit codes:** `0` ok (also paused / nothing to do) - `1` runtime error, or a sync whose result is `error` -
`2` validation or usage error - `3` blocked by the guard - `4` finished with conflicts - `5` busy (another sync is
running; retry later).

Run results: `ok`, `conflicts`, `blocked`, `cancelled`, `error`, `skipped: paused`, `busy`.

## Scheduled sync

After a pipeline's first sync it syncs by itself: **every hour while the computer is on**, per pipeline `15m`, `1h`, `6h`, `daily` or `off`
(`mdpipeline schedule set NAME 6h`; the interval lives in `config.yaml` as `schedule:` and is only written when it is not `1h`).

- **A background process** (`mdpipeline background`, started at login by a login item) keeps running when the window is closed. The first
  successful sync turns it on and says so in plain words, with how to change or turn it off. In the UI the words stay on Home (across reloads) until you click OK, Change or Turn off; a terminal that printed them counts as seen. Opt out any time:
  `mdpipeline schedule background off` (removes the login item and does no scheduled work; it never turns itself back on).
  Login items: Linux `~/.config/autostart/mdpipeline-background.desktop`, macOS `~/Library/LaunchAgents/com.markdownpipeline.background.plist`,
  Windows the Startup folder. Set `MDPIPELINE_NO_BACKGROUND_START=1` to install the login item without starting a process now.
- **After a reboot on a server or in WSL 2.** Where `systemctl --user` works, turning background sync on also enables a **systemd user service**
  (`~/.config/systemd/user/mdpipeline-background.service`): background sync then runs once you log in by any means, SSH included, with no click.
  To have it start right after a reboot, before anyone logs in, run this once yourself (the app never runs it for you): `loginctl enable-linger $USER` (if it says access denied: `sudo loginctl enable-linger $USER`). Without it, background sync stops when you log out of the server and, after a reboot, waits until you log in; the app says so on the first-run Ready step, on Home (you can hide it) and in the output of `mdpipeline schedule`.
  Where there is no systemd (WSL 2 without it), or it did not start: when background sync is on but not running, opening the app or running any
  `mdpipeline` command starts it again by itself. Only one background process ever runs. `mdpipeline schedule background off` and `mdpipeline uninstall` remove the login item and the service.
- **If it is on but not running** and the automatic start did not work, Home says so quietly with a "Start it" button and `mdpipeline schedule` says "on but not running".
- **After sleep or a reboot** it catches up once: a pipeline that is overdue runs one time, never a backlog.
- **A scheduled run is a normal run** (trigger `schedule`): same plan, guard and conflict rules. A failed, partial or unauthorised read never
  deletes files. A guard-blocked pipeline waits for your approval and is not retried every interval.
- **Notices** (an OS notification + a "Needs you" item), one per problem, raised again only if it comes back after being fixed:
  `sync_failed` (failed twice in a row), `source_missing`, `stale` (no successful sync for 24 hours the computer was **awake**: a weekend with
  the laptop closed counts for nothing, and the catch-up runs first; never raised on top of another notice for the same pipeline), `guard_blocked` (a decision is waiting). The OS asks for notification permission at the first
  real notice, never during the first run. A notice names the pipeline only, never a file, title or error text.
- Settings, runtime counters and the registered brains are one file in the app data directory (`scheduler.json`), never inside a brain.
- Usage events (content-free, see `docs/usage-events.md`): `sync_completed(trigger)`, `sync_failed(error_class)`, `notice_shown(kind)`,
  `schedule_changed(old_interval, new_interval)`.

UI API (for the Home / Settings screens): `GET /api/state` carries per pipeline `schedule`, `last_sync`, `next_sync`, and top-level `notices`
(the "Needs you" items) and `background` (`enabled`, `announced`, `running`); `POST /api/pipelines/NAME/schedule {"interval"}`;
`POST /api/background {"enabled"}`; `background.announce_pending` / `announcement` stay in the state until `POST /api/background/ack`.

## If `state.json` gets corrupted

Every save of `.mdpipeline/state.json` first keeps the previous valid copy in `.mdpipeline/backups/`
(`state-1.json` is the newest, up to 5 generations; git-ignored; they hold paths and hashes only, no document
content). A sync that cannot read the state still stops and writes nothing: an empty state would make every copied
file unowned. Run `mdpipeline state restore` to see what the next sync of every pipeline would do with the newest
good backup (nothing is written), then `mdpipeline state restore --apply`. The UI offers the same under
**Needs you**. The unreadable file is kept as `.mdpipeline/backups/state-before-restore.json`.

If there is no usable backup (or you deleted `state.json`), `mdpipeline state rebuild` scans the output folder, reads each
file's provenance and re-renders what the **current source** would produce. A byte-exact match is owned again, with
its name, first-seen date and suffix taken from the provenance (never recomputed). Everything else stays unowned and
is listed: an edited brain copy, a changed source ("cannot tell whether the source or the brain copy changed"), a
hand-written note with copied provenance, a pipeline or folder that does not exist, two files claiming one source.
It writes no Markdown file; it shows the next-sync dry run and the guard first (`--apply` to write the state,
`--approve` if the guard would block the next sync). It refuses while the state is readable and owns files.

To resolve a file the rebuild left unowned (it shows as conflict (a) in the CLI, `status` and **Needs you**): **To let the pipeline own it again:** move your copy out of the output folder if it has edits you want to keep (or delete it), then sync; the copy is recreated from the source. **Deleting loses any edits made in the brain**; moving keeps them.

## MCP server

Started on demand by the agent client over stdio (`mdpipeline --brain DIR mcp`). Readable scope: Markdown under
`raw/` and `wiki/`, plus every file a pipeline wrote anywhere in the output folder (recognised by its provenance block); your own files in the same folder (a README, `work/`), `.mdpipeline/`, hidden folders, absolute paths, `..`, symlinks and non-Markdown are refused.

| Tool | Does |
|---|---|
| `search(query, path_prefix?, limit?)` | ranked files, matching passages, provenance. Keyword/full-text (BM25-style); quotes for a phrase. No LLM. |
| `read(path)` | file content and parsed provenance |
| `list(path_prefix?)` | subfolders and files, with a provenance summary |
| `sync_status(pipeline?)` | per pipeline: status, last run, counts, conflicts, guard state, age; plus brain age |
| `recent_changes(since?, pipeline?, limit?)` | files created, changed or deleted by successful syncs since a time (ISO date or time; default the last 24 hours), merged per file, with counts. Answers "what changed since yesterday?". |
| `sync(pipeline?)` | syncs one pipeline or all; a guard block returns `blocked` ("approval needed in UI or CLI"). No approve or force option. |

There is no tool that writes or edits brain content. Calls are logged by tool name and time only.

## Web UI

`mdpipeline ui` (default `http://127.0.0.1:8765`, `--port 0` picks a free port, `--no-browser` only prints the URL)
starts a small local server and opens the app. It works offline (no CDN, fonts or external scripts), listens on
`127.0.0.1` only, refuses requests with a foreign `Host` or `Origin`, and requires a per-session token on every
change. Four screens:

1. **Pipelines:** one row per pipeline (folders, target template, its own target with the last commit link for GitHub, last sync, status as text plus shape: ok,
   conflicts, blocked, error, paused, syncing), **Sync now**, **Edit**, **Pause/Resume**, **Sync all**, a
   "Connect your agent (MCP)" panel with a Copy button for Claude Code and Codex. Problem statuses link to Activity.
2. **Pipeline view mode** (click a pipeline's name; own URL `#/pipeline/NAME`, so a reload or a link reopens it): read the whole setup
   without any risk of changing it: sources with their live check ("Folder found - N .md files", "Folder not found"), where the files land
   (for GitHub: repo, branch, sign-in method, clone path), target path, file-name policy with an example name, include/exclude, on-delete,
   guard and paused or active. Above the setup: the pipeline's **status** (same text and shape as the home, last sync and run counts, files owned,
   for GitHub the last commit link and the clone status), its **open Needs-you items** (each with a link to handle it in Activity; guard approval stays
   there) and, below, its **last 10 runs** (rows expand to file-level changes; "See all in Activity" opens Activity filtered to this pipeline). The page
   follows syncs run from the CLI, MCP or UI without a reload. Credentials show only the method, "set / not set" and the vault name, never a value. No field is editable;
   opening the view writes nothing. Below the setup is the **next-sync dry run** of the saved configuration (the same plan as the editor's
   preview and `sync --dry-run`): it runs when you open the view and after any sync, shows "planned at hh:mm", and has **Refresh**. A source or target
   that cannot be read (folder moved, token rejected, vault locked, sign in again) is shown as a problem in the Activity wording and is never planned as
   deletes. If the next sync would trip the guard the view says so and points to Activity (Needs you). **Edit** opens the editor (Cancel or Save returns to the view), plus **Sync now**, **Pause/Resume** and
   **Back to home**.
3. **Pipeline editor:** a list of sources, none until you press **+ Add source** and choose **Folder** (path, label, live "Folder found - N .md files" check) or **Jira**; any row can be removed as long as a source remains (a Jira-only pipeline is fine), **Where the files land** (a folder, or a GitHub repository with **Sign in with GitHub** or a personal access token,
   Test connection, and for a saved pipeline **Change target...** which previews the new target and what stays behind), target template with variable chips and a resolved example, include/exclude, on-delete policy, guard %
   (Advanced) and a **live dry-run preview** grouped by folder with filterable counts, reasons for every skip or
   conflict, a guard warning and collision/validation messages next to the offending field. Save, Save & sync.
   Removing a folder keeps its files (unowned) unless "Also delete its N files" is ticked (that goes through the guard).
4. **Activity:** **Needs you** (errors, guard approvals with the exact file lists, conflicts with Open / Copy path /
   Force with inline confirmation) and the run history (time, pipeline, trigger, counts, result, duration;
   rows expand to file-level changes; pause, resume, approve, cancel and force are listed too). The tab badge counts
   open items. The open UI follows syncs run from the CLI or MCP within about 2 seconds (polling a cheap version
   endpoint), without a reload.

The UI and the CLI call the same functions (`service.py`, `removal.py`, `engine.py`), so validation messages and
safety rules are identical. UI actions are logged with trigger `ui`. Removing a whole pipeline is CLI only.

### Looking at one pipeline without editing it: `pipeline show`

```bash
mdpipeline pipeline show docs            # the setup and status, in words
mdpipeline pipeline show docs --json     # the same, for scripts and agents
mdpipeline sync docs --dry-run           # what the next sync would do (nothing is written)
```

`pipeline show NAME` prints what the UI's view mode shows: sources (folders with the live "Folder found - N .md files" check; Jira sites, JQL and
labels), where the files land (folder, or GitHub repo, branch, sign-in method, clone path), target path, file-name policy with an example name,
include/exclude, on-delete, guard, paused or active, then the status, last sync, files owned and (GitHub) the last commit. It writes nothing and
never prints a secret: a credential is shown as its method (app vault, environment variable, OS keychain), `set` / `not set` and its name. An unknown
name exits with `2` and a clear message. For the dry run of one pipeline use `sync NAME --dry-run` (the same plan engine the editor and the view mode use).

`--json` shape (`schema` is `1`; keys are only ever added): `schema`, `name`, `paused`, `sources[]` (`type` folder: `label`, `path`, `check{ok, exists, md_files, message}`;
`type` jira: `label`, `site`, `jql`, `credentials{email, token}` each `{method, name, set}`), `include[]`, `exclude[]`, `target`,
`name_policy{policy, pattern, example}`, `destination{type, path | repo, branch, auth, clone, login, credential{method, name, set}}`,
`on_source_delete`, `guard{threshold_percent, min_files}` and `status{status, needs_you (true when the only problem is a missing source folder: amber "needs you" in the app, nothing deleted), paused, summary, schedule, last_sync, next_sync, last_sync_ts, last_run{ts, trigger, result, counts},
owned_files, pending_approval, destination, last_changes{ts, text, counts{new, changed, deleted}, items, more}}`.

## Jira source

A pipeline can bring **Jira Cloud** issues into the brain next to your folders: one JQL query is one source, **one file per issue**, kept
current by incremental sync, including deletions. The connector is **read-only** (it never changes Jira).

### 1. Create an API token and keep it out of the files
At https://id.atlassian.com/manage-profile/security/api-tokens (**Create API token**) create a token for your Atlassian account. You give your account email
and the token to the app once: the email as a flag, the token through a **hidden prompt** (or one line of stdin with `--jira-stdin`; a token as a
command-line argument is refused). The app checks them against Jira and keeps them in its **vault**; the configuration stores only
`vault:<id>`. In the UI, the Jira row has an Email field and a password field for the token. Older setups that point at an environment
variable (`env:NAME`) or a keychain entry (`keychain:SERVICE`) keep working until you move them into the vault with `mdpipeline vault import`.

### 2. Add the source
```bash
mdpipeline jira test --jira-site https://yourcompany.atlassian.net --jira-jql "project in (MD, MP)" --jira-email you@yourcompany.com
mdpipeline pipeline add work --jira-label jira --jira-site https://yourcompany.atlassian.net --jira-jql "project in (MD, MP)" --jira-email you@yourcompany.com
mdpipeline pipeline edit work --jira-label jira --replace-jira-token
mdpipeline sync work --dry-run
mdpipeline sync work
mdpipeline pipeline edit work --jira-label jira --jira-jql "project in (MD, MP) AND updated >= -90d"
```
`jira test` prints the account's display name and how many issues the query matches; it writes nothing. Saving validates the site, the
credentials and the JQL against Jira (a wrong token, no permission, a site that does not exist and an invalid JQL with Jira's own message each
give a plain error) and saves nothing on failure. Folder and Jira sources can be mixed in one pipeline (`--source PATH` plus the `--jira-*`
flags); labels stay unique. The site must be `https://<name>.atlassian.net`.

**JQL examples:** one project `project = MD`; several `project in (MD, MP)`; open issues only `project = MD AND statusCategory != Done`;
recently touched `project = MD AND updated >= -90d`. Changing the JQL is a planned change: the dry run shows which owned files would be
deleted, and the guard applies.

### A Jira-only pipeline
A pipeline needs **at least one source, of any type**; a folder is not required. A pipeline that reads only Jira:
```bash
mdpipeline pipeline add work --jira-label jira --jira-site https://yourcompany.atlassian.net --jira-jql "project = MD" --jira-email you@yourcompany.com
mdpipeline sync work --dry-run
mdpipeline pipeline edit work --remove-source jira --dry-run
```
In the UI press **+ New pipeline**, **+ Add source**, **Jira**. To switch a folder pipeline to Jira only, add the Jira source and remove the folder in the same
command (`pipeline edit work --remove-source docs --jira-label ...`) or in the same save in the editor. Removing a source (a folder or a Jira source,
by its label) keeps its files in the brain: they become **unowned**, are reported, and no pipeline touches them again. Deleting them is opt-in
(`--delete-files`, or the "Also delete its N files" box), is shown first (`--dry-run`, or the editor's live dry run) and goes through the guard.
Zero sources is refused ("Add at least one source"), and a replacement source that fails its check (site, credentials, JQL) removes nothing.

### Switching a source Off and On
Each source has an **On/Off switch** (the switch on its row in the editor, or `--source-off LABEL` / `--source-on LABEL`). An Off source keeps all its
settings (path, label, site, JQL, credential references) and is **not read at all**: no folder scan, no request to Jira, no credential read. Its files stay
in the brain, stay **owned** by the pipeline and are **kept**: not updated and never deleted because the source is off (the dry run lists them as
`SKIP · source off: kept`, and they still count in the big-change guard's total). At least one source must be On (to stop everything, pause the pipeline).
An Off source is information ("1 of 2 sources off"), not an error and not a Needs-you item.
```bash
mdpipeline pipeline edit work --source-off docs            # run on Jira only for now
mdpipeline pipeline edit work --source-on docs --dry-run   # what the next sync would do with docs back on; nothing is changed
mdpipeline pipeline edit work --source-on docs             # switch it on; the plan of the next sync is printed, nothing is written yet
```
Switching a source back On plans first: everything that changed while it was off shows in the dry run (new, changed and deleted items follow the
on-delete rule, files you edited in the brain become conflicts, the guard applies). A Jira source catches up with a full listing, so issues deleted
while it was off are found. Off is not the same as removed: removing a source (`--remove-source`) makes its files unowned; Off keeps them owned.

### What is synced (and what is not)
- **One file per issue**, named by the pipeline's file-name policy; the date is the issue's **created** date; names stay stable when the summary changes. A
  project move changes the issue key but keeps the same file (one rename). With the `original` policy the file name follows the summary.
- Front-matter `jira:` (key, type, status, status category, priority, labels, components, assignee, reporter, parent, created, updated,
  resolved, due, fix versions, sprints), then the title, description, sub-tasks, links, **attachments listed (name, size, link; not downloaded)**
  and **comments**, oldest first. People appear by **display name** only: no e-mail addresses or account ids are written.
- Rich text is converted without silent loss: an element the converter does not know keeps its text under a visible marker and is counted as a
  **conversion warning**. A conversion error fails that one issue only.
- Not synced in this version: custom fields (except sprints), change **history**, worklogs, JSM queues, Confluence, Jira **Server**/Data Center.
- A change in Jira that only touches front-matter (for example the assignee) is a **metadata-only** update: it is reported separately and the
  guard does not count it.

### Network rule and token safety
Network calls come only from source connectors and the GitHub target, only over **https**, only to the configured site (`yourcompany.atlassian.net`).
Only read requests are sent. The token and email never appear in `config.yaml`, the state, the log, backups, the written files, CLI output, the UI or
the MCP responses (a test searches everything). Logs hold counts, keys and error categories, never issue content.

### Failures never delete
The connector never deletes on a failure. If the site is unreachable, the credentials fail or lack permission, any page of the result fails, the answer is malformed or Jira times out, the
sync of that source is marked failed in `status` and **Needs you**, **nothing is deleted**, and the other sources keep syncing. A JQL that suddenly
**returns 0** issues for a source that owns files always asks the guard. The limit: when Jira reports success, an issue you lost permission for looks
exactly like a deleted one and follows the pipeline's on-delete policy.

### Troubleshooting
| Message | Meaning and next step |
|---|---|
| token rejected (auth) | Jira refused the email or API token. Create a new API token at https://id.atlassian.com/manage-profile/security/api-tokens and enter it with `pipeline edit NAME --jira-label L --replace-jira-token` (or in the editor). |
| no permission | the account cannot read those issues. Check its project permission in Jira. |
| site not found | the site URL is wrong. Use `https://yourcompany.atlassian.net`. |
| invalid JQL | Jira refused the query; the message is Jira's own. Fix it with `jira test`. |
| rate limit | Jira's limit was reached; the client waits as Jira asks, then fails the source without deleting. Sync again later. |
| unreachable (network) | check the network; the next sync converges. |
| conversion warnings | an element the converter does not know was kept as text with a marker (see `status`). |
| a Jira file shows as conflict | you edited it in the brain: your copy is never overwritten (see the conflict help above). |

## What the activity log contains

`.mdpipeline/log.jsonl` stays on your machine and is the only record of runs. For folder sources it lists the files of each run by path.
For connector sources (Jira) it identifies files by **issue key** (for example `PROJ-12`), the action and counts, and **never by issue title**
or a title-derived file name: a title is issue content, and logs get shared in support requests. A conflict or a forced file is named by
its key in the log; your own screen (`status`, the UI) still looks up the real path in the state. Secrets are never logged.

## GitHub repository as a pipeline's target

Each pipeline chooses where its files land: the brain's own folder (the default), another folder (`--directory`), or a GitHub
repository branch (`--github`). A GitHub target is written straight through the GitHub API (no clone is needed to sync), as **one
commit per sync**. Pipelines, the guard, file names, conflicts and recovery work exactly as for a folder. Pipelines in one brain can
have different targets; they sync independently (a failing target never blocks or changes another one).

### 1. Sign in with GitHub (recommended) or create a token
**Sign in with GitHub** uses a GitHub App you install only on the repositories you choose; there is no token to create or paste. In the UI it is the
default option of the target step; in the terminal:

```bash
mdpipeline pipeline add docs --source ~/projects/docs --github acme/team-brain --auth sign-in
```
The app shows a short code and `https://github.com/login/device`; you open the link, enter the code and approve, and the app says "Signed in as ...".
It then lists the repositories the app is installed on; if yours is not there, it shows the install link
(`https://github.com/apps/<app>/installations/new`: choose **Only select repositories**), waits, and checks again. The sign-in is kept in the vault
and refreshed by the app; commits are made as you. The app **ships with its GitHub App as the default** (`src/mdpipeline/defaults.json`), so there is nothing to set up. To use your own app instead (see `docs/github-app-registration.md`),
enter the Client ID and app slug in the UI (the target step shows two fields and a link to the step-by-step, served by the local UI at `/github-app-registration.md`; it is also shipped inside the package) or run `mdpipeline github-app set --client-id ID --slug SLUG`; no restart is needed. What you save wins over the shipped default; `mdpipeline github-app show` (and the editor) say which one is in use, and `mdpipeline github-app reset` (or "Use the shipped default" in the editor) goes back. If a build ships no default, the option says it is not set up and the token option works. If the sign-in expires or is
revoked, syncs stop without writing or deleting anything and **Needs you** says "Sign in again" (`mdpipeline pipeline sign-in NAME`). **Sign out**
(`pipeline sign-out NAME`) removes it from the vault; to revoke the app's access at GitHub, remove it at github.com/settings/applications.

**Or use a personal access token.** In GitHub: Settings, Developer settings, **Fine-grained personal access tokens**, Generate new token. Set *Repository access* to
**Only select repositories** and pick the one repo. Under *Repository permissions* set **Contents: Read and write** (nothing else
is needed). You give the token to the app once; the app keeps it in its **vault** (see "The app vault"). You never put it in an
environment variable or a file.

### 2. Add a pipeline with a GitHub target
```bash
mdpipeline init ~/brains/team --create
mdpipeline pipeline add docs --source ~/projects/docs --github acme/team-brain --branch main     # asks for the token (hidden)
mdpipeline pipeline test-target docs
mdpipeline sync docs --dry-run
mdpipeline sync docs
```
`pipeline add` asks for the token with a hidden prompt (or reads one line from stdin with `--stdin`; a token given as an argument is
refused). It checks that the repository and branch exist, that the token can read **and write** contents, and that the branch is
not protected against direct pushes; if a check fails **nothing is saved** and the token is not stored. The configuration keeps only
`vault:<id>`. `pipeline test-target` is read-only: it prints the user, the repository, the branch head and whether a write would be
allowed. `sync --dry-run` reads the repository and shows the exact plan; it makes no commit. To give a second pipeline the same token
use `--use-secret ID` (see `mdpipeline vault list`).

### Branch protection
The pipeline always commits directly to the configured branch (no pull requests). A branch protected against direct pushes is
refused when you add the pipeline, with the advice to use a dedicated branch: create a branch such as `md-sync` in the repository, then
`mdpipeline pipeline add ... --branch md-sync`. Commits that other people or agents make to the branch are never lost: if the branch moves
during a sync, the sync plans again on the new head (at most 3 times, then it stops and writes nothing). The pipeline never
force-pushes.

### No machine paths in a shared repository
A GitHub target never commits this machine's absolute folder path: `provenance.source_root` shows `local:<label>` (the same on every machine). Files
written by an older version still carry the path until you run the one-time `mdpipeline pipeline scrub-paths NAME` (it shows the plan first; only the
provenance of those files changes, never their content; `--apply` makes one commit; edited files are never overwritten).

### What gets committed
One commit per sync, all or nothing: the created, updated, renamed and deleted Markdown files **and** the pipeline state
(`.mdpipeline/state.json`: paths, hashes, names and dates only, no document content and **never any secret**). A sync with no changes
makes no commit. The commit message is `mdpipeline sync [PIPELINE]: N created, N updated, N renamed, N deleted` (plus a `change-set:`
line when a guard approval was used). The pipeline never touches `wiki/` or any other path. The activity log, the lock, temp files
and a pending guard approval stay on your machine.

### Change a pipeline's target
```bash
mdpipeline pipeline change-target docs --github acme/team-brain --branch main
mdpipeline pipeline change-target docs --directory ~/other-brain --apply
```
It first shows the plan for the new target: **0 changes** when the files are already there (for example you pushed the brain folder with git), otherwise
what is missing or differs, and **what stays behind**. With `--apply` (or a yes) the pipeline's ownership (names, first-seen dates) is carried to the new
target as a state-only change. **The old target is never changed or deleted**, not even its state file. In the UI: edit the pipeline, **Change target...**.

### Importing old env: / keychain: references
Before the vault, a GitHub target (or a Jira source) could name an environment variable or keychain entry. Those keep working until you run
`mdpipeline vault import` (or press the button in the UI under **Needs you**); it moves them into the vault once and tells you that you may delete
the variable, the `.env` entry or the keychain entry. After that the app never reads them again. `--dry-run` only lists them.

### Keep a local clone for your agent
The MCP `search`, `read` and `list` work on a local clone, so your agent sees new files at once:
```bash
git clone https://github.com/acme/team-brain.git ~/clones/team-brain
mdpipeline pipeline edit docs --clone ~/clones/team-brain          # --remove-clone to stop
```
After each sync the clone is **fast-forwarded** with `git fetch` and `git merge --ff-only`, only if it is on the configured branch, has no local
changes and has not diverged. Your uncommitted work and local commits are never touched; otherwise the reason is shown in `status` and under
**Needs you**. The clone's `origin` must be the `https://github.com/OWNER/REPO` URL (git uses your own git credentials; the pipeline's token is never passed to git).
`sync` and `sync_status` work with or without a clone.

### The app vault
Tokens are kept in the app's vault: one file `vault.json` in the app's data directory (`~/.local/share/mdpipeline` on Linux,
`~/Library/Application Support/mdpipeline` on macOS; never in your output folder or a repository). With an OS keychain the file is encrypted and its key is in the keychain; without one
the file itself is the protection, as `gh`, the AWS CLI and docker do it: mode 0600 in a 0700 folder, created that way from the start, and refused (with the command to fix it) when
other users can read it or it is not yours. There is no passphrase, so scheduled syncs recover after a reboot with nothing to enter. Vaults from v0.1.1 to v0.1.3 that were protected by a passphrase open once with it and are then moved. Secrets can be added, replaced and removed (`mdpipeline vault add|replace|remove|list`),
never shown or exported (a secret cannot be removed while a pipeline uses it). The keychain is macOS Keychain or the Linux Secret Service. A vault that cannot be opened (an old passphrase vault not yet unlocked, a lost keychain entry) or a secrets file open to other users stops only the syncs that need a secret ("Needs you"); nothing is written or deleted.

### Network rule
Network calls come only from source connectors and the GitHub target, only over **https**, and only to the configured GitHub API host
(`api.github.com`). A local-only brain with folder sources makes zero network calls (a test proves it). The token is sent only in the
`Authorization` header and never appears in the repository, the state, the log, a backup, the CLI output, the UI or the MCP responses.

### Troubleshooting
Every failure leaves the branch unchanged and shows in `mdpipeline sync`, `status` and **Needs you** with its next step:

| Message | Meaning and next step |
|---|---|
| token rejected (auth) | GitHub refused the token (expired, revoked, wrong repo). Create a new fine-grained token with Contents read and write for this one repository and run `mdpipeline pipeline edit NAME --replace-token`. |
| vault locked / saved-secrets file open to other users | the app could not open its vault (keychain entry missing, an old v0.1.1-v0.1.3 passphrase vault not yet unlocked) or refused its file because other users can read it (`mdpipeline vault status` prints the `chmod 600` fix). Nothing was written or deleted; syncs that need no secret keep running. `mdpipeline vault status` says how it is protected; enter the old passphrase once in the UI (Needs you) or when the CLI asks. |
| Sign in again | your GitHub sign-in expired, was revoked, or the app was uninstalled. Nothing was written or deleted. `mdpipeline pipeline sign-in NAME`, or Change... in the editor's target step. |
| app not installed on the repository | a sign-in token only sees repositories the GitHub App is installed on (a typo in the name looks the same). Install it on that repository from the link shown, then test again. |
| different credential on the same repository and branch | two pipelines that write to one repository branch share one target, so they must use the same credential (the identity that writes is the one you chose). Saving a second pipeline with another credential is refused; an older config that already has this shows a Needs-you item on both pipelines and neither syncs (nothing is written or deleted). Use the same credential (`--use-secret ID`), or move one pipeline to another branch. `pipeline show` names the other pipelines. |
| branch protected | direct pushes are blocked. Use a dedicated branch such as `md-sync`, or let this token push to the branch. |
| head moved too often | other commits kept arriving during the sync (3 attempts). Nothing was written; sync again in a moment. |
| rate limited | GitHub's request limit was reached. Nothing was written; wait and sync again. |
| GitHub could not be reached (network) | check the network; the next sync converges. |
| clone not fast-forwardable | the clone has local changes, local commits, another branch or an unexpected origin. Fix it in the clone; it is never changed by the pipeline. |
| state unreadable | the state in the repository is corrupt. `mdpipeline state restore --pipeline NAME` shows what an earlier state (from the file's git history) would do; `--apply` commits it. `state rebuild` rebuilds it from the provenance in the repository. |

## Safety guarantees (what the tests prove)

- Source folders are byte- and mtime-identical after every kind of sync; they are only ever opened read-only.
- The only code that writes into the brain is `writer.py`: relative path inside the output folder (never `.mdpipeline/`, `wiki/` or a hidden top-level folder), no `..`, `.md` only,
  no symlink anywhere in the path, atomic write via a temp file under `.mdpipeline/tmp/`.
  Hostile templates, labels, names and symlinked folders are tested at save time and at write time.
- A second sync with no changes performs zero writes. A sync killed mid-run (SIGKILL tested) leaves no partial file
  and the next run converges without false conflicts.
- Hand-written files and brain-edited files are never overwritten or deleted (force overwrites edited owned files
  only, and never deletes).
- A missing/unreadable source folder, or a folder removed from the config, never causes deletions.
- Overlapping syncs (CLI, MCP, UI) are serialized by a brain-wide lock: the second one writes nothing and returns `busy`.
- Jira source (`tests/test_data_loss_jira.py`, against a fake Jira, no real network): an outage, an auth or permission failure, a failing page,
  a malformed answer or a rate-limit storm never deletes anything; an empty answer always asks the guard; a summary change never renames; a project
  move keeps the same file; brain edits and hand-written files are never overwritten or claimed; the token and email appear nowhere.
- Targets and vault (`tests/test_release_gate_p31.py`, fake GitHub, fake keychain, no real network): migration of a brain-level config (backup, 0-change plan), two
  targets (one failing never touches the other), a shared target, change target (old target byte-identical), a locked vault, token expiry mid-sync / refused
  refresh / revoked sign-in (branch unchanged), a secret search over every output and file for 0 matches, and a token given as an argument refused.
- View mode (`tests/test_release_gate_view_mode.py`, fake GitHub and Jira, no real network): opening a pipeline in view mode, refreshing its dry run and
  `pipeline show` leave the configuration, state, log, target files, sources and the repository history byte-identical (0 commits, GET-only calls) for
  folder and Jira sources on directory and GitHub targets; the only thing a read may touch is the vault's "last used" date of a secret. A secret search over the
  page, every API response and `pipeline show` (text and JSON) finds 0 matches for the GitHub token, the sign-in tokens and the Jira token; a moved folder,
  a rejected token, a locked vault and an expired sign-in are shown as problems and the plan shows no deletes; paused, never synced, error and guard states are covered.
- GitHub target (`tests/test_data_loss_github.py`, against a fake GitHub, no real network): one atomic commit per sync, no commit when
  nothing changed, never a force push, commits by others never lost, only the pipelines' Markdown files (never `.mdpipeline/` or `wiki/`) and the state path are ever changed, the token
  never appears anywhere, a failure at any step leaves the branch unchanged.
