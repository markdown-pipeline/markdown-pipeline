"""MD-223: on Windows the first-run folder and every path shown are normal Windows paths (no ``~``, no mix of ``\\`` and ``/``). The Windows rules are exercised on every platform (``winpaths.is_windows`` is
switched on, and ``ntpath`` / ``PureWindowsPath`` do the work), so Linux CI proves them too; the real thing runs on mdp-win-1."""
from __future__ import annotations

import os

import pytest

import winsupport
from mdpipeline import config, firstrun, pipelinesummary, winpaths


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(winpaths, "is_windows", lambda: True)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\me")
    monkeypatch.delenv("HOME", raising=False)


# --- MD-223 AC1/AC2: the default folder is a full Windows path ---------------------------------------------------------
def test_the_default_folder_on_windows_is_the_full_path_under_the_home_folder(windows):
    assert firstrun.default_folder("Acme Jira!") == r"C:\Users\me\markdown-pipeline\acme-jira"


def test_the_default_folder_on_linux_is_still_the_tilde_path(monkeypatch):
    monkeypatch.setattr(winpaths, "is_windows", lambda: False)
    assert firstrun.default_folder("Acme Jira!") == "~/markdown-pipeline/acme-jira"


def test_a_first_run_draft_on_windows_saves_the_default_folder_with_backslashes_only(windows, tmp_path):
    d = firstrun.build_draft({"source": "folder", "path": str(tmp_path)})
    folder = d["destination"]["path"]
    assert folder == r"C:\Users\me\markdown-pipeline" + "\\" + firstrun._slug(tmp_path.name)
    assert "/" not in folder


def test_dest_check_on_windows_suggests_the_full_path_never_a_tilde(windows, tmp_path):
    res = firstrun.dest_check({"source": "folder", "path": str(tmp_path)})
    assert res["suggested"].startswith("C:\\Users\\me\\markdown-pipeline\\") and "~" not in res["suggested"] and "/" not in res["suggested"]
    assert res["path"] == res["suggested"]


# --- MD-223 AC4: a typed path is normalised before it is saved or compared -----------------------------------------------
@pytest.mark.parametrize("typed,expected", [
    ("c:/Users/me/out/", r"C:\Users\me\out"),
    (r"c:\Users\me\out\\", r"C:\Users\me\out"),
    ("~/out", r"C:\Users\me\out"),
    (r"C:\notes/sub", r"C:\notes\sub"),
])
def test_a_typed_destination_on_windows_is_one_normal_string(windows, tmp_path, typed, expected):
    assert firstrun.build_draft({"source": "folder", "path": str(tmp_path), "dest": typed})["destination"]["path"] == expected
    assert firstrun.dest_check({"source": "folder", "path": str(tmp_path), "dest": typed})["path"] == expected


def test_the_same_folder_typed_two_ways_is_stored_as_one_string_on_windows(windows):
    assert config.normalise_path("c:/Work/Notes/") == config.normalise_path(r"C:\Work\Notes") == r"C:\Work\Notes"


def test_a_second_drive_is_normalised_without_touching_the_disk(windows):
    # a pure string rule: a real D: can be an empty DVD drive on a Windows host, so nothing here may probe it
    assert config.normalise_path("d:/notes/sub/") == r"D:\notes\sub"


def test_linux_typed_paths_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(winpaths, "is_windows", lambda: False)
    winsupport.set_home(monkeypatch, tmp_path)
    assert firstrun.build_draft({"source": "folder", "path": str(tmp_path), "dest": "~/out"})["destination"]["path"] == os.path.expanduser("~/out")


# --- MD-223 AC5: the summary shows the full path on Windows --------------------------------------------------------------
def test_the_summary_does_not_shorten_the_home_folder_on_windows(windows):
    assert pipelinesummary._tilde(r"C:\Users\me\biz\notes") == r"C:\Users\me\biz\notes"
