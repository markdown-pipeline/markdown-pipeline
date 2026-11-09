"""MD-215 diagnostics: a text that says where the files of a failed first-run write test actually are.

The page says "7 files in <folder>"; the test then looks in one folder. When they disagree the failure alone cannot say whether the files went to the
default folder, to a subfolder, or were written a moment after the check. ``report`` lists what is on disk and the last lines of the brain's log, so the
server's view sits next to the page's. Read-only; never raises."""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_LINES = 200
FRESH_SECONDS = 60
LOG_TAIL = 5


def _tree(root: Path, now: float, label: str) -> list[str]:
    if not root.exists():
        return [f"{label}: {root} does not exist"]
    out, files = [f"{label}: {root}"], sorted(p for p in root.rglob("*") if p.is_file())
    for p in files[:MAX_LINES]:
        age = now - p.stat().st_mtime
        out.append(f"  {p.relative_to(root).as_posix()}  {p.stat().st_size} bytes  {int(age)} s ago{'  (fresh)' if age <= FRESH_SECONDS else ''}")
    if len(files) > MAX_LINES:
        out.append(f"  ... {len(files) - MAX_LINES} more not shown")
    if not files:
        out.append("  (no files)")
    return out


def report(tmp_path: Path, *, home: Path | None, brain, now: float | None = None) -> str:
    """Everything under the test folder, the default folder under the test HOME, and the tail of the brain's log."""
    now = time.time() if now is None else now
    lines: list[str] = []
    try:
        lines += _tree(Path(tmp_path), now, "test folder")
        if home is not None:
            lines += _tree(Path(home) / "markdown-pipeline", now, "default folder under the test HOME")
        if brain is not None:
            log = Path(brain.config_dir) / "log.jsonl"
            lines.append(f"log.jsonl: {log}")
            tail = log.read_text(encoding="utf-8").splitlines()[-LOG_TAIL:] if log.exists() else []
            lines += [f"  {json.dumps(json.loads(t), sort_keys=True)}" if t.strip().startswith("{") else f"  {t}" for t in tail] or ["  (empty or missing)"]
    except Exception as exc:                      # noqa: BLE001 - a diagnostic must never hide the real failure
        lines.append(f"(the report itself failed: {exc!r})")
    return "\n".join(lines)
