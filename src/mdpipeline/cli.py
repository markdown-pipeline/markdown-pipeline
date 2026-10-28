"""The ``mdpipeline`` command line.

Exit codes (stable, scriptable):
  0 success (also: pipeline paused, nothing to do)
  1 runtime error, or a sync whose result is ``error``
  2 validation or usage error (including "no brain registered")
  3 sync blocked by the big-change guard
  4 sync finished with conflicts
  5 busy: another sync is running (retry later)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from pathlib import Path

from .brain import Brain, BrainError, ConfigReadError, find_brain, init_brain
from .destinations import AmbiguousTarget, describe_destination
from .config import NEW_PIPELINE_TARGET, Pipeline, Source, load_config, save_config
from .locking import BusyError
from .naming import DEFAULT_NAME_POLICY, POLICY_NAMES
from .removal import ConfirmationRequired, remove_pipeline, remove_source
from .service import NotFound, ValidationFailed, create_pipeline, set_paused, update_pipeline
from .planner import NEXT_STEP
from .rebuild import MISMATCH, RebuildRefused, apply_rebuild, rebuild_preview
from .recovery import RestoreError, apply_restore, preview_restore
from .state import StateError
from .engine import SyncResult, cancel_pending, sync_all, sync_pipeline
from .log import ActivityLog
from .state import State
from .status import brain_status, parse_ts
from .target import TargetError
from .targetsetup import create_pipeline_with_token
from .vault import VaultError

EXIT_OK, EXIT_RUNTIME, EXIT_USAGE, EXIT_BLOCKED, EXIT_CONFLICTS, EXIT_BUSY = 0, 1, 2, 3, 4, 5


class UsageError(Exception):
    pass


SECRET_LOOKING = re.compile(r"(gh[pousr]_[A-Za-z0-9_]{6,}|github_pat_[A-Za-z0-9_]{6,})")


class SafeParser(argparse.ArgumentParser):
    """An argument parser whose error messages never echo something that looks like a token (a mistyped command line)."""

    def error(self, message: str):
        super().error(SECRET_LOOKING.sub("<redacted>", message))


def interactive() -> bool:
    return sys.stdin.isatty()


def stderr_visible() -> bool:
    """MD-156: questions are written to stderr, so a question is only asked when a person can see it (stderr is a terminal too)."""
    return sys.stderr.isatty()


def read_secret(label: str, from_stdin: bool, flag: str = "--stdin") -> str:
    """A secret from a hidden prompt, or one line of stdin. Never from an argument (shell history, process list)."""
    if from_stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    elif interactive():
        value = getpass.getpass(f"{label}: ")
    else:
        raise UsageError(f"There is no terminal to ask on: pipe the secret in with {flag}.")
    if not value:
        raise UsageError("The secret is empty. Nothing was saved.")
    return value


def with_vault(action):
    """Run ``action``; when an old passphrase vault (v0.1.1-v0.1.3) needs its passphrase and a person is at the keyboard, ask once (hidden),
    move the secrets to the user-only file or keychain (MD-163) and retry. A new vault never asks for a passphrase."""
    from . import vault as vault_mod

    try:
        return action()
    except vault_mod.VaultLocked as exc:
        if not (exc.needs_passphrase and interactive()):
            raise
        vault_mod.unlock(getpass.getpass("Vault passphrase (from an earlier version; asked this once): "))
        return action()


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_source(text: str) -> Source:
    """``PATH`` or ``LABEL=PATH`` (a label never contains '/' or '~')."""
    if "=" in text:
        label, _, path = text.partition("=")
        if label and "/" not in label and not label.startswith("~"):
            return Source(path, label)
    return Source(text)


def jira_flags(sp, adding: bool) -> None:
    g = sp.add_argument_group("Jira source", "Bring Jira Cloud issues into the brain: one JQL query is one source, each issue one file. Read-only. "
                              "Give your Atlassian email; the API token is asked for (hidden prompt) or read with --jira-stdin. Both are checked against Jira "
                              "and then kept in the app vault. Older setups use references (env:NAME, keychain:SERVICE) instead.")
    g.add_argument("--jira-label", metavar="LABEL", help="label of the Jira source (default: jira)" if adding else "label of the Jira source to add or change")
    g.add_argument("--jira-site", metavar="URL", help="https://yourcompany.atlassian.net")
    g.add_argument("--jira-jql", metavar="JQL", help="which issues, e.g. 'project in (MD, MP)'")
    g.add_argument("--jira-email", metavar="EMAIL", help="your Atlassian account email (kept in the vault), or a reference such as env:JIRA_EMAIL")
    g.add_argument("--jira-token", metavar="REF", help="a REFERENCE to the API token (vault:ID, env:NAME, keychain:SERVICE); the token itself is never an argument")
    g.add_argument("--jira-stdin", action="store_true", help="read the Jira API token from one line of standard input instead of a hidden prompt")
    if not adding:
        g.add_argument("--replace-jira-token", action="store_true", help="enter a new API token for the Jira source named by --jira-label (checked first)")


def target_flags(sp, adding: bool) -> None:
    g = sp.add_argument_group("Target (where the pipeline's files land)",
                              "By default the brain's own folder. Or another folder (--directory), or a GitHub repository (--github). "
                              "A GitHub token is never given as an argument: you are asked for it (hidden), or pass it with --stdin; it is kept in the app vault.")
    g.add_argument("--directory", metavar="PATH", help="land the files in this folder (its own state is kept there)")
    g.add_argument("--github", metavar="OWNER/REPO", help="land the files in this GitHub repository (one commit per sync)")
    g.add_argument("--branch", metavar="BRANCH", help="the branch to commit to (default: the repository's default branch)")
    g.add_argument("--auth", choices=["token", "sign-in"], help="how to authenticate to GitHub: a personal access token (default) or Sign in with GitHub")
    g.add_argument("--clone", metavar="DIR", help="optional local clone of the repository, fast-forwarded after each sync (for MCP search and read)")
    g.add_argument("--stdin", action="store_true", help="read the token from one line of standard input instead of a hidden prompt")
    g.add_argument("--use-secret", metavar="ID", help="use a token already in the vault (see `mdpipeline vault list`)")
    for refused in ("--token", "--value", "--secret", "--password"):
        g.add_argument(refused, help=argparse.SUPPRESS)
    if not adding:
        g.add_argument("--replace-token", action="store_true", help="enter a new token for this pipeline's GitHub target (checked first; the old one stays if it fails)")
        g.add_argument("--remove-token", action="store_true", help="detach the token from this pipeline (and delete it from the vault when no other pipeline uses it)")
        g.add_argument("--remove-clone", action="store_true", help="stop using a local clone")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(
        prog="mdpipeline",
        description="Copy Markdown from your project folders into an output folder (your brain), with provenance. "
                    "Run `mdpipeline init` in the brain directory, add a pipeline, dry-run, then sync.",
        epilog="Exit codes: 0 ok, 1 runtime error, 2 validation/usage error, 3 blocked by the guard, 4 conflicts, 5 busy.")
    parser.add_argument("--brain", metavar="DIR", help="brain directory (default: the registered brain at or above the current directory)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("init", help="choose the folder where your Markdown files go (the \"brain\")",
                       description="Register DIR (default: current directory) as the brain: it holds the configuration, the activity log and the lock. "
                                   "DIR can be any folder (add --create to make it). Safe to repeat. Where each pipeline's files land "
                                   "(a folder or a GitHub repository) is chosen per pipeline: see `mdpipeline pipeline add --help`.")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--create", action="store_true", help="create DIR if it does not exist (for a brain whose pipelines all go elsewhere)")
    for moved in ("--github", "--branch", "--credential", "--clone"):
        p.add_argument(moved, help=argparse.SUPPRESS)

    jt = sub.add_parser("jira", help="check a Jira source", description="Commands for Jira sources.")
    jtsub = jt.add_subparsers(dest="jira_command", required=True, metavar="ACTION")
    jtest = jtsub.add_parser("test", help="test a Jira connection: the account's display name and how many issues the JQL matches (read-only, writes nothing)",
                             description="Either name a saved source (--pipeline P --source LABEL) or give --jira-site/--jira-jql/--jira-email/--jira-token to test before saving.")
    jtest.add_argument("--pipeline", metavar="NAME")
    jtest.add_argument("--source", metavar="LABEL")
    jira_flags(jtest, False)

    tg = sub.add_parser("target", help="moved: the target is now part of each pipeline", description="The target is chosen per pipeline now.")
    tg.add_argument("rest", nargs="*", help=argparse.SUPPRESS)

    ga = sub.add_parser("github-app", help="the GitHub App behind \"Sign in with GitHub\": show or set its client ID and slug",
                        description="Sign in with GitHub needs the GitHub App registered once (docs/github-app-registration.md). Only its public client ID "
                                    "and slug are kept (in the app data directory); there is no client secret.")
    gasub = ga.add_subparsers(dest="github_app_command", required=True, metavar="ACTION")
    gaset = gasub.add_parser("set", help="record the client ID and slug of the registered GitHub App")
    gaset.add_argument("--client-id", required=True)
    gaset.add_argument("--slug", required=True, help="the last part of https://github.com/apps/<slug>")
    gasub.add_parser("show", help="show the client ID and slug the app uses, and whether they are the shipped default or your own")
    gasub.add_parser("reset", help="forget your own GitHub App and use the one the app ships with again")

    vt = sub.add_parser("vault", help="the app vault: where the app keeps your tokens (add, replace, remove; values are never shown)",
                        description="Secrets (a GitHub token, a Jira token) are kept in the app's own data directory; encrypted with a key in your OS "
                                    "keychain, or (when there is no keychain) in a file only your user can read. Values can be added, replaced and removed, never shown or exported.")
    vtsub = vt.add_subparsers(dest="vault_command", required=True, metavar="ACTION")
    vadd = vtsub.add_parser("add", help="store a new secret (hidden prompt, or --stdin)")
    vadd.add_argument("--kind", required=True, choices=["github-token", "jira-token", "jira-email"])
    vadd.add_argument("--name", required=True, help="a label for you, e.g. 'work GitHub token'")
    vrep = vtsub.add_parser("replace", help="give an existing secret a new value (hidden prompt, or --stdin)")
    vrep.add_argument("id")
    for sp in (vadd, vrep):
        sp.add_argument("--stdin", action="store_true", help="read the value from one line of standard input instead of a prompt")
        for refused in ("--value", "--token", "--secret", "--password"):
            sp.add_argument(refused, help=argparse.SUPPRESS)
    vrm = vtsub.add_parser("remove", help="delete a secret (refused while a pipeline uses it)")
    vrm.add_argument("id")
    vls = vtsub.add_parser("list", help="the secrets: id, name, kind, dates and the pipelines that use them (never values)")
    vls.add_argument("--json", action="store_true")
    vtsub.add_parser("status", help="where the vault is and how it is protected")
    vim = vtsub.add_parser("import", help="move the old env:/keychain: token references of GitHub targets into the vault (once)",
                           description="Reads each old reference one last time, keeps the token in the vault and rewrites the pipelines to use it. "
                                       "Until you run this the old references keep working.")
    vim.add_argument("--dry-run", action="store_true", help="only list what would be imported")

    pipe = sub.add_parser("pipeline", help="manage pipelines (add, list, edit, remove, pause, resume)")
    psub = pipe.add_subparsers(dest="pipeline_command", required=True, metavar="ACTION")

    def rules(sp, adding: bool) -> None:
        sp.add_argument("--target", metavar="TEMPLATE", help="where the files go inside the output folder (optional; default: raw/{relpath}; an empty value = the root of the output folder). Variables: {pipeline} {source} {relpath} {dir} {name} {ext} {mtime:YYYY-MM-DD}. Example: raw/{pipeline}/{source}/{relpath}")
        sp.add_argument("--include", action="append", metavar="GLOB", help="include glob (repeatable; replaces the default **/*.md)")
        sp.add_argument("--exclude", action="append", metavar="GLOB", help="exclude glob (repeatable; replaces the defaults: .git, node_modules, .obsidian and all dot-folders)")
        sp.add_argument("--on-delete", choices=["delete", "keep"], help="what to do when a source file is deleted (default: delete)")
        sp.add_argument("--guard-percent", type=float, help="big-change guard threshold in percent (default 40)")
        sp.add_argument("--name-policy", metavar="POLICY", help="file-name policy: " + ", ".join(POLICY_NAMES) + ("; default for new pipelines: " + DEFAULT_NAME_POLICY if adding else ""))
        sp.add_argument("--name-pattern", metavar="PATTERN", help="custom file-name pattern (implies --name-policy custom), e.g. {date}_{slug}. Variables: {date} {slug} {name} {source} {pipeline}")
        sp.add_argument("--guard-min-files", type=int, help="the guard only triggers for at least this many files (default 5)")

    add = psub.add_parser("add", help="create a pipeline from source folders and/or a Jira source", description="Create a pipeline. --source is PATH or LABEL=PATH and can be repeated; the label defaults to the folder name. Add a Jira source with the --jira-* flags (alone or together with folders).")
    add.add_argument("name")
    add.add_argument("--source", action="append", metavar="[LABEL=]PATH")
    jira_flags(add, True)
    target_flags(add, True)
    rules(add, True)

    rm = psub.add_parser("remove", help="remove a pipeline (its files STAY in the brain, unowned, unless --purge)",
                         description="Remove a pipeline. By default every file it created stays in the output folder and becomes unowned: "
                                     "no pipeline will touch it again. --purge deletes the files it owns (except any you edited) after confirmation.")
    rm.add_argument("name")
    rm.add_argument("--purge", action="store_true", help="also delete the files the pipeline owns (asks you to confirm the count)")
    rm.add_argument("--yes", action="store_true", help="skip the confirmation question for --purge")

    for verb, text in (("pause", "pause a pipeline: every trigger skips it until you resume"), ("resume", "resume a paused pipeline")):
        pp = psub.add_parser(verb, help=text, description=text.capitalize() + ". The change is logged.")
        pp.add_argument("name")

    for verb, text in (("sign-in", "sign in with GitHub again for a pipeline (replaces its stored sign-in)"),
                       ("sign-out", "sign out: remove the pipeline's GitHub sign-in from the vault (revoke the app yourself on github.com)")):
        sp = psub.add_parser(verb, help=text, description=text.capitalize() + ".")
        sp.add_argument("name")

    ct = psub.add_parser("change-target", help="move a pipeline to another target (folder or GitHub repository): the plan first, the old target untouched",
                         description="Shows what the new target needs (0 changes when the files are already there), then, with --apply or a yes, carries the "
                                     "pipeline's ownership (names, dates) to the new target. The old target is never changed or deleted. "
                                     "Nothing is uploaded by the move itself: the next sync does that through the plan.")
    ct.add_argument("name")
    target_flags(ct, True)
    ct.add_argument("--brain-folder", action="store_true", help="move back to the brain's own folder")
    ct.add_argument("--apply", action="store_true", help="apply the change after showing the plan (without it you are asked, or nothing is changed)")

    sc = psub.add_parser("scrub-paths", help="one-time: replace machine paths in the provenance of a GitHub pipeline's existing files by a stable label",
                         description="A repository is shared, so new files never carry this machine's absolute source path (source_root shows local:<label>). "
                                     "Files written before that still do; this shows the plan to rewrite only their provenance (the content is untouched) "
                                     "and, with --apply, commits it once. Files someone edited are never overwritten.")
    sc.add_argument("name")
    sc.add_argument("--apply", action="store_true", help="make the commit (without it nothing is changed)")

    tt = psub.add_parser("test-target", help="test a pipeline's target: for GitHub the user, repository, branch head and whether a write would be allowed (GET only)",
                         description="Checks where the pipeline's files land without writing anything.")
    tt.add_argument("name")

    sh = psub.add_parser("show", help="show one pipeline's configuration and status (read-only; credentials only as method, set/not set and name)",
                         description="Prints everything the UI's view mode shows about a pipeline, without opening the editor and without writing anything. "
                                     "For the dry run of its next sync use: mdpipeline sync NAME --dry-run")
    sh.add_argument("name")
    sh.add_argument("--json", action="store_true", help="a stable, documented JSON shape (README, 'pipeline show'); never a secret value")

    lst = psub.add_parser("list", help="list pipelines with folders, template, policy, status and last sync")
    lst.add_argument("--json", action="store_true")

    edit = psub.add_parser("edit", help="change a pipeline's folders or rules", description="Edit a pipeline. The same validation as `add` runs; on error nothing is saved.")
    edit.add_argument("name")
    edit.add_argument("--add-source", action="append", metavar="[LABEL=]PATH", help="add a source folder")
    jira_flags(edit, False)
    edit.add_argument("--rename-label", nargs=2, metavar=("OLD", "NEW"), help="rename a folder label; the next dry-run shows the files that move")
    edit.add_argument("--remove-source", metavar="LABEL", help="remove a folder; its files stay in the brain, unowned (unless --delete-files)")
    edit.add_argument("--source-off", action="append", metavar="LABEL", help="switch a source Off: it keeps its setup, is not read, and its files are kept and stay owned (never deleted)")
    edit.add_argument("--source-on", action="append", metavar="LABEL", help="switch a source back On; the plan of the next sync (what changed while it was off) is shown")
    edit.add_argument("--dry-run", action="store_true", help="with --remove-source or --source-on: show what it would do (files kept or deleted, the plan, the guard) and change nothing")
    edit.add_argument("--delete-files", action="store_true", help="with --remove-source: also delete that folder's files (goes through the big-change guard)")
    edit.add_argument("--approve", action="store_true", help="with --delete-files: approve a guard-blocked deletion shown by the previous attempt")
    target_flags(edit, False)
    rules(edit, False)

    sp = sub.add_parser("sync", help="sync one pipeline or all of them",
                        description="Copy new and changed Markdown into the output folder. Use --dry-run to see the exact change set first.")
    sp.add_argument("pipeline", nargs="?", help="pipeline name (or use --all)")
    sp.add_argument("--all", action="store_true", help="sync every pipeline that is not paused")
    sp.add_argument("--dry-run", action="store_true", help="print the plan (create/update/delete/keep/skip/conflict, grouped by source) and write nothing")
    sp.add_argument("--approve", action="store_true", help="approve a sync that the big-change guard blocked, for exactly the change set it showed")
    sp.add_argument("--force", action="store_true", help="overwrite files edited in the brain (conflict b); never overwrites files the pipeline does not own")
    sp.add_argument("--json", action="store_true", help="print the result as JSON")

    ui = sub.add_parser("ui", help="open the local web UI (127.0.0.1 only)",
                        description="Start the local web UI for this brain and open it in your browser. Stop it with Ctrl+C.")
    ui.add_argument("--port", type=int, default=8765, help="port (default 8765; 0 picks a free one)")
    ui.add_argument("--no-browser", action="store_true", help="only print the URL")

    st = sub.add_parser("start", help="open the app (the first command after installing)",
                        description="Open the local web UI. With no brain registered here, it makes the default one in ~/markdown-pipeline first. "
                                    "Typing just `mdpipeline` does the same. Stop it with Ctrl+C.")
    st.add_argument("--port", type=int, default=8765, help="port (default 8765; 0 picks a free one)")
    st.add_argument("--no-browser", action="store_true", help="only print the URL")

    up = sub.add_parser("update", help="update the app to the newest version (one command)",
                        description="Runs your package manager's upgrade for this install. Your configuration, vault and synced files are not touched; "
                                    "the first plan after an update is what the old version would have planned.")
    up.add_argument("--dry-run", action="store_true", help="only print the command")
    sub.add_parser("uninstall", help="stop background sync and show how to remove the app (your files are kept)",
                   description="Turns background sync off, stops the background process and removes its login item, then prints the one command "
                               "that removes the program. Your synced Markdown files, your brains and the app's data folder are not deleted.")

    note = sub.add_parser("install-note")                    # the install script's one-word marker (MD-117); not listed in --help
    note.add_argument("method")
    sub._choices_actions = [a for a in sub._choices_actions if a.dest != "install-note"]

    cx = sub.add_parser("connect", help="print the MCP config snippet for Claude Code and Codex",
                        description="Print ready-to-paste MCP configuration for your agent, with this brain's path filled in.")
    cx.add_argument("--agent", choices=["claude", "codex"], help="only this agent (default: both)")

    cn = sub.add_parser("cancel", help="cancel the change set a guard-blocked sync showed",
                        description="Cancel a blocked sync. The pipeline stays blocked until the next sync re-checks.")
    cn.add_argument("pipeline")

    st = sub.add_parser("status", help="per pipeline: last run, result, counts, conflicts, guard state and brain age")
    st.add_argument("pipeline", nargs="?")
    st.add_argument("--json", action="store_true")

    stp = sub.add_parser("state", help="recover the ownership state (restore the last good backup)",
                         description="The state (.mdpipeline/state.json) records which brain files the pipelines own. Each save keeps the previous "
                                     "valid copy under .mdpipeline/backups/.")
    stsub = stp.add_subparsers(dest="state_command", required=True, metavar="ACTION")
    rs = stsub.add_parser("restore", help="show what a restored state would do, then restore it with --apply",
                          description="Without --apply this only prints what the next sync of every pipeline would do with the restored "
                                      "state, and writes nothing. With --apply the backup becomes the state; the replaced file is kept in "
                                      ".mdpipeline/backups/state-before-restore.json.")
    stp.add_argument("--pipeline", metavar="NAME", help="work on the target of this pipeline (needed when the pipelines use more than one target)")
    rs.add_argument("--generation", type=int, metavar="N", help="which backup (1 = newest, the default)")
    rs.add_argument("--apply", action="store_true", help="restore for real (after you have read the preview)")

    rb = stsub.add_parser("rebuild", help="rebuild the state from the provenance in the output folder (when no backup is usable)",
                          description="Scan the output folder, and re-own every file whose provenance names an existing pipeline and source and whose content exactly "
                                      "matches what the current source would produce. Files that do not match are left unowned and listed. "
                                      "Without --apply nothing is written. Only for a missing or unreadable state.")
    rb.add_argument("--apply", action="store_true", help="write the rebuilt state (after you have read the preview)")
    rb.add_argument("--approve", action="store_true", help="the big-change guard would block the next sync: apply anyway")

    sub.add_parser("mcp", help="start the MCP server (stdio); your agent client launches this on demand",
                   description="Run the local MCP server over stdio. Agent clients (Claude Code, Codex) start it themselves; see `mdpipeline connect`.")

    lg = sub.add_parser("log", help="show recent runs and events from the local activity log")
    lg.add_argument("--pipeline", help="only this pipeline")
    lg.add_argument("--limit", type=int, default=20, help="number of entries (default 20)")
    lg.add_argument("--json", action="store_true")

    us = sub.add_parser("usage", help="anonymous usage data: see what is sent, turn sharing on or off, reset the install ID",
                        description="Anonymous usage data helps us see where setup gets hard. It is off until you choose. Nothing is sent while it is off, "
                                    "and what is sent is listed by `mdpipeline usage log`, exactly as sent.")
    ussub = us.add_subparsers(dest="usage_action", metavar="ACTION")
    ussub.add_parser("status", help="your choice, what is and is never sent, and the install ID (the default)")
    ussub.add_parser("on", help="share anonymous usage (effective immediately)")
    ussub.add_parser("off", help="don't share (effective immediately; anything waiting to be sent is dropped)")
    uslog = ussub.add_parser("log", help="the last events sent from this computer, newest first, exactly as sent")
    uslog.add_argument("--json", action="store_true")
    ussub.add_parser("reset-id", help="replace the random install ID with a new one")
    ussub.add_parser("flush", help="send the queued events now (the app does this by itself in the background)")
    usctr = ussub.add_parser("counter", help="the owner's switch for the identifier-free consent counter (off by default)")
    usctr.add_argument("state", choices=["on", "off"])

    sc = sub.add_parser("schedule", help="how often each pipeline syncs by itself, and the background process that does it",
                        description="Pipelines sync by themselves after their first sync: every hour by default, while the computer is on. "
                                    "A small background process keeps running when the window is closed and starts again when you log in.")
    scsub = sc.add_subparsers(dest="schedule_action", metavar="ACTION")
    scsub.add_parser("show", help="each pipeline's interval, last sync and next sync (the default)")
    scset = scsub.add_parser("set", help="change how often one pipeline syncs by itself")
    scset.add_argument("pipeline")
    scset.add_argument("interval", help="15m, 1h, 6h, daily or off")
    scbg = scsub.add_parser("background", help="turn the background process on or off (it also removes or restores the login item)")
    scbg.add_argument("state", choices=["on", "off"])
    bgp = sub.add_parser("background", help="run the background process in this terminal (the login item runs this for you)",
                         description="Syncs the pipelines that are due, every minute, until stopped. Only one runs at a time.")
    bgp.add_argument("--started-by", choices=["login_item", "user_service", "app_start", "manual"], default="manual", help=argparse.SUPPRESS)
    return parser


def missing_pipeline_message(brain: Brain, name: str) -> str:
    """MD-139: a missing pipeline is always a full sentence naming the pipeline and the brain it was looked for in."""
    try:
        names = [p.name for p in load_config(brain).pipelines]
    except Exception:                                  # an unreadable config is reported by its own error elsewhere
        names = []
    there = ("Pipelines in that output folder: " + ", ".join(names) + ".") if names else "That output folder has no pipelines."
    return (f"No pipeline named '{name}' in the output folder at {brain.root}. {there} "
            "Use --brain DIR, or run the command from that folder.")


def resolve_brain(args) -> Brain:
    try:
        brain = find_brain(args.brain or os.getcwd())
    except BrainError as exc:
        raise UsageError(str(exc)) from exc
    args.resolved_brain = brain                        # MD-139: lets the top-level handler name the brain in a NotFound message
    return brain


def report_validation(result, stream_errors: bool = True) -> None:
    for issue in result.warnings:
        err(f"Warning: {issue.message}")
    if stream_errors:
        for issue in result.errors:
            err(f"Error: {issue.message}")


MOVED_TARGET = ("The target is now part of each pipeline, not a setting of the brain. Choose it when you add or edit a pipeline: "
                "`mdpipeline pipeline add NAME --source PATH --github OWNER/REPO` (or --directory PATH), and check it with "
                "`mdpipeline pipeline test-target NAME`.")


def cmd_init(args) -> int:
    if args.github or args.branch or args.credential or args.clone:
        raise UsageError(MOVED_TARGET)
    brain = init_brain(args.dir, create=args.create)
    print(f"Output folder registered: {brain.root}\nConfiguration: {brain.config_path}")
    return EXIT_OK


def cmd_jira(args) -> int:
    from .jira_source import JiraSource
    from .sources import SourceError
    from .credentials import CredentialError

    brain = resolve_brain(args)
    if args.pipeline or args.source:
        pipeline = load_config(brain).get(args.pipeline or "")
        source = next((s for s in (pipeline.sources if pipeline else []) if s.label == args.source and s.type == "jira"), None)
        if source is None:
            raise UsageError("Name a saved Jira source with --pipeline NAME --source LABEL.")
    else:
        source = jira_source_from_args(args)
        if source is None:
            raise UsageError("Give --pipeline/--source for a saved source, or --jira-site, --jira-jql and --jira-email (the token is asked for).")
        ask_jira_token(args, source)
    from .jira_source import check_jira_source

    typed = {k: v for k, v in source.options.items() if k.startswith("_")}
    static_source = Source(source.path, source.label, type="jira", options={**{k: v for k, v in source.options.items() if not k.startswith("_")},
                                                                         **({"email_ref": source.options.get("email_ref") or "vault:v_checking",
                                                                             "token_ref": source.options.get("token_ref") or "vault:v_checking"} if typed else {})})
    from . import usage_hooks

    found, _ = check_jira_source(static_source, False)
    if found:
        usage_hooks.jira_checked(False, "bad-input")
        raise UsageError(" ".join(message for _, message in found))
    try:
        info = JiraSource(source).test_connection()
    except SourceError as exc:
        usage_hooks.jira_checked(False, exc.category)
        err(f"Error: {exc}")
        return EXIT_RUNTIME
    except CredentialError as exc:
        usage_hooks.jira_checked(False, "auth")
        err(f"Error: {exc}")
        return EXIT_RUNTIME
    usage_hooks.jira_checked(True)
    print(f"Site:     {info['site']}\nAccount:  {info['user']}\nMatches:  {info['matches']} issues for: {source.options['jql']}\nNothing was written.")
    return EXIT_OK


def cmd_target(args) -> int:
    raise UsageError(MOVED_TARGET)


def optional_brain(args) -> Brain | None:
    try:
        return find_brain(args.brain or os.getcwd())
    except BrainError:
        return None


def cmd_vault(args) -> int:
    from . import vault as vault_mod
    from .vault import VaultError, secret_users

    action = args.vault_command
    if action in ("add", "replace"):
        if any(getattr(args, f, None) for f in ("value", "token", "secret", "password")):
            raise UsageError("A secret is never accepted as an argument (it would stay in your shell history). "
                             "Leave it out: you are asked for it with a hidden prompt, or use --stdin.")
        value = read_secret("Secret (typing is hidden)", args.stdin)
        if action == "add":
            secret_id = with_vault(lambda: vault_mod.default_vault().add(args.kind, args.name, value))
            print(f"Stored. Use it in a pipeline's target as: vault:{secret_id}")
        else:
            with_vault(lambda: vault_mod.default_vault().replace(args.id, value))
            print(f"Replaced the value of {args.id}.")
        return EXIT_OK
    if action == "import":
        from .secretimport import import_references, pending_imports

        brain = resolve_brain(args)
        if args.dry_run:
            items = pending_imports(brain)
            for i in items:
                print(f"{i.ref}  used by: {', '.join(i.pipelines)}")
            print("Nothing was changed. Run `mdpipeline vault import` to move these into the vault." if items else "Nothing to import.")
            return EXIT_OK
        results = with_vault(lambda: import_references(brain))
        for r in results:
            print(("Imported: " if r.ok else "Not imported: ") + r.message)
        if not results:
            print("Nothing to import: no GitHub target uses an env: or keychain: reference.")
        return EXIT_OK if all(r.ok for r in results) else EXIT_RUNTIME
    if action == "status":
        try:
            info = vault_mod.default_vault().describe()
        except vault_mod.VaultUnsafe as exc:
            print(str(exc))
            return EXIT_RUNTIME
        how = {"keychain": "encrypted, the key is in your OS keychain", "file": "no OS keychain on this computer, so the secrets are in a file only your user can read (0600)",
               "scrypt": "protected by a passphrase from an earlier version: run any vault command to unlock it once and move it to a file only your user can read"}.get(info["protection"] or "", "")
        print(f"Vault file: {info['path']}\n" + (f"Protection: {how}" if info["exists"] else "No vault yet: it is created when you store the first secret."))
        return EXIT_OK
    brain = optional_brain(args)
    if action == "remove":
        try:
            with_vault(lambda: vault_mod.remove_secret(brain, args.id) if brain else vault_mod.default_vault().remove(args.id))
        except VaultError as exc:
            if isinstance(exc, vault_mod.VaultLocked):
                raise
            raise UsageError(str(exc)) from None
        print(f"Removed {args.id}.")
        return EXIT_OK
    rows = [{**vars(i), "used_by": secret_users(brain, i.id) if brain else []} for i in with_vault(lambda: vault_mod.default_vault().list())]
    if args.json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    if not rows:
        print("The vault is empty.")
    for r in rows:
        print(f"{r['id']}  {r['kind']}  {r['name']}  created {r['created']}  last used {r['last_used'] or 'never'}  used by: {', '.join(r['used_by']) or '-'}")
    return EXIT_OK


JIRA_FLAG_NAMES = ("jira_site", "jira_jql", "jira_email", "jira_token")


def jira_source_from_args(args, existing: Source | None = None) -> Source | None:
    """A Jira Source from the --jira-* flags (None when none were given). A new source needs a site, a JQL and an email;
    the API token comes from a reference flag, or is asked for later (hidden prompt / --jira-stdin). An existing source keeps what is not given.

    A typed email is held in the in-memory option ``_email`` (never persisted) until it is checked and kept in the vault."""
    from .jirasetup import is_reference

    given = {k: getattr(args, k, None) for k in JIRA_FLAG_NAMES}
    label = getattr(args, "jira_label", None)
    if not any(given.values()) and not (label and existing) and not getattr(args, "replace_jira_token", False):
        if label:
            raise UsageError("--jira-label needs the other Jira flags: --jira-site, --jira-jql, --jira-email.")
        return None
    base = dict(existing.options) if existing else {}
    for flag, key in (("jira_site", "site"), ("jira_jql", "jql"), ("jira_token", "token_ref")):
        if given[flag] is not None:
            base[key] = given[flag]
    if given["jira_email"] is not None:
        if is_reference(given["jira_email"]):
            base["email_ref"] = given["jira_email"]
        elif "@" in given["jira_email"]:
            base["_email"] = given["jira_email"].strip()
        else:
            raise UsageError("--jira-email takes your Atlassian email address (or a reference such as env:JIRA_EMAIL).")
    missing = [f"--{f.replace('_', '-')}" for f, k in (("jira_site", "site"), ("jira_jql", "jql")) if not base.get(k)]
    if not (base.get("email_ref") or base.get("_email")):
        missing.append("--jira-email")
    if missing and not existing:
        raise UsageError("A Jira source needs " + ", ".join(missing) + ".")
    site = base.get("site", "")
    return Source(site.rstrip("/") if site else "", label or (existing.label if existing else "jira"), type="jira", options=base)


def ask_jira_token(args, source: Source, replacing: bool = False) -> None:
    """A Jira source without a token reference gets its token from a hidden prompt or one line of stdin (never an argument)."""
    if replacing or not (source.options.get("token_ref") or source.options.get("_token")):
        source.options["_token"] = read_secret("Jira API token (typing is hidden)", args.jira_stdin, "--jira-stdin")


def apply_jira_edit(args, pipeline: Pipeline) -> None:
    label = getattr(args, "jira_label", None)
    existing = next((s for s in pipeline.sources if s.label == label and s.type == "jira"), None) if label else None
    source = jira_source_from_args(args, existing)
    if source is None:
        return
    if getattr(args, "replace_jira_token", False):
        if existing is None:
            raise UsageError("--replace-jira-token needs --jira-label naming a saved Jira source.")
        ask_jira_token(args, source, replacing=True)
    elif existing is None:
        ask_jira_token(args, source)
    if existing is not None:
        pipeline.sources[pipeline.sources.index(existing)] = source
    else:
        if label and any(s.label == label for s in pipeline.sources):
            raise UsageError(f"'{label}' is already a source label of pipeline '{pipeline.name}'.")
        pipeline.sources.append(source)


def source_row(s: Source) -> dict:
    return {**s.public_dict(), "type": s.type}            # keys are only ever added (the JSON of `pipeline list` stays compatible)


def describe_source(row: dict) -> str:
    if row.get("type") == "jira":
        return f"{row['label']} (Jira {row['site']}: {row['jql']})" + ("" if row.get("enabled", True) else " [OFF]")
    return f"{row['label']} (folder {row['path']})" + ("" if row.get("enabled", True) else " [OFF]")


def pipeline_row(brain: Brain, p: Pipeline, status: dict) -> dict:
    last = status["last_run"]
    return {"name": p.name, "sources": [source_row(s) for s in p.sources], "target": p.target,
            "destination": describe_destination(brain, p),
            "on_source_delete": p.on_source_delete, "name_policy": p.name_policy,
            "name_pattern": p.name_pattern, "status": status["status"], "paused": p.paused,
            "last_sync": last["ts"] if last else None, "last_result": last["result"] if last else None}


def refuse_secret_arguments(args) -> None:
    if any(getattr(args, f, None) for f in ("token", "value", "secret", "password")):
        raise UsageError("A token is never accepted as an argument (it would stay in your shell history). Leave it out: you are asked for it "
                         "with a hidden prompt, or use --stdin.")


def destination_from_args(args, existing: dict | None = None, *, adding: bool = False) -> tuple[dict | None, str | None]:
    """The destination the target flags name, and the token typed for it (None when there is none).

    For an edit, a different folder or repository is a change of target (``pipeline change-target``), not an edit."""
    refuse_secret_arguments(args)
    directory, github = getattr(args, "directory", None), getattr(args, "github", None)
    if directory and github:
        raise UsageError("Choose either --directory or --github, not both.")
    if not adding and (directory or github or getattr(args, "branch", None)) and existing is not None:
        raise UsageError("A pipeline's repository, branch or folder is changed with `mdpipeline pipeline change-target NAME ...`: "
                         "it shows the plan for the new target first and leaves the old target untouched.")
    if directory:
        from .config import normalise_path

        return {"type": "directory", "path": normalise_path(directory)}, None
    if github:
        auth = getattr(args, "auth", None) or "token"
        dest = {"type": "github", "repo": github, "branch": args.branch or "", "auth": auth}
        if args.clone:
            dest["clone"] = _clone_path(args.clone)
        token = None
        if auth == "token":
            if args.use_secret:
                dest["credential"] = f"vault:{args.use_secret}"
            else:
                token = read_secret("GitHub token (typing is hidden)", args.stdin)
        return dest, token
    return existing, None


def _clone_path(text: str) -> str:
    path = os.path.abspath(os.path.expanduser(text))
    if not os.path.isdir(path):
        raise UsageError(f"{path} is not a folder.")
    return path


def policy_kwargs(args, default: str | None = None) -> dict:
    """``--name-policy`` / ``--name-pattern`` as Pipeline fields (a pattern alone means the custom policy)."""
    policy, pattern = args.name_policy, args.name_pattern
    if pattern is not None and policy is None:
        policy = "custom"
    if policy is None:
        policy = default
    out = {}
    if policy is not None:
        out["name_policy"] = policy
        out["name_pattern"] = pattern or ""
    return out


def cli_sign_in(repo: str) -> "SignInTokens":
    """The device flow in the terminal: show the code and the link, wait for the approval, then make sure the app is installed on the repo."""
    from . import github_signin as gs
    from .github_api import GitHubApi
    from .github_signin import SignInNotConfigured

    try:
        client_id = gs.require_client_id()
    except SignInNotConfigured as exc:
        raise UsageError(str(exc)) from None
    code = gs.request_device_code(client_id)
    print(f"To sign in, open {code.verification_uri} and enter this code:\n\n    {code.user_code}\n\nWaiting for you to approve it on GitHub (Ctrl+C to cancel)...", flush=True)
    tokens = gs.wait_for_tokens(client_id, code)
    api = GitHubApi(tokens.access_token)
    tokens.login = api.user()
    print(f"Signed in as {tokens.login}.")
    for _ in range(10):
        if repo in gs.installed_repos(api):
            return tokens
        message = (f"The GitHub App is not installed on {repo}. Install it on that repository (choose \"Only select repositories\"): {gs.install_url()}")
        if not interactive():
            raise UsageError(message)
        print(message)
        input("Press Enter once it is installed (Ctrl+C to cancel) ")
    raise UsageError(message)


def cmd_change_target(args, brain: Brain) -> int:
    from . import vault as vault_mod
    from .destinations import destination_type, ephemeral_github_view
    from .retarget import ChangeRefused, apply_change, preview_change
    from .targetconfig import GitHubSpec
    from .targetsetup import check_token, store_token

    if load_config(brain).get(args.name) is None:
        raise UsageError(missing_pipeline_message(brain, args.name))
    if args.brain_folder and (args.directory or args.github):
        raise UsageError("Use either --brain-folder, --directory or --github.")
    token, tokens, view, dest = None, None, None, None
    if not args.brain_folder:
        dest, token = destination_from_args(args, adding=True)
        if dest is None:
            raise UsageError("Name the new target: --directory PATH, --github OWNER/REPO, or --brain-folder.")
        if destination_type(dest) == "github" and dest.get("auth") == "sign-in":
            tokens = cli_sign_in(dest["repo"])
            token = tokens.access_token
        if destination_type(dest) == "github" and token:
            conn = check_token(GitHubSpec.from_dict(dest), token)           # the same checks as `pipeline add`: nothing is stored on failure
            dest["branch"] = dest["branch"] or conn.branch
            view = ephemeral_github_view(brain, dest, token)
    try:
        preview = preview_change(brain, args.name, dest, new_view=view)
    except ChangeRefused as exc:
        raise UsageError(str(exc)) from None
    c = preview.plan.counts()
    print(f"Plan for the new target ({preview.new_label}), pipeline '{args.name}' now writes to {preview.old_label}:")
    print(f"  {preview.owned_files} owned files: {preview.owned_files - len(preview.missing) - len(preview.differing)} already there with the same bytes, "
          f"{len(preview.missing)} missing (the next sync creates them), {len(preview.differing)} differ (conflicts: never overwritten).")
    print(f"  the next sync there would do: {count_line(c)}")
    print(f"  Stays behind: {preview.stays_behind} files and the state file in {preview.old_label} are not touched.")
    if not args.apply:
        if not interactive():
            print("Nothing was changed. Repeat the same command with --apply to move the pipeline.")
            return EXIT_OK
        if input(f"Move pipeline '{args.name}' to {preview.new_label}? Type yes to apply: ").strip().lower() not in ("yes", "y"):
            print("Nothing was changed.")
            return EXIT_OK
    created = None
    try:
        if token:
            spec, created = with_vault(lambda: store_token(GitHubSpec.from_dict(dest), token, f"GitHub {'sign-in' if tokens else 'token'} for {args.name}", tokens))
            clone = dest.get("clone")
            dest = spec.to_dict() | {"auth": spec.auth}
            if clone:
                dest["clone"] = clone
        apply_change(brain, args.name, dest)
    except ChangeRefused as exc:
        raise UsageError(str(exc)) from None
    except Exception:
        if created:
            try:
                vault_mod.default_vault().remove(created)
            except VaultError:
                pass
        raise
    print(f"Moved. Pipeline '{args.name}' now writes to {preview.new_label}. Ownership, names and dates carried over; "
          f"{preview.stays_behind} files stay in {preview.old_label}. Check with: mdpipeline sync {args.name} --dry-run")
    return EXIT_OK


def cmd_scrub_paths(args, brain: Brain) -> int:
    from .destinations import destination_type

    pipeline = load_config(brain).get(args.name)
    if pipeline is None:
        raise UsageError(missing_pipeline_message(brain, args.name))
    if destination_type(pipeline.destination) != "github":
        raise UsageError("This only applies to a pipeline whose target is a GitHub repository (a folder target keeps its source_root).")
    res = sync_pipeline(brain, args.name, trigger="cli", dry_run=not args.apply, scrub_paths=True)
    print(format_result(res))
    if not args.apply:
        print("Nothing was changed. Run the same command with --apply to commit the provenance change.")
    return exit_for([res])


def cmd_pipeline_sign(args, brain: Brain, signing_in: bool) -> int:
    """`pipeline sign-in NAME` (sign in again) and `pipeline sign-out NAME`."""
    from . import vault as vault_mod
    from .destinations import destination_type
    from .targetconfig import GitHubSpec
    from .targetsetup import check_token

    config = load_config(brain)
    pipeline = config.get(args.name)
    if pipeline is None:
        raise UsageError(missing_pipeline_message(brain, args.name))
    dest = pipeline.destination or {}
    if destination_type(dest) != "github" or dest.get("auth") != "sign-in":
        raise UsageError(f"Pipeline '{args.name}' does not use Sign in with GitHub (its target is not a GitHub repository with a sign-in).")
    ref = dest.get("credential", "")
    if signing_in:
        tokens = cli_sign_in(dest["repo"])
        check_token(GitHubSpec.from_dict(dest), tokens.access_token)
        if ref.startswith("vault:") and with_vault(lambda: vault_mod.default_vault().has(ref[len("vault:"):])):
            with_vault(lambda: vault_mod.default_vault().replace(ref[len("vault:"):], tokens.to_json()))
        else:
            secret_id = with_vault(lambda: vault_mod.default_vault().add("github-sign-in", f"GitHub sign-in of {tokens.login} for {pipeline.name}", tokens.to_json()))
            dest["credential"] = f"vault:{secret_id}"
            save_config(brain, config)
        print(f"Signed in as {tokens.login}. Pipeline '{args.name}' will use this sign-in from now on.")
        return EXIT_OK
    others = [p for p in config.pipelines if p.name != args.name and p.destination and p.destination.get("credential") == ref]
    dest["credential"] = ""
    save_config(brain, config)
    if ref.startswith("vault:") and not others:
        with_vault(lambda: vault_mod.default_vault().remove(ref[len("vault:"):]))
    print(f"Signed out: the sign-in was removed from the vault and pipeline '{args.name}' no longer has a credential (its syncs stop until you sign in again).\n"
          "The app cannot revoke its access at GitHub without a client secret: to revoke it, open https://github.com/settings/applications "
          "and remove the app. Run `mdpipeline pipeline sign-in " + args.name + "` to sign in again.")
    return EXIT_OK


def cmd_github_app(args) -> int:
    from . import github_signin as gs

    if args.github_app_command == "set":
        gs.save_app_settings(args.client_id, args.slug)
        print(f"Saved: client ID {args.client_id.strip()}, app slug {args.slug.strip()}.")
        return EXIT_OK
    if args.github_app_command == "reset":
        gs.reset_app_settings()
        settings = gs.app_settings()
        print("Your own GitHub App was forgotten. " + (f"Using the shipped default again: client ID {settings['client_id']}, app slug {settings['slug']}."
                                                        if settings["client_id"] else "This build ships no default, so Sign in with GitHub is not set up until you run `github-app set`."))
        return EXIT_OK
    settings, source = gs.app_settings(), gs.app_source()
    origin = {"default": "shipped default", "user": "your own app (saved with `github-app set` or in the UI); `mdpipeline github-app reset` goes back to the shipped default",
              "none": "none: this build ships no default; run `github-app set`"}[source]
    print(f"Client ID: {settings['client_id'] or '(not set)'}\nApp slug:  {settings['slug'] or '(not set)'}\nInstall link: {gs.install_url() if settings['slug'] else '(not set)'}\nSource:    {origin}")
    return EXIT_OK


def edit_target_settings(args, edited: Pipeline) -> None:
    """The target-related flags of ``pipeline edit``: clone, token replacement and removal (a new repository or folder is `change-target`)."""
    from . import vault as vault_mod
    from .destinations import destination_type
    from .targetsetup import replace_token

    dest, _ = destination_from_args(args, edited.destination)       # refuses --github/--directory/--branch/--token here
    github = destination_type(edited.destination) == "github"
    if (args.clone or args.remove_clone or args.replace_token or args.remove_token) and not github:
        raise UsageError("--clone, --replace-token and --remove-token only apply to a pipeline whose target is a GitHub repository.")
    if args.clone and args.remove_clone:
        raise UsageError("Use either --clone DIR or --remove-clone.")
    if args.clone:
        edited.destination["clone"] = _clone_path(args.clone)
    if args.remove_clone:
        edited.destination.pop("clone", None)
    if args.replace_token:
        token = read_secret("New GitHub token (typing is hidden)", args.stdin)
        with_vault(lambda: replace_token(None, edited, token))
    if args.remove_token:
        ref = edited.destination.get("credential", "")
        edited.destination["credential"] = ""
        others = [p for p in load_config(find_brain(args.brain or os.getcwd())).pipelines if p.name != edited.name and p.destination and p.destination.get("credential") == ref]
        if ref.startswith("vault:") and not others:
            with_vault(lambda: vault_mod.default_vault().remove(ref[len("vault:"):]))


def cmd_test_target(args, brain: Brain) -> int:
    from .destinations import bind_target, destination_type, directory_root
    from .targetconfig import GitHubSpec, test_connection as github_test

    pipeline = load_config(brain).get(args.name)
    if pipeline is None:
        raise UsageError(missing_pipeline_message(brain, args.name))
    if destination_type(pipeline.destination) == "directory":
        root = directory_root(brain, pipeline.destination)
        if not root.is_dir():
            err(f"Problem: the target folder {root} does not exist.")
            return EXIT_RUNTIME
        writable = os.access(root, os.W_OK)
        print(f"Target folder: {root}\n"
              f"Write:         {'writable (nothing was written)' if writable else 'NOT writable'}")
        return EXIT_OK if writable else EXIT_RUNTIME
    spec = GitHubSpec.from_dict(pipeline.destination or {})
    try:
        conn = with_vault(lambda: github_test(spec))
    except TargetError as exc:
        err(f"Error: {exc}")
        return EXIT_RUNTIME
    print(f"Signed in as: {conn.user}\nCommit author: {conn.user}\nRepository:   {conn.repo}\nBranch:       {conn.branch}\nHead commit:  {conn.head_sha[:7]}  {conn.head_url}")
    print("Write:        " + ("allowed (nothing was written)" if conn.can_write else "NOT allowed"))
    for problem in conn.problems:
        err(f"Problem: {problem}")
    return EXIT_OK if conn.can_write else EXIT_RUNTIME


def cmd_pipeline_show(args, brain) -> int:
    """MD-95: one pipeline's configuration and status, read-only."""
    from .pipelineview import credential_text, show_view

    try:
        data = show_view(brain, args.name)
    except NotFound:
        raise UsageError(missing_pipeline_message(brain, args.name)) from None
    if args.json:
        print(json.dumps(data, indent=2))
        return EXIT_OK
    st, last = data["status"], data["status"]["last_run"]
    print(f"{data['name']}  [{st['status']}]  last sync: {st['last_sync_ts'] or 'never'}"
          + (f"  (last run {last['result']} by {last['trigger']}: {st['summary']})" if last else ""))
    print(f"  state:       {'paused (skipped by every trigger until resumed)' if data['paused'] else 'active'}")
    print("  sources:")
    for s in data["sources"]:
        if s["type"] == "jira":
            print(f"    - {s['label']} (Jira) {s['site']}" + ("" if s["enabled"] else "  [OFF: not read, its files are kept]"))
            print(f"        JQL: {s['jql']}")
            print(f"        {credential_text('email', s['credentials']['email'])}; {credential_text('API token', s['credentials']['token'])}")
        else:
            print(f"    - {s['label']} (folder) {s['path']}  [{s['check']['message']}]" + ("" if s["enabled"] else "  [OFF]"))
    d = data["destination"]
    if d["type"] == "github":
        print(f"  files land:  GitHub {d['repo']} @ {d['branch']} (sign-in: {'Sign in with GitHub' if d.get('auth') == 'sign-in' else 'personal access token'}"
              + (f", signed in as {d['login']}" if d.get("login") else "") + ")")
        print(f"               {credential_text('credential', d.get('credential'))}; local clone: {d.get('clone') or 'none'}")
        if d.get("credential_conflict"):
            print(f"  CONFLICT:    {d['credential_conflict']['message']} Syncs are blocked until then.")
    else:
        print(f"  files land:  directory {d['path']}")
    print(f"  target path: {data['target']}")
    np = data["name_policy"]
    print(f"  file names:  {np['policy']}" + (f" {np['pattern']}" if np["pattern"] else "") + (f"  (Example: {np['example']})" if np["example"] else ""))
    print(f"  include:     {', '.join(data['include']) or '-'}")
    print(f"  exclude:     {', '.join(data['exclude']) or '-'}")
    print(f"  on delete:   {data['on_source_delete']}")
    print(f"  guard:       {data['guard']['threshold_percent']:g}% of the files (at least {data['guard']['min_files']} files)")
    print(f"  files owned: {st['owned_files']}")
    commit = (st["destination"] or {}).get("last_commit")
    if commit:
        print(f"  last commit: {str(commit['sha'])[:7]} {commit.get('url', '')}")
    if st.get("pending_approval"):
        print(f"  waiting:     the last sync stopped at the guard; approve with: mdpipeline sync {data['name']} --approve")
    print(f"  next sync:   mdpipeline sync {data['name']} --dry-run   (nothing is written)")
    return EXIT_OK


