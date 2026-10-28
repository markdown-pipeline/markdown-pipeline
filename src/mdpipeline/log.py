"""The local activity log (``<brain>/.mdpipeline/log.jsonl``): one JSON object per line.

It stays on the machine: no telemetry, no network. MCP calls are logged by tool name and time only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .brain import Brain


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ActivityLog:
    def __init__(self, brain: Brain) -> None:
        self.path = brain.config_dir / "log.jsonl"

    def append(self, event: dict) -> None:
        line = json.dumps({"ts": now_iso(), **event}, sort_keys=False, default=str) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def event(self, action: str, pipeline: str | None, trigger: str, **details) -> None:
        """Log a pipeline event: pause, resume, approve, cancel, force, remove ..."""
        self.append({"type": "event", "action": action, "pipeline": pipeline, "trigger": trigger, **details})

    def mcp_call(self, tool: str) -> None:
        """Log an MCP tool call (name and time only, never the query)."""
        self.append({"type": "mcp_call", "tool": tool})

    def read(self, pipeline: str | None = None, limit: int | None = None, types: set[str] | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        events = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if pipeline is not None and event.get("pipeline") != pipeline:
                    continue
                if types is not None and event.get("type") not in types:
                    continue
                events.append(event)
        return events[-limit:] if limit else events
