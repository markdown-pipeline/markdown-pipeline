"""MD-184 part B: build report.md for `windows_vm_gate.sh check` (owner decision DS-P-1102-2, comment 11290 on MD-184).

The owner reviews this file (about 5 minutes) instead of running the 30-minute manual Gate 2. So it has: one pass/fail line per checklist step, the wheel
version tested, the Windows build, one labelled screenshot per user-facing screen, and the steps that cannot be scripted, listed as 'not covered'.
Nothing in it is a step for the owner inside the run.

Usage: windows_gate_report.py OUT_DIR STAMP COMMIT WHEEL WHEEL_SHA ISO_SHA EXPIRY WINDOWS_BUILD   (reads OUT_DIR/results.jsonl; exit 0 only if all passed)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Checklist steps of comment 11024 (MD-184) that the machine can run, with the checks that decide each one.
STEPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Step 1", "install the candidate wheel, first sync", ("install", "first_sync")),
    ("Step 3", "background on starts one process and writes the login item", ("background_on_starts_process", "background_on_writes_login_item")),
    ("Step 4", "stop asked during a sync: it finishes, the process ends, the next sync is clean, no file lost",
     ("sync_was_running_when_stop_asked", "stop_ends_the_process", "stop_removes_login_item", "stop_lets_the_sync_finish", "next_sync_is_clean",
      "files_intact_after_stop")),
    ("Step 6", "after a reboot: the login item starts the process, the next sync and status are ok",
     ("reboot_login_item_starts_process", "reboot_login_item_still_there", "reboot_next_sync_ok", "reboot_status_ok")),
]

NOT_COVERED = [
    "Step 1: whether the first-run screens read well, and a pasted Windows path (a person judges screens; the screenshots below show them)",
    "Step 2: the notice Windows itself shows when a startup app is added",
    "Step 3: that no console window flashes or takes focus at sign-in (a screenshot cannot catch a flash)",
    "Step 5: that 'Open' on a file and on a folder opens the normal Windows app",
    "MD-183 (W5): `install.ps1` followed by `update` and uninstall (blocked by the `update` 'Access is denied' bug; not in the run yet)",
]


def wheel_version(wheel: str) -> str:
    """'mdpipeline-0.1.16-py3-none-any.whl' -> '0.1.16'."""
    return wheel.split("-")[1]


def build_report(rows: list[dict], shots: list[tuple[str, str, str | None]], *, stamp: str, wheel: str, wheel_sha: str, commit: str,
                 windows_build: str, iso_sha: str, expiry: str) -> tuple[str, bool]:
    """Return (markdown, all_passed). A step with a missing or failed check row fails; a missing row is never a silent pass."""
    by_check = {r["check"]: r for r in rows}
    step_lines: list[str] = []
    failures: list[str] = []
    for step, title, checks in STEPS:
        bad = [f"{c}: {by_check[c]['detail']}" if c in by_check else f"{c}: no result" for c in checks if c not in by_check or by_check[c]["status"] != "PASS"]
        step_lines.append(f"- {'FAIL' if bad else 'PASS'} {step}: {title}" + (f" ({'; '.join(bad)})" if bad else f" ({len(checks)} of {len(checks)} checks)"))
        failures += bad
    ok = not failures
    lines = [f"# Windows Gate 2 report, {stamp}", "",
             f"- Result: **{'PASS' if ok else 'FAIL'}**",
             f"- Wheel tested: `{wheel}` (version **{wheel_version(wheel)}**, sha256 `{wheel_sha}`), tree commit `{commit}`",
             f"- Windows build: {windows_build}",
             f"- Windows image: Enterprise evaluation ISO sha256 `{iso_sha}`; {expiry}", "",
             "Owner review (DS-P-1102-2): read this page and the screenshots, about 5 minutes. Nothing in the run itself needs a person.", "",
             "## Checklist steps", *step_lines, "", "## Screenshots (what a user sees)"]
    for _key, label, name in shots:
        lines += ["", f"### {label}", f"![{label}]({name})" if name else f"{label}: screenshot not captured"]
    lines += ["", "## Every check", "", "| phase | check | result | seconds | detail |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['phase']} | {r['check']} | {r['status']} | {r['seconds']} | {str(r['detail']).strip().replace('|', '/')} |")
    lines += ["", "## Not covered (cannot be scripted)", *[f"- {n}" for n in NOT_COVERED]]
    return "\n".join(lines) + "\n", ok


def main(argv: list[str]) -> int:
    out, stamp, commit, wheel, wheel_sha, iso_sha, expiry, windows_build = argv[1:9]
    rows = []
    for line in Path(out, "results.jsonl").read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip().lstrip("﻿")
        if line.startswith("{"):
            rows.append(json.loads(line))
    shots = [(k, label, name if Path(out, name).exists() else None) for k, label, name in
             (("install", "Install output", "shot-install.png"), ("first_sync", "First sync", "shot-first-sync.png"),
              ("status_after_reboot", "Status after reboot", "shot-status-after-reboot.png"))]
    text, ok = build_report(rows, shots, stamp=stamp, wheel=wheel, wheel_sha=wheel_sha, commit=commit, windows_build=windows_build, iso_sha=iso_sha, expiry=expiry)
    Path(out, "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