def parse_switches(args, current: Pipeline) -> dict[str, bool]:
    """{label: on} from --source-off / --source-on; unknown labels and a label named both ways are usage errors."""
    off, on = list(args.source_off or []), list(args.source_on or [])
    labels = {s.label for s in current.sources}
    for label in off + on:
        if label not in labels:
            raise UsageError(f"Pipeline '{current.name}' has no source labelled '{label}'.")
    if set(off) & set(on):
        raise UsageError("Use either --source-off or --source-on for a source, not both.")
    return {**{label: False for label in off}, **{label: True for label in on}}


def owned_by_label(brain: Brain, pipeline: Pipeline, label: str) -> int:
    from .destinations import bind_target

    bound = bind_target(brain, pipeline)
    bound.target.begin()
    return sum(1 for r in State.load(bound).pipeline(pipeline.name).files.values() if r.source_label == label)


def print_next_sync_plan(brain: Brain, pipeline: Pipeline) -> None:
    """What the next sync of ``pipeline`` (as given, saved or not) would do: counts and the guard verdict. Writes nothing."""
    import time

    from .destinations import bind_target
    from .planner import plan_pipeline

    bound = bind_target(brain, pipeline)
    bound.target.begin()
    plan = plan_pipeline(bound, pipeline, State.load(bound).pipeline(pipeline.name), now=time.time())
    print(f"Next sync would: {count_line(plan.counts())}")
    print(f"Guard: {plan.guard.message}")


