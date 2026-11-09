"""The local MCP server. Started on demand by the agent client over stdio (no daemon).

Tools: ``search``, ``read``, ``list`` (read-only retrieval; no LLM), (MD-122) ``recent_changes``, and (MD-23) ``sync_status``
and ``sync``. There is no tool that writes or edits brain content. Every call is logged by tool name
and time only (never the query or the path).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, changes, usage
from .brain import CONFIG_DIR_NAME, CONFIG_FILE_NAME, Brain, BrainError, find_brain
from .brainquery import QueryError, list_path, read_file, search
from .config import load_config
from .engine import sync_all, sync_pipeline
from .install_info import command_prefix
from .log import ActivityLog
from .schedulestore import SchedulerStore
from .status import brain_status

INSTRUCTIONS = (
    "Search, read and list the Markdown knowledge in this brain (the files its pipelines wrote, plus raw/ and wiki/). Every result carries "
    "provenance (source pipeline, folder label and source path) so answers can cite their source. "
    "Retrieval only: you write the answer yourself."
)


# MD-213: what each tool changes, for the agent and for directory listings. The three that read files only read the brain; none of them reaches the network.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
SYNC_EFFECTS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)      # writes files inside the brain, deletes only what a source deleted

PIPELINE_ARG = Annotated[str | None, Field(description="Name of one pipeline, exactly as `list`/`sync_status` show it. Omit it to cover every pipeline.")]


# MD-196 (registry R1): the words an agent shows when the app has not been set up yet. One next step. Marketing reviews them before release.
def setup_message(method: str | None = None) -> str:
    """Marketing's words (MM-147): one command, the one this install can run (``uvx mdpipeline`` for a uvx or Registry install)."""
    return ("Markdown Pipeline is installed but not set up yet, so there are no files to read. It runs on Linux for now. "
            f"To finish, run `{command_prefix(method)} start` in a terminal. It opens the app in your browser, where you choose a "
            "source and where the files go. When the first sync is done, ask again; no restart is needed.")


SEVERAL_MESSAGE = ("More than one output folder is set up, and this server was not told which to use. "
                   "To finish: add `--brain FOLDER` to this server's command (`mdpipeline connect` prints it for the folder you choose), then ask again.")


class NeedsSetup(Exception):
    """No usable output folder (the message is what the agent shows the user)."""


def brain_provider(explicit: str | None, cwd: Callable[[], str] = os.getcwd) -> Callable[[], Brain]:
    """Find the brain on every tool call, so a person who finishes setup while the agent keeps the server running needs no restart.

    The same lookup as the command line first (``--brain``, else the working directory and its parents). When the client started the server somewhere
    with no output folder (a registry install has no ``--brain``), the one output folder the app has registered is used; with several, the person must say which.
    """
    def provide() -> Brain:
        try:
            return find_brain(explicit or cwd())
        except BrainError:
            pass
        if explicit:
            raise NeedsSetup(setup_message())
        known = [Path(p) for p in SchedulerStore().read()["brains"] if (Path(p) / CONFIG_DIR_NAME / CONFIG_FILE_NAME).is_file()]
        if len(known) == 1:
            return Brain(known[0])
        raise NeedsSetup(SEVERAL_MESSAGE if known else setup_message())

    return provide


