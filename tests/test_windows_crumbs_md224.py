"""MD-224: the folder picker's breadcrumbs open the folder they name. The Windows rules are exercised on every platform with ``PureWindowsPath``;
the real thing runs on mdp-win-1 (tests/test_folder_browser.py::test_the_response_has_a_breadcrumb_and_a_way_up)."""
from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from mdpipeline import folderbrowser


def test_windows_crumbs_start_at_the_drive_and_every_path_is_absolute():
    crumbs = folderbrowser._crumbs(r"C:\Users\me", PureWindowsPath)
    assert [(c["name"], c["path"]) for c in crumbs] == [("C:\\", "C:\\"), ("Users", r"C:\Users"), ("me", r"C:\Users\me")]


def test_windows_crumbs_on_another_drive_stay_on_that_drive():
    crumbs = folderbrowser._crumbs(r"D:\data\x", PureWindowsPath)
    assert [c["path"] for c in crumbs] == ["D:\\", r"D:\data", r"D:\data\x"]


def test_a_drive_root_has_one_crumb():
    assert folderbrowser._crumbs("C:\\", PureWindowsPath) == [{"name": "C:\\", "path": "C:\\"}]


def test_linux_crumbs_are_unchanged():
    assert folderbrowser._crumbs("/a/b", PurePosixPath) == [{"name": "/", "path": "/"}, {"name": "a", "path": "/a"}, {"name": "b", "path": "/a/b"}]


def test_the_way_up_from_a_drive_root_stays_at_the_root_without_an_error():       # MD-224 AC4: no parent, the picker keeps showing the drive
    assert folderbrowser._parent("C:\\", PureWindowsPath) is None
    assert folderbrowser._parent(r"C:\Users", PureWindowsPath) == "C:\\"
    assert folderbrowser._parent("/", PurePosixPath) is None and folderbrowser._parent("/a", PurePosixPath) == "/"