class RemovalBlocked(Exception):
    """A source removal with --delete-files stopped at the big-change guard."""


def print_removal_preview(brain: Brain, args) -> int:
    """`pipeline edit NAME --remove-source LABEL --dry-run`: what the removal would do; nothing is changed."""
    from .removal import preview_remove_source

    pv = preview_remove_source(brain, args.name, args.remove_source, delete_files=args.delete_files)
    if args.delete_files:
        print(f"Removing source '{args.remove_source}' with --delete-files would: delete {pv.deleted} files; keep {pv.left_edited} edited files (unowned).")
        print(f"Guard: {pv.guard.message}" + ("  (blocked: it needs --approve)" if pv.guard.blocked else ""))
    else:
        print(f"Removing source '{args.remove_source}' would leave its {pv.kept} files in the output folder kept (now unowned: no pipeline will touch them); nothing is deleted.")
    print("Nothing was changed.")
    return EXIT_OK


def cmd_pipeline(args) -> int:
    brain = resolve_brain(args)
    action = args.pipeline_command
    if action == "show":
        return cmd_pipeline_show(args, brain)
    if action == "test-target":
        return cmd_test_target(args, brain)
    if action == "change-target":
        return cmd_change_target(args, brain)
    if action == "scrub-paths":
        return cmd_scrub_paths(args, brain)
    if action in ("sign-in", "sign-out"):
        return cmd_pipeline_sign(args, brain, action == "sign-in")
    if action == "add":
        kwargs = {}
        for flag, key in (("target", "target"), ("include", "include"), ("exclude", "exclude"),
                          ("on_delete", "on_source_delete"), ("guard_percent", "guard_threshold_percent"),
                          ("guard_min_files", "guard_min_files")):
            if getattr(args, flag) is not None:
                kwargs[key] = getattr(args, flag)
        kwargs.setdefault("target", NEW_PIPELINE_TARGET)  # MD-140: raw/{relpath}; an explicit --target "" = the root (MD-133)
        kwargs.update(policy_kwargs(args, default=DEFAULT_NAME_POLICY))
        sources = [parse_source(s) for s in args.source or []]
        jira = jira_source_from_args(args)
        if jira is not None:
            ask_jira_token(args, jira)
            sources.append(jira)
        if not sources:
            raise UsageError("Name at least one source: --source PATH (a folder) or the --jira-* flags (a Jira source).")
        destination, token = destination_from_args(args, adding=True)
        pipeline = Pipeline.new(args.name, sources, destination=destination, **kwargs)
        tokens = None
        if destination and destination.get("type") == "github" and destination.get("auth") == "sign-in":
            from .validation import validate_pipeline

            pipeline.destination["credential"] = "vault:v_pending"
            checked = validate_pipeline(brain, load_config(brain), pipeline)       # the cheap checks before asking the person to sign in
            if not checked.ok:
                raise ValidationFailed(checked)
            tokens = cli_sign_in(destination["repo"])
        result = create_pipeline_with_token(brain, pipeline, token, with_vault, tokens)
        report_validation(result)
        print(f"Pipeline '{pipeline.name}' saved ({result.file_count} matching files). Try: mdpipeline sync {pipeline.name} --dry-run")
        return EXIT_OK
    if action == "list":
        config = load_config(brain)
        statuses = {s["name"]: s for s in brain_status(brain)["pipelines"]}
        rows = [pipeline_row(brain, p, statuses[p.name]) for p in config.pipelines]
        if args.json:
            print(json.dumps(rows, indent=2))
            return EXIT_OK
        if not rows:
            print("No pipelines yet. Create one with: mdpipeline pipeline add NAME --source PATH")
            return EXIT_OK
        import_hint(brain)
        for r in rows:
            print(f"{r['name']}  [{r['status']}]  last sync: {r['last_sync'] or 'never'}")
            print("    sources:  " + "; ".join(describe_source(s) for s in r["sources"]))
            print(f"    target:   {r['target']}  ->  {r['destination']['label']}")
            print(f"    on delete: {r['on_source_delete']}")
            print(f"    file names: {r['name_policy']}" + (f" {r['name_pattern']}" if r["name_pattern"] else ""))
        return EXIT_OK
    if action in ("pause", "resume"):
        try:
            changed = set_paused(brain, args.name, action == "pause", trigger="cli")
        except NotFound:
            raise UsageError(missing_pipeline_message(brain, args.name)) from None
        print(f"Pipeline '{args.name}' {'paused' if action == 'pause' else 'resumed'}." if changed
              else f"Pipeline '{args.name}' was already {'paused' if action == 'pause' else 'running'}.")
        return EXIT_OK
    if action == "remove":
        if load_config(brain).get(args.name) is None:               # MD-139: before any confirmation prompt
            raise UsageError(missing_pipeline_message(brain, args.name))
        count = len(State.load(brain).pipeline(args.name).files)
        if args.purge and not args.yes:
            print(f"--purge will delete the {count} files pipeline '{args.name}' owns in the output folder at {brain.root} (files you edited are left alone).")
            answer = input(f"Type the pipeline name ({args.name}) to confirm: ").strip()
            if answer not in (args.name, "yes", "y"):
                err("Aborted: nothing was removed.")
                return EXIT_RUNTIME
        try:
            res = remove_pipeline(brain, args.name, trigger="cli", purge=args.purge, confirm_count=count if args.purge else None)
        except ConfirmationRequired as exc:
            raise UsageError(str(exc)) from exc
        if args.purge:
            print(f"Pipeline '{args.name}' removed from {brain.root}; {res.deleted} files deleted.")
            if res.left_edited:
                print(f"{len(res.left_edited)} files you edited were kept (unowned): " + ", ".join(res.left_edited))
        else:
            print(f"Pipeline '{args.name}' removed from {brain.root}; {res.unowned} files kept in the output folder (now unowned: no pipeline will touch them).")
        return EXIT_OK
    if action == "edit":
        config = load_config(brain)
        current = config.get(args.name)
        if current is None:
            raise UsageError(missing_pipeline_message(brain, args.name))
        if args.remove_source and not any(s.label == args.remove_source for s in current.sources):
            raise UsageError(f"Pipeline '{args.name}' has no source labelled '{args.remove_source}'.")
        if (args.delete_files or args.approve) and not args.remove_source:
            raise UsageError("--delete-files and --approve only apply together with --remove-source.")
        if args.dry_run and not (args.remove_source or args.source_on):
            raise UsageError("--dry-run only applies together with --remove-source or --source-on.")
        switches = parse_switches(args, current)
        if args.dry_run and args.remove_source:
            return print_removal_preview(brain, args)
        edited = Pipeline.from_dict(current.to_dict())
        relabels = {}
        if args.rename_label:
            old, new = args.rename_label
            match = next((s for s in edited.sources if s.label == old), None)
            if match is None:
                raise UsageError(f"Pipeline '{args.name}' has no source labelled '{old}'.")
            match.label = new
            relabels[old] = new
        for text in args.add_source or []:
            edited.sources.append(parse_source(text))
        apply_jira_edit(args, edited)
        for flag, key in (("target", "target"), ("include", "include"), ("exclude", "exclude"),
                          ("on_delete", "on_source_delete"), ("guard_percent", "guard_threshold_percent"),
                          ("guard_min_files", "guard_min_files")):
            if getattr(args, flag) is not None:
                setattr(edited, key, getattr(args, flag))
        for key, value in policy_kwargs(args).items():
            setattr(edited, key, value)
        edit_target_settings(args, edited)
        for src in edited.sources:
            if src.label in switches:
                src.enabled = switches[src.label]
        if args.dry_run:                       # --source-on --dry-run: the plan of the next sync with the source on, nothing saved
            print_next_sync_plan(brain, edited)
            print("Nothing was changed.")
            return EXIT_OK
        removed: list = []
        if args.remove_source:
            edited.sources = [src for src in edited.sources if src.label != args.remove_source]       # the new configuration is checked without it

        def remove_it() -> None:           # runs once the new configuration passed every check, so a refused replacement removes nothing
            res = remove_source(brain, args.name, args.remove_source, trigger="cli", delete_files=args.delete_files, approve=args.approve,
                                allow_empty=bool(edited.sources))
            removed.append(res)
            if res.result == "blocked":
                raise RemovalBlocked

        try:
            result = update_pipeline(brain, args.name, edited, relabels, after_validation=remove_it if args.remove_source else None)
        except RemovalBlocked:
            res = removed[0]
            print(f"{res.message}\nFiles that would be deleted:")
            for path in res.affected:
                print(f"    {path}")
            print("Nothing was changed. Repeat the command with --approve to apply exactly this deletion.")
            return EXIT_BLOCKED
        if removed:
            res = removed[0]
            if args.delete_files:
                print(f"Source '{args.remove_source}' removed; {res.deleted} files deleted; {res.unowned} edited files kept (unowned).")
            else:
                print(f"Source '{args.remove_source}' removed; {res.unowned} files kept in the output folder (now unowned: no pipeline will touch them).")
            if not (args.add_source or args.source_off or args.source_on or args.rename_label or args.remove_clone or args.replace_token or args.remove_token
                    or any(getattr(args, f) is not None for f in ("target", "directory", "github", "branch", "clone", "include", "exclude", "on_delete", "guard_percent", "guard_min_files", "name_policy", "name_pattern", "jira_site", "jira_jql"))):
                return EXIT_OK
        report_validation(result)
        print(f"Pipeline '{args.name}' updated.")
        for label, on in switches.items():
            if on:
                print(f"Source '{label}' is On again.")
            else:
                print(f"Source '{label}' is Off: it is not read, and its {owned_by_label(brain, edited, label)} files are kept and stay owned (never deleted because of it). "
                      f"Switch it back on with --source-on {label}.")
        if any(switches.values()):
            print_next_sync_plan(brain, edited)
        if (edited.name_policy, edited.name_pattern) != (current.name_policy, current.name_pattern) and State.load(brain).pipeline(args.name).files:
            print(f"The file-name policy changed. Nothing was renamed yet: run `mdpipeline sync {args.name} --dry-run` to see every "
                  "rename (old path -> new path); the next sync applies it, and the big-change guard asks for approval above its threshold.")
        return EXIT_OK
    raise UsageError(f"Unknown pipeline action {action}")


