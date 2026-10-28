"""Regenerate docs/usage-events.md from the allowlist (MD-116 AC5):  uv run python scripts/gen_usage_events_doc.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdpipeline.usage_docs import render_doc  # noqa: E402

target = Path(__file__).resolve().parent.parent / "docs" / "usage-events.md"
target.write_text(render_doc(), encoding="utf-8")
print(f"wrote {target}")