def build_server(brain: "Brain | Callable[[], Brain]") -> MCPServer:
    server = MCPServer("mdpipeline", instructions=INSTRUCTIONS, version=__version__)
    provide = brain if callable(brain) else (lambda: brain)
    announced = []

    def setup_needed(exc: NeedsSetup) -> dict:
        """No usable output folder: one message with one next step (counted once per server process, with consent, as a notice)."""
        if not announced:
            announced.append(True)
            usage.emit("notice_shown", kind="setup_mode")
        return {"status": "setup_needed", "message": str(exc)}


    @server.tool(name="search", annotations=READ_ONLY, description="Keyword/full-text search over the brain (the folder of Markdown files Markdown Pipeline keeps current from your team's tools): the files its pipelines wrote, plus raw/ and wiki/. "
                 "Read-only: it changes nothing. Returns {results: [...]} ranked best first; each result has the file path, a score, up to 3 matching passages "
                 "(line number and text) and the file's provenance. Covers every pipeline target that has local files: files of other targets have paths like "
                 "'@pipeline/raw/...' and a 'target' label. Use quotes in the query for an exact phrase. An empty query is an error. "
                 "Use search when you do not know which file holds the answer; use `read` when you already have the path, and `list` to see what exists. "
                 "Use `recent_changes`, not search, to ask what changed.")
    def search_tool(
        query: Annotated[str, Field(description="Words to look for, as plain text. Put an exact phrase in double quotes. Must not be empty.")],
        path_prefix: Annotated[str | None, Field(description="Only search under this folder, relative to the brain, e.g. 'raw/projects/biz' or '@pipeline/raw/docs'. Omit it to search everything.")] = None,
        limit: Annotated[int, Field(description="How many files to return, best first. Default 10, at most 50 (a larger number is lowered to 50).")] = 10,
    ) -> dict:
        try:
            brain = provide()
        except NeedsSetup as exc:
            return setup_needed(exc)
        log = ActivityLog(brain)
        log.mcp_call("search")
        usage.note_agent_read()          # MD-116: a count per day, nothing about what was asked
        try:
            return {"results": search(brain, query, path_prefix, limit)}
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="read", annotations=READ_ONLY, description="Read one Markdown file of the brain (the folder of Markdown files Markdown Pipeline keeps current from your team's tools). Read-only. Returns the whole file: {path, content, provenance, size} "
                 "(provenance is the parsed source metadata, null when the file has none; size is in bytes). Errors (ToolError) for a path that does not exist, "
                 "is a folder, is not a Markdown file, contains '..', is hidden or the app's own folder, or is outside the files a pipeline wrote and raw/ and wiki/. "
                 "Use read when you have the path (from `search` or `list`); it does not search.")
    def read_tool(
        path: Annotated[str, Field(description="Path of one .md file, relative to the brain, e.g. 'raw/projects/biz/pricing.md'. For a pipeline target that is not the brain's own folder use '@pipeline/raw/...' as `search` and `list` print it.")],
    ) -> dict:
        try:
            brain = provide()
        except NeedsSetup as exc:
            return setup_needed(exc)
        log = ActivityLog(brain)
        log.mcp_call("read")
        usage.note_agent_read()          # MD-116: a count per day, nothing about what was asked
        try:
            return read_file(brain, path)
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="list", annotations=READ_ONLY, description="List what is in the brain (the folder of Markdown files Markdown Pipeline keeps current from your team's tools), one folder level at a time. Read-only. Returns {path, folders, files}: each folder with its "
                 "count of Markdown files, each Markdown file with a provenance summary, sorted by name (at the top level: the output folders, raw/, wiki/). "
                 "At the top level it may add not_searchable: targets with no local files. Errors (ToolError) for a folder that does not exist or is not readable. "
                 "Use list to find out what exists and where; use `search` to find content and `read` to open a file.")
    def list_tool(
        path_prefix: Annotated[str | None, Field(description="The folder to list, relative to the brain, e.g. 'raw/projects' or '@pipeline/raw'. Omit it for the top level.")] = None,
    ) -> dict:
        try:
            brain = provide()
        except NeedsSetup as exc:
            return setup_needed(exc)
        log = ActivityLog(brain)
        log.mcp_call("list")
        usage.note_agent_read()          # MD-116: a count per day, nothing about what was asked
        try:
            return list_path(brain, path_prefix)
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="sync_status", annotations=READ_ONLY, description="How fresh is the brain (the folder of Markdown files Markdown Pipeline keeps current from your team's tools)? Read-only: it never syncs and changes nothing. Per pipeline: status, last run "
                 "(time, result, counts), conflicts, big-change guard (it pauses a sync that would delete or overwrite a large share of a pipeline's files until the person approves) state and age since the last successful sync, plus the overall brain age. Every pipeline and the top level carry "
                 "last_success_at (ISO 8601, UTC). A pipeline name that does not exist gives an empty list of pipelines. When background sync is not running the reply has stale: true, "
                 "stale_reason and message_for_user: quote message_for_user to the person as is, because their files may be out of date. "
                 "Use sync_status to check freshness; use `recent_changes` to see which files changed, and `sync` only to refresh now.")
    def sync_status_tool(pipeline: PIPELINE_ARG = None) -> dict:
        try:
            brain = provide()
        except NeedsSetup as exc:
            return setup_needed(exc)
        log = ActivityLog(brain)
        log.mcp_call("sync_status")
        data = brain_status(brain, pipeline)
        for p in data["pipelines"]:
            if p["last_run"]:
                p["last_run"] = {k: v for k, v in p["last_run"].items() if k != "files"}
        return data

    @server.tool(name="recent_changes", annotations=READ_ONLY, description="What changed in the files of the brain (the folder of Markdown files Markdown Pipeline keeps current from your team's tools)? Read-only. The files created, changed or deleted by successful syncs "
                 "since a time (default: the last 24 hours), merged to one row per file, oldest change first, with counts and a text summary. At most `limit` rows are in items; "
                 "truncated: true says there were more (counts still cover all of them). A time that cannot be read, or a pipeline that does not exist, is an error (ToolError). "
                 "Use it for 'what changed since yesterday?'; use `sync_status` for how fresh the brain is, and `search` to find content.")
    def recent_changes_tool(
        since: Annotated[str | None, Field(description="Start of the window: an ISO date or time such as '2026-10-11' or '2026-10-11T08:00:00Z' (no time zone means UTC). Omit it for the last 24 hours.")] = None,
        pipeline: PIPELINE_ARG = None,
        limit: Annotated[int, Field(description="Most rows to return in items. Default 200. Rows past it are dropped and truncated is true.")] = 200,
    ) -> dict:
        try:
            brain = provide()
        except NeedsSetup as exc:
            return setup_needed(exc)
        log = ActivityLog(brain)
        log.mcp_call("recent_changes")
        usage.note_agent_read()          # MD-116: a count per day, nothing about what was asked
        if pipeline is not None and load_config(brain).get(pipeline) is None:
            raise ToolError(f"No pipeline named '{pipeline}'.")
        try:
            return changes.recent(brain, since=since, pipeline=pipeline, limit=limit)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="sync", annotations=SYNC_EFFECTS, description="Refresh the brain (the folder of Markdown files Markdown Pipeline keeps current from your team's tools) now: sync one pipeline (or all pipelines when none is given) from its sources (such as Jira or a local folder) into its "
                 "output folder. This writes files inside the brain (new and changed Markdown; files that a source deleted are deleted too) and the activity log. "
                 "Returns {results: [...]}, one per pipeline, with the counts of what changed. If the "
                 "big-change guard (it pauses a sync that would delete or overwrite a large share of a pipeline's files until the person approves) blocks the sync, nothing is written and the result is 'blocked': "
                 "only the person can approve it, in the app or with the `mdpipeline` command. This tool cannot approve or force. A pipeline that does not exist is an error (ToolError). "
                 "Use sync only when the person asked you to refresh or the files are stale; to just check freshness use `sync_status`.")
    def sync_tool(pipeline: PIPELINE_ARG = None) -> dict:
        try:
            brain = provide()
        except NeedsSetup as exc:
            return setup_needed(exc)
        log = ActivityLog(brain)
        log.mcp_call("sync")
        if pipeline is not None and load_config(brain).get(pipeline) is None:
            raise ToolError(f"No pipeline named '{pipeline}'.")
        results = [sync_pipeline(brain, pipeline, trigger="mcp")] if pipeline else sync_all(brain, trigger="mcp")
        out = []
        for res in results:
            d = res.to_dict()
            if res.result == "blocked":
                d["message"] = "approval needed in UI or CLI"
            out.append(d)
        return {"results": out}

    return server


def serve(brain: "Brain | Callable[[], Brain]") -> None:
    """Run the server over stdio until the client disconnects."""
    build_server(brain).run("stdio")