def cmd_state_rebuild(args, brain) -> int:
    preview = rebuild_preview(brain, args.pipeline) if not args.apply else None
    if preview is None:
        try:
            preview = apply_rebuild(brain, approve=args.approve, pipeline=args.pipeline)
        except RebuildRefused as exc:
            err(f"Error: {exc}")
            return EXIT_BLOCKED if "--approve" in str(exc) else EXIT_USAGE
    print(f"{preview.claimed_total} files would be owned again" + (" (done)" if preview.applied else "") + ".")
    for name, plan in preview.plans.items():
        print(f"  {name}: next sync would do: {count_line(plan.counts())}")
        print(f"    guard: {plan.guard.message}")
    if preview.unclaimed:
        print(f"{len(preview.unclaimed)} files in the output folder are left unowned (Needs you):")
        for u in preview.unclaimed:
            print(f"    {u.path}: {u.reason}")
        if any(u.reason == MISMATCH for u in preview.unclaimed):
            print(f"For the files that say \"{MISMATCH}\": {NEXT_STEP}")
    if not preview.applied:
        print("Nothing was changed. Run the same command with --apply to write the rebuilt state.")
    return EXIT_OK


def cmd_state(args) -> int:
    brain = resolve_brain(args)
    if args.state_command == "rebuild":
        return cmd_state_rebuild(args, brain)
    preview = preview_restore(brain, args.generation, args.pipeline)
    print(f"Backup {preview.generation} ({preview.backup}) records {preview.owned_files} owned files.")
    print("With this state the next sync of each pipeline would do:")
    for name, plan in preview.plans.items():
        print(f"  {name}: {count_line(plan.counts())}")
        for conflict in plan.conflicts:
            print(f"      conflict ({conflict['type']}) {conflict['path']}: {conflict['reason']}")
    if not args.apply:
        print("Nothing was changed. Run the same command with --apply to restore this backup.")
        return EXIT_OK
    apply_restore(brain, args.generation, pipeline=args.pipeline)
    kept = "The replaced state stays in the repository's history." if preview.data is not None else "The replaced file is kept in .mdpipeline/backups/state-before-restore.json."
    print(f"State restored from backup {preview.generation}. {kept}")
    return EXIT_OK


