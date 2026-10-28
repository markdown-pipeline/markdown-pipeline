"""The local MCP server. Started on demand by the agent client over stdio (no daemon).

Tools: ``search``, ``read``, ``list`` (read-only retrieval; no LLM), (MD-122) ``recent_changes``, and (MD-23) ``sync_status``
and ``sync``. There is no tool that writes or edits brain content. Every call is logged by tool name
and time only (never the query or the path).
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import changes, usage
from .brain import Brain
from .brainquery import QueryError, list_path, read_file, search
from .config import load_config
from .engine import sync_all, sync_pipeline
from .log import ActivityLog
from .status import brain_status

INSTRUCTIONS = (
    "Search, read and list the Markdown knowledge in this brain (the files its pipelines wrote, plus raw/ and wiki/). Every result carries "
    "provenance (source pipeline, folder label and source path) so answers can cite their source. "
    "Retrieval only: you write the answer yourself."
)


def build_server(brain: Brain) -> MCPServer:
    server = MCPServer("mdpipeline", instructions=INSTRUCTIONS)
    log = ActivityLog(brain)

    @server.tool(name="search", description="Keyword/full-text search over the brain's Markdown (the files its pipelines wrote, plus raw/ and wiki/). "
                 "Returns ranked files with matching passages and each file's provenance. Covers every pipeline target that has local files: "
                 "files of other targets have paths like '@pipeline/raw/...' and a 'target' label. "
                 "Use quotes for an exact phrase. path_prefix (e.g. 'raw/projects/biz') narrows the search.")
    def search_tool(query: str, path_prefix: str | None = None, limit: int = 10) -> dict:
        log.mcp_call("search")
        usage.note_agent_read()          # MD-116: a count per day, nothing about what was asked
        try:
            return {"results": search(brain, query, path_prefix, limit)}
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="read", description="Read one Markdown file of the brain (path relative to the brain, "
                 "e.g. 'raw/projects/biz/pricing.md'). Returns the content and its parsed provenance.")
    def read_tool(path: str) -> dict:
        log.mcp_call("read")
        usage.note_agent_read()          # MD-116: a count per day, nothing about what was asked
        try:
            return read_file(brain, path)
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="list", description="List the folders and Markdown files under path_prefix "
                 "(default: the top level: the output folders, raw/ and wiki/), with a provenance summary per file.")
    def list_tool(path_prefix: str | None = None) -> dict:
        log.mcp_call("list")
        usage.note_agent_read()          # MD-116: a count per day, nothing about what was asked
        try:
            return list_path(brain, path_prefix)
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="sync_status", description="How fresh is the brain? Per pipeline: status, last run (time, result, counts), "
                 "conflicts, big-change guard state and age since the last successful sync, plus the overall brain age.")
    def sync_status_tool(pipeline: str | None = None) -> dict:
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


def serve(brain: Brain) -> None:
    """Run the server over stdio until the client disconnects."""
    build_server(brain).run("stdio")
