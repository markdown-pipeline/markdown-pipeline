"""MD-184 part B, owner decision DS-P-1102-2 (comment 11290): the report of `windows_vm_gate.sh check` is what the owner reviews in about 5 minutes.
It must have one pass/fail line per checklist step, the wheel version tested, the Windows build, one labelled screenshot per user-facing screen,
and must list what cannot be scripted as 'not covered'. It must not ask the owner to do anything inside the run."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("windows_gate_report", APP / "scripts" / "windows_gate_report.py")
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def row(check: str, status: str = "PASS", phase: str = "before", detail: str = "ok") -> dict:
    return {"phase": phase, "check": check, "status": status, "seconds": 1.0, "detail": detail}


ALL_CHECKS = [row(c) for c in (
    "install", "first_sync", "background_on_starts_process", "background_on_writes_login_item", "sync_was_running_when_stop_asked",
    "stop_ends_the_process", "stop_removes_login_item", "stop_lets_the_sync_finish", "next_sync_is_clean", "files_intact_after_stop",
    "update_helper_installs_while_running", "update_leaves_a_working_install", "update_restarts_background_sync")] + [
    row(c, phase="after") for c in ("reboot_login_item_starts_process", "reboot_login_item_still_there", "reboot_next_sync_ok", "reboot_status_ok")]

META = dict(stamp="20261102-100000", wheel="mdpipeline-0.1.16-py3-none-any.whl", wheel_sha="ab" * 32, commit="abc1234",
            windows_build="Microsoft Windows [Version 10.0.26300.9457]", iso_sha="cd" * 32, expiry="Windows evaluation expires 2027-01-28 (87 days left)")
SHOTS = [("install", "Install output", "shot-install.png"), ("first_sync", "First sync", "shot-first-sync.png"),
         ("status_after_reboot", "Status after reboot", "shot-status-after-reboot.png")]


def build(rows=None, shots=SHOTS, **meta):
    return report.build_report(ALL_CHECKS if rows is None else rows, shots, **{**META, **meta})


def test_the_report_names_the_wheel_version_tested():
    text, _ = build()
    assert "0.1.16" in text and "mdpipeline-0.1.16-py3-none-any.whl" in text


def test_the_report_names_the_windows_build():
    text, _ = build()
    assert "10.0.26300.9457" in text


def test_there_is_one_pass_line_per_checklist_step():
    text, _ = build()
    for step in ("Step 1", "Step 3", "Step 4", "Step 6", "Step 7"):
        assert [l for l in text.splitlines() if l.startswith(f"- PASS {step}")], step


def test_a_failed_check_makes_its_step_line_fail_and_the_result_fail():
    rows = [r if r["check"] != "next_sync_is_clean" else row("next_sync_is_clean", "FAIL", detail="errors 2") for r in ALL_CHECKS]
    text, ok = build(rows)
    assert not ok
    assert [l for l in text.splitlines() if l.startswith("- FAIL Step 4")]
    assert "errors 2" in text
    assert "Result: **FAIL**" in text


def test_a_step_with_no_result_row_is_a_failure_not_a_silent_pass():
    rows = [r for r in ALL_CHECKS if r["phase"] != "after"]
    text, ok = build(rows)
    assert not ok
    assert [l for l in text.splitlines() if l.startswith("- FAIL Step 6")]


def test_all_checks_passing_gives_pass_and_ok():
    text, ok = build()
    assert ok and "Result: **PASS**" in text


def test_each_screenshot_is_embedded_with_its_label():
    text, _ = build()
    for label, name in (("Install output", "shot-install.png"), ("First sync", "shot-first-sync.png"), ("Status after reboot", "shot-status-after-reboot.png")):
        assert f"![{label}]({name})" in text


def test_a_missing_screenshot_is_stated_not_hidden():
    text, _ = build(shots=[("install", "Install output", None)] + SHOTS[1:])
    assert "Install output: screenshot not captured" in text


def test_what_cannot_be_scripted_is_listed_as_not_covered():
    text, _ = build()
    section = text.split("## Not covered", 1)[1]
    for needle in ("first-run", "notice", "flash", "Open"):
        assert needle in section


def test_the_report_asks_the_owner_for_no_step_inside_the_run():
    text, _ = build()
    assert "- [ ]" not in text
    assert "Owner review" in text      # the review of the report afterwards (DS-P-1102-2) is named, but it is not a step of the run


def test_the_version_comes_from_the_wheel_file_name():
    assert report.wheel_version("mdpipeline-0.1.16-py3-none-any.whl") == "0.1.16"


# --- MD-183: `update` while the app is running (Step 7) ----------------------------------------------------------------------
def test_a_failed_update_check_fails_step_7_and_the_result():
    rows = [r if r["check"] != "update_leaves_a_working_install" else row("update_leaves_a_working_install", "FAIL", detail="No module named mdpipeline") for r in ALL_CHECKS]
    text, ok = build(rows)
    assert not ok and [l for l in text.splitlines() if l.startswith("- FAIL Step 7")] and "No module named mdpipeline" in text


def test_a_missing_update_row_is_a_failure_not_a_silent_pass():
    text, ok = build([r for r in ALL_CHECKS if not r["check"].startswith("update_")])
    assert not ok and [l for l in text.splitlines() if l.startswith("- FAIL Step 7")]


def test_the_report_says_what_the_update_check_does_not_run():
    text, _ = build()
    assert "public `install.ps1`" in text