def human_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    seconds = max(0, seconds)           # MD-170: a timestamp from a clock that was ahead is "0s", never "-10735s"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def count_line(counts: dict) -> str:
    keys = ("created", "updated", "renamed", "deleted", "kept", "skipped", "conflicts", "unchanged", "errors")
    return ", ".join(f"{k} {counts[k]}" for k in keys if k != "renamed" or counts.get(k))


def format_result(res: SyncResult) -> str:
    head = f"{res.pipeline}: {res.result}" + (" (dry run)" if res.dry_run else "")
    lines = [head]
    if res.message:
        lines.append(f"  {res.message}")
    if res.commit:
        lines.append(f"  commit: {res.commit.url}")
    if res.plan:
        for group in res.plan.groups:
            lines.append(f"  {group.label}  {group.path}" + (f"  [{group.status}: {group.error}]" if group.status != "ok" else ""))
            for item in group.items:
                if item.action == "unchanged":
                    continue
                where = item.target or "-"
                note = f"  ({item.reason})" if item.reason and not item.from_target else ""
                if item.from_target:
                    note = f"  (renamed from {item.from_target})"
                kind = f"conflict ({item.conflict_type})" if item.action == "conflict" else ("error" if item.error else item.action)
                lines.append(f"    {kind:<13} {where}  <- {item.display or item.source_path}{note}")
            lines.append(f"    {count_line(group.counts())}")
        lines.append(f"  total: {count_line(res.counts())}" if len(res.plan.groups) > 1 else "")
        if res.plan.metadata_only:
            lines.append(f"  note: {res.plan.metadata_only} metadata-only updates (front-matter or provenance changed, same body) are not counted by the guard.")
        lines.append(f"  guard: {res.plan.guard.message}")
        lines.extend(f"  note: {n}" for n in res.plan.notes)
    return "\n".join(l for l in lines if l != "")


