"""The local MCP server. Started on demand by the agent client over stdio (no daemon).

Tools: ``search``, ``read``, ``list`` (read-only retrieval; no LLM), (MD-122) ``recent_changes``, and (MD-23) ``sync_status``
and ``sync``. There is no tool that writes or edits brain content. Every call is logged by tool name
and time only (never the query or the path).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import changes, usage
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
    server = MCPServer("mdpipeline", instructions=INSTRUCTIONS)
    provide = brain if callable(brain) else (lambda: brain)
    announced = []

    def setup_needed(exc: NeedsSetup) -> dict:
        """No usable output folder: one message with one next step (counted once per server process, with consent, as a notice)."""
        if not announced:
            announced.append(True)
            usage.emit("notice_shown", kind="setup_mode")
        return {"status": "setup_needed", "message": str(exc)}


    @server.tool(name="search", description="Keyword/full-text search over the brain's Markdown (the files its pipelines wrote, plus raw/ and wiki/). "
                 "Returns ranked files with matching passages and each file's provenance. Covers every pipeline target that has local files: "
                 "files of other targets have paths like '@pipeline/raw/...' and a 'target' label. "
                 "Use quotes for an exact phrase. path_prefix (e.g. 'raw/projects/biz') narrows the search.")
    def search_tool(query: str, path_prefix: str | None = None, limit: int = 10) -> dict:
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

    @server.tool(name="read", description="Read one Markdown file of the brain (path relative to the brain, "
                 "e.g. 'raw/projects/biz/pricing.md'). Returns the content and its parsed provenance.")
    def read_tool(path: str) -> dict:
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

    @server.tool(name="list", description="List the folders and Markdown files under path_prefix "
                 "(default: the top level: the output folders, raw/ and wiki/), with a provenance summary per file.")
    def list_tool(path_prefix: str | None = None) -> dict:
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

    @server.tool(name="sync_status", description="How fresh is the brain? Per pipeline: status, last run (time, result, counts), "
                 "conflicts, big-change guard state and age since the last successful sync, plus the overall brain age.")
    def sync_status_tool(pipeline: str | None = None) -> dict:
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

    @server.tool(name="recent_changes", description="What changed in the brain's files? The files created, changed or deleted by successful syncs since a time "
                 "(ISO date or time, default: the last 24 hours), merged per file, with counts. Use it for 'what changed since yesterday?'. "
                 "pipeline narrows it to one pipeline.")
    def recent_changes_tool(since: str | None = None, pipeline: str | None = None, limit: int = 200) -> dict:
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

    @server.tool(name="sync", description="Refresh the brain: sync one pipeline (or all pipelines when none is given) from its "
                 "source folders into the output folder. If the big-change guard blocks the sync, nothing is written and the result is "
                 "'blocked': only the owner can approve it in the UI or CLI. This tool cannot approve or force.")
    def sync_tool(pipeline: str | None = None) -> dict:
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
