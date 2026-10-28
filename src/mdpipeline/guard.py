"""The big-change guard: stop a sync that would delete or overwrite too much of a pipeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardState:
    blocked: bool
    count: int          # planned deletions + overwrites of existing owned files
    total: int          # files the pipeline currently owns (incl. those of missing folders)
    percent: float
    threshold: float
    min_files: int
    message: str = ""

    def to_dict(self) -> dict:
        return {"blocked": self.blocked, "count": self.count, "total": self.total,
                "percent": round(self.percent, 1), "threshold": self.threshold, "min_files": self.min_files,
                "message": self.message}


def evaluate_guard(count: int, total: int, threshold: float, min_files: int) -> GuardState:
    """Block iff count/total > threshold% AND count >= min_files (both must hold)."""
    percent = (count / total * 100.0) if total else 0.0
    blocked = total > 0 and count >= min_files and count > 0 and percent > threshold
    if blocked:
        message = (f"This sync would delete or overwrite {count} of {total} owned files ({percent:.0f}%), "
                   f"above the {threshold:g}% threshold (minimum {min_files} files). "
                   "Approve in the UI or with --approve.")
    else:
        message = f"Guard OK: {count} of {total} owned files would be deleted or overwritten ({percent:.0f}%)."
    return GuardState(blocked, count, total, percent, threshold, min_files, message)