def exit_for(results: list[SyncResult]) -> int:
    worst = EXIT_OK
    order = [EXIT_OK, EXIT_CONFLICTS, EXIT_BLOCKED, EXIT_BUSY, EXIT_RUNTIME]
    mapping = {"ok": EXIT_OK, "skipped: paused": EXIT_OK, "cancelled": EXIT_OK, "conflicts": EXIT_CONFLICTS,
               "blocked": EXIT_BLOCKED, "busy": EXIT_BUSY, "error": EXIT_RUNTIME}
    for r in results:
        code = mapping[r.result]
        if order.index(code) > order.index(worst):
            worst = code
    return worst


def cmd_sync(args) -> int:
    brain = resolve_brain(args)
    if not args.pipeline and not args.all:
        raise UsageError("Name a pipeline to sync, or use --all.")
    if args.pipeline and args.all:
        raise UsageError("Use either a pipeline name or --all, not both.")
    kw = dict(trigger="cli", dry_run=args.dry_run, approve=args.approve, force=args.force)
    if args.all:
        results = sync_all(brain, **kw)
        if not results:
            print("No pipelines to sync.")
    else:
        if load_config(brain).get(args.pipeline) is None:
            raise UsageError(missing_pipeline_message(brain, args.pipeline))
        results = [sync_pipeline(brain, args.pipeline, **kw)]
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, default=str))
    else:
        for r in results:
            print(format_result(r))
    from . import usage_hooks

    for r in results:
        usage_hooks.dry_run_viewed(r)
    if not args.json and not getattr(args, "dry_run", False) and any(r.result in ("ok", "conflicts") and not r.dry_run for r in results):
        from .scheduler import ensure_background

        announcement = ensure_background(acknowledge=True)          # MD-121: the first successful sync turns scheduled sync on, and says so once
        if announcement:
            print("\n" + announcement + "\nChange it with: mdpipeline schedule set PIPELINE 6h   Turn it off with: mdpipeline schedule background off")
    return exit_for(results)


def cmd_update(args) -> int:
    from . import install_info, lifecycle

    method = install_info.install_method()
    command = lifecycle.update_command(method)
    if command is None:
        print("This copy runs from a checkout of the source. To update it: git pull, then `uv tool install --reinstall .` in the app folder.")
        return EXIT_OK
    shown = " ".join(command)
    if args.dry_run:
        print(f"Would run: {shown}")
        return EXIT_OK
    print(f"Running: {shown}")
    try:
        code = lifecycle.run_command(command)
    except FileNotFoundError:
        err(f"Error: '{command[0]}' was not found, so the app was not updated. Install it, or run the update with the tool you installed with.")
        return EXIT_RUNTIME
    if code != 0:
        err(f"Error: the update command failed (exit {code}), so the app was not updated. Nothing else was changed.")
        return EXIT_RUNTIME
    print("Updated. Your configuration, vault and files are as they were. Background sync starts the new version at your next login "
          "(or now: mdpipeline schedule background off, then on).")
    return EXIT_OK


def cmd_uninstall(args) -> int:
    from . import background, install_info, lifecycle, scheduler
    from .vault import data_dir

    scheduler.turn_off_background()
    background.stop()
    print("Background sync is off and its login item is removed.")
    print(f"To remove the program, run:  {lifecycle.uninstall_command(install_info.install_method())}")
    print("Your synced Markdown files, your output folders and their configuration are untouched.")
    print(f"The app's own data (vault, settings) is in {data_dir()}: delete that folder too if you want everything gone.")
    return EXIT_OK


DEFAULT_BRAIN_NAME = "markdown-pipeline"


def default_brain() -> Brain:
    """The brain a first-time person gets: ``~/markdown-pipeline`` (made on first use, left alone afterwards)."""
    try:
        return init_brain(Path.home() / DEFAULT_BRAIN_NAME, create=True)
    except BrainError as exc:
        raise UsageError(str(exc)) from exc


def cmd_start(args) -> int:
    """Open the UI: the brain given, else the registered one at or above here, else the default one (made if missing)."""
    try:
        brain = find_brain(args.brain or os.getcwd())
    except BrainError:
        if args.brain:
            raise
        brain = default_brain()
    print(f"Your files go to {brain.root}")
    return serve_ui(brain, args)


def cmd_ui(args) -> int:
    return serve_ui(resolve_brain(args), args)


def serve_ui(brain: Brain, args) -> int:
    from .ui_server import make_server

    server = make_server(brain, port=args.port)
    print(f"Markdown Pipeline UI: {server.url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        import webbrowser

        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def cmd_connect(args) -> int:
    from .snippets import claude_code_command, claude_code_json, codex_toml

    brain = resolve_brain(args)
    if args.agent in (None, "claude"):
        print("== Claude Code ==\nRun this once (adds the server to your Claude Code config):\n")
        print(f"    {claude_code_command(brain)}\n")
        print("or put this in .mcp.json in your project:\n")
        print(claude_code_json(brain) + "\n")
    if args.agent in (None, "codex"):
        print("== Codex ==\nAdd this to ~/.codex/config.toml:\n")
        print(codex_toml(brain))
    print("Then ask your agent to search your files, e.g. \"search my files for pricing\".")
    return EXIT_OK


def cmd_cancel(args) -> int:
    brain = resolve_brain(args)
    try:
        res = cancel_pending(brain, args.pipeline, trigger="cli")
    except KeyError:
        raise UsageError(missing_pipeline_message(brain, args.pipeline)) from None
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    print(f"{res.pipeline}: {res.result}\n  {res.message}")
    return EXIT_OK


def import_hint(brain: Brain) -> None:
    from .secretimport import pending_imports

    items = pending_imports(brain)
    if items:
        print(f"Note: {sum(len(i.pipelines) for i in items)} GitHub target(s) still read their token through an old env:/keychain: reference. "
              "Move it into the app vault with: mdpipeline vault import")


def cmd_status(args) -> int:
    brain = resolve_brain(args)
    data = brain_status(brain, args.pipeline)
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return EXIT_OK
    import_hint(brain)
    if not data["pipelines"]:
        print("No pipelines.")
    for s in data["pipelines"]:
        last = s["last_run"]
        print(f"{s['name']}  [{s['status']}]")
        target = s["target"]
        print(f"    target: {target['label']}" + (f"  [unreachable: {target['error']}]" if target.get("error") else ""))
        if target.get("last_commit"):
            print(f"    last commit: {target['last_commit']['url']}")
        clone = target.get("clone_status")
        if clone and clone["status"] == "skipped":
            print(f"    clone: NOT updated - {clone['reason']}")
        if last:
            print(f"    last run: {last['ts']} via {last['trigger']}  result: {last['result']}  {count_line(last['counts'])}")
        else:
            print("    last run: never")
        for c in s["conflicts"]:
            print(f"    conflict ({c['type']}): {c['path']}")
            if c.get("next_step"):
                print(f"        {c['next_step']}")
        if s["guard"]:
            print(f"    guard: {'BLOCKED - ' if s['guard']['blocked'] else ''}{s['guard']['message']}")
        for src in s.get("sources", []):
            if src["type"] != "folder":
                note = f"last error: {src['last_error_category']}" if src["last_error_category"] else "ok"
                print(f"    source {src['label']} ({src['type']}): last fetch {src['last_fetch'] or 'never'}, {note}")
        if s.get("warnings"):
            print(f"    conversion warnings in the last run: {s['warnings']}")
        print(f"    age: {human_age(s['age_seconds'])} since the last successful sync; owned files: {s['owned_files']}")
    print(f"output folder age: {human_age(data['brain_age_seconds'])} (time since the most recent successful sync)")
    return EXIT_OK


def cmd_log(args) -> int:
    brain = resolve_brain(args)
    events = ActivityLog(brain).read(pipeline=args.pipeline, limit=args.limit)
    if args.json:
        print(json.dumps(events, indent=2))
        return EXIT_OK
    for e in events:
        who = e.get("pipeline") or "-"
        if e["type"] == "run":
            print(f"{e['ts']}  {who}  {e['trigger']}  {e['result']}  {count_line(e['counts'])}")
        elif e["type"] == "mcp_call":
            print(f"{e['ts']}  -  mcp  tool:{e['tool']}")
        else:
            print(f"{e['ts']}  {who}  {e.get('trigger', '-')}  event:{e.get('action')}")
    return EXIT_OK


USAGE_CARD = """\
Help improve Markdown Pipeline.
Share anonymous usage: which setup steps you complete, errors, counts and timings.
Never your content, titles, file names, paths, queries or tokens.
You can skip this: if you don't choose, nothing is shared. Change it any time with `mdpipeline usage on|off`."""
USAGE_HINT = ("Anonymous usage data is off (you haven't chosen), so nothing is shared. To share it: mdpipeline usage on. "
              "To see exactly what would be sent: mdpipeline usage status.")


def needs_usage_notice(command: str) -> bool:
    """The MCP server speaks over stdio (nothing may be printed or asked there) and `usage` is the place the choice is made."""
    return command not in ("mcp", "usage", "background", "start", "uninstall", "install-note")   # `start` opens the UI, whose first screen asks (a terminal question first would double it)


def first_run_usage_notice() -> None:
    """FR-S1 AC6: the first interactive run asks once; a non-interactive run says once how to opt in. Never blocks, never fails the command."""
    from . import usage

    try:
        store = usage.UsageStore()
        if store.consent != "unanswered":
            return
        if interactive() and stderr_visible():
            if store.notice_seen("cli_ask"):
                return
            err(USAGE_CARD)
            print("Share anonymous usage? [s]hare / [d]on't share / Enter to skip: ", end="", file=sys.stderr, flush=True)   # stderr: input() would print to stdout
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = ""
            choice = "share" if answer in ("s", "share") else "decline" if answer in ("d", "decline", "don't share", "dont share") else "no_answer"
            store.decide(choice, usage.get_transport())
            store.mark_notice_seen("cli_ask")
            return
        if not store.notice_seen("cli_hint"):
            err(USAGE_HINT)
            store.mark_notice_seen("cli_hint")
    except Exception:          # noqa: BLE001 - a notice must never stop the command the user asked for
        return


def _when(ts: float | None, now: float) -> str:
    if ts is None:
        return "after its first sync"
    return "now" if ts <= now + 1 else f"in {human_age(ts - now)}"


def cmd_schedule(args) -> int:
    from . import scheduler
    from .config import SCHEDULE_VALUES
    from .service import set_schedule

    brain = resolve_brain(args)
    action = args.schedule_action or "show"
    if action == "set":
        try:
            changed = set_schedule(brain, args.pipeline, args.interval, trigger="cli")
        except ValueError as exc:
            raise UsageError(str(exc)) from exc
        if not changed:
            print(f"{args.pipeline} already syncs on '{args.interval}'.")
        elif args.interval == "off":
            print(f"{args.pipeline}: scheduled sync is off.")
        else:
            print(f"{args.pipeline}: syncs every {args.interval}.")
        return EXIT_OK
    if action == "background":
        if args.state == "off":
            scheduler.turn_off_background()
            print("Background sync is off: nothing syncs by itself and the login item is removed. Turn it on again: mdpipeline schedule background on")
        else:
            scheduler.turn_on_background()
            print("Background sync is on: it keeps running when this window is closed and starts again when you log in.")
            _print_linger_line()
        return EXIT_OK
    now = time.time()
    data = brain_status(brain, now=now)
    from . import background

    if not scheduler.background_enabled():
        state = "off"
    elif background.is_running():
        state = "on"
    elif background.started_this_run:         # MD-166: this very command started it; never say "not running" about it
        state = "on" if background.wait_until_running() else "on (starting now)"
    else:
        state = "on but not running (start it with: mdpipeline background)"
    print(f"Background sync: {state} (intervals: {', '.join(SCHEDULE_VALUES)})")
    if state != "off":
        _print_linger_line()
    for p in data["pipelines"]:
        interval = "off" if p["schedule"] == "off" else f"every {p['schedule']}"
        last = "never" if p["last_sync"] is None else f"{human_age(now - parse_ts(p['last_sync']))} ago"
        nxt = "-" if p["next_sync"] is None and p["schedule"] == "off" else _when(p["next_sync"], now)
        print(f"{p['name']}: {interval}; last sync {last}; next sync {nxt}" + ("; paused" if p["paused"] else ""))
    return EXIT_OK


def _print_linger_line() -> None:
    """MD-166: where the person is at that moment (the CLI), say what happens without linger. Never runs it."""
    from . import autostart

    if autostart.linger_notice():
        print(autostart.LINGER_LINE_PLAIN)
        from . import usage_hooks

        usage_hooks.linger_notice_shown("cli")      # MD-168: once a day


def cmd_background(args) -> int:
    from . import background

    if not background.run(how=getattr(args, "started_by", "manual")):
        print("The background process is already running.")
    return EXIT_OK


def cmd_usage(args) -> int:
    from . import usage

    store, action = usage.UsageStore(), args.usage_action or "status"
    if action == "on":
        store.decide("share", usage.get_transport())
        print("Sharing anonymous usage is on. See exactly what is sent: mdpipeline usage log")
    elif action == "off":
        store.decide("decline", usage.get_transport())
        print("Anonymous usage is off. Nothing is sent.")
    elif action == "reset-id":
        print(f"New install ID: {store.reset_install_id()}")
    elif action == "flush":
        sent = store.flush(usage.get_transport())
        print(f"Sent {sent} event{'' if sent == 1 else 's'}.")
    elif action == "counter":
        store.set_counter_enabled(args.state == "on")
        print(f"The consent counter is {args.state}.")
    elif action == "log":
        log = store.view()["log"]
        if args.json:
            print(json.dumps(log, indent=2))
        elif not log:
            print("Nothing has been sent from this computer.")
        for entry in ([] if args.json else log):
            note = "  (goes to the team: sent because you chose to)" if entry.get("chosen") else ""
            print(f"{entry.get('at', '-')}  {entry['event']}  {json.dumps(entry['properties'])}{note}")
    else:
        view = store.view()
        state = {"share": "Sharing anonymous usage.", "decline": "Not sharing (you chose Don't share). Nothing is sent.",
                 "unanswered": "Not chosen yet, so nothing is shared."}[view["consent"]]
        print(f"Anonymous usage data: {state}\n")
        print("What we send:\n" + "\n".join(f"  - {line}" for line in view["what_we_send"]))
        print("\nNever sent:\n" + "\n".join(f"  - {line}" for line in view["never_sent"]))
        print(f"\n{view['always_sent']}\n\n{view['sent_on_purpose']}\n\nInstall ID: {view['install_id']}   (reset: mdpipeline usage reset-id)\n"
              "Change any time: mdpipeline usage on | off")
    return EXIT_OK


def starts_usage_helper(command: str) -> bool:
    """Short CLI runs hand queued usage events to a detached sender when they end. Long-lived commands send by themselves; ``usage`` is the sender."""
    return command not in ("mcp", "ui", "usage", "background", "start", "uninstall", "install-note")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv or ["start"])           # just `mdpipeline` opens the app
    try:
        return _main(args)
    finally:
        if starts_usage_helper(args.command):
            from . import usage

            usage.flush_detached()           # returns at once: the command never waits for the network


def self_heal_background(command: str) -> None:
    """MD-165: background sync is on but not running (a reboot with no desktop login): any command starts it again. Never breaks the command."""
    if command in ("background", "uninstall"):
        return
    try:
        from . import background

        background.ensure_running()
    except Exception:                   # noqa: BLE001
        return


def _main(args) -> int:
    self_heal_background(args.command)
    if needs_usage_notice(args.command):
        first_run_usage_notice()
        if args.command != "ui":                     # the UI reports its own launch
            from . import usage_hooks

            usage_hooks.app_opened(first_only=True)
    try:
        if args.command == "usage":
            return cmd_usage(args)
        if args.command == "schedule":
            return cmd_schedule(args)
        if args.command == "background":
            return cmd_background(args)
        if args.command == "init":
            return cmd_init(args)
        if args.command == "pipeline":
            return cmd_pipeline(args)
        if args.command == "target":
            return cmd_target(args)
        if args.command == "jira":
            return cmd_jira(args)
        if args.command == "vault":
            return cmd_vault(args)
        if args.command == "github-app":
            return cmd_github_app(args)
        if args.command == "sync":
            return cmd_sync(args)
        if args.command == "ui":
            return cmd_ui(args)
        if args.command == "start":
            return cmd_start(args)
        if args.command == "install-note":
            from . import usage
            from .install_info import record_install_method

            usage.internal_flag()            # MD-154: an install made with MDPIPELINE_INTERNAL=1 is marked now, before anyone has chosen anything
            return EXIT_OK if record_install_method(args.method) else EXIT_USAGE
        if args.command == "update":
            return cmd_update(args)
        if args.command == "uninstall":
            return cmd_uninstall(args)
        if args.command == "connect":
            return cmd_connect(args)
        if args.command == "cancel":
            return cmd_cancel(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "log":
            return cmd_log(args)
        if args.command == "state":
            return cmd_state(args)
        if args.command == "mcp":
            from .mcp_server import serve

            serve(resolve_brain(args))
            return EXIT_OK
        raise UsageError(f"Unknown command {args.command}")
    except ValidationFailed as exc:
        report_validation(exc.result)
        return EXIT_USAGE
    except BusyError as exc:
        err(f"Error: {exc}. Try again in a moment.")
        return EXIT_BUSY
    except NotFound as exc:
        brain = getattr(args, "resolved_brain", None)
        if brain is None:
            try:
                brain = find_brain(getattr(args, "brain", None) or os.getcwd())
            except BrainError:
                brain = None
        err("Error: " + (missing_pipeline_message(brain, str(exc).split("/")[0]) if brain else f"No pipeline named '{exc}'."))
        return EXIT_USAGE
    except UsageError as exc:
        err(f"Error: {exc}")
        return EXIT_USAGE
    except (ConfigReadError, StateError, OSError, TargetError, VaultError) as exc:
        err(f"Error: {exc}")
        return EXIT_RUNTIME
    except (BrainError, RestoreError) as exc:
        err(f"Error: {exc}")
        return EXIT_USAGE
