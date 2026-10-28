"""MD-142: a server-side folder browser. Directory names only, loopback + session token + Origin check, nothing outside what the OS user can read."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mdpipeline import folderbrowser
from mdpipeline.config import Pipeline, Source
from test_usage_hooks import queued, sharing  # noqa: F401  (fixture)
from uihelper import running_ui
import winsupport

CANARY_FILE = "secret-quarterly-numbers.md"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """home/ with Documents, Desktop, a hidden folder, a file, and projects/{alpha,Beta,gamma}."""
    home = tmp_path / "home"
    for d in ("Documents", "Desktop", ".config", "projects/alpha", "projects/Beta", "projects/gamma"):
        (home / d).mkdir(parents=True)
    (home / "projects" / CANARY_FILE).write_text("numbers")
    (home / "notes.txt").write_text("x")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(folderbrowser, "wsl_drives", lambda: [])
    return home


def browse(ui, path="", **kw):
    return ui.post("/api/folder-browse", {"path": path, **kw})


def names(res):
    return [d["name"] for d in res["dirs"]]


# --- listing -----------------------------------------------------------------------------------------------------------
def test_lists_folders_only_sorted_without_case_and_never_a_file_name(brain, tree):
    with running_ui(brain) as ui:
        status, res = browse(ui, str(tree / "projects"))
    assert status == 200 and res["ok"] is True and res["path"] == str(tree / "projects")
    assert names(res) == ["alpha", "Beta", "gamma"] and CANARY_FILE not in json.dumps(res)


def test_the_response_has_a_breadcrumb_and_a_way_up(brain, tree):
    with running_ui(brain) as ui:
        _, res = browse(ui, str(tree / "projects" / "alpha"))
    assert res["parent"] == str(tree / "projects")
    assert [c["name"] for c in res["crumbs"]][-2:] == ["projects", "alpha"] and res["crumbs"][-1]["path"] == str(tree / "projects" / "alpha")


def test_the_root_has_no_parent(brain, tree):
    with running_ui(brain) as ui:
        _, res = browse(ui, "/")
    assert res["ok"] is True and res["parent"] is None


def test_an_empty_path_or_an_unknown_one_starts_at_home_with_a_calm_note(brain, tree):
    with running_ui(brain) as ui:
        _, empty = browse(ui, "")
        _, gone = browse(ui, str(tree / "nowhere" / "deeper"))
        _, afile = browse(ui, str(tree / "notes.txt"))
        _, rel = browse(ui, "relative/path")
    assert empty["path"] == str(tree) and "note" not in empty
    for res in (gone, afile, rel):
        assert res["ok"] is True and res["path"] == str(tree) and res["note"]["kind"] in ("not_found", "not_a_folder", "not_absolute")
        assert res["note"]["message"] and res["note"]["next"]


def test_a_tilde_path_is_expanded(brain, tree):
    with running_ui(brain) as ui:
        _, res = browse(ui, "~/projects")
    assert res["path"] == str(tree / "projects")


def test_dot_dot_is_resolved_before_listing_and_never_returned(brain, tree):
    with running_ui(brain) as ui:
        _, res = browse(ui, str(tree / "projects" / "alpha" / ".." / "gamma" / ".." / ".."))
    assert res["path"] == str(tree) and ".." not in res["path"].split(os.sep)


def test_a_nul_byte_is_refused_not_passed_to_the_os(brain, tree):
    with running_ui(brain) as ui:
        status, res = browse(ui, str(tree) + "\x00/etc")
    assert status == 200 and res["path"] == str(tree) and res["note"]["kind"] == "not_found"


def test_hidden_folders_are_hidden_until_asked(brain, tree):
    with running_ui(brain) as ui:
        _, plain = browse(ui, str(tree))
        _, shown = browse(ui, str(tree), show_hidden=True)
    assert ".config" not in names(plain) and ".config" in names(shown)


def test_a_symlink_to_a_folder_is_listed_and_to_a_file_is_not(brain, tree):
    (tree / "projects" / "link-dir").symlink_to(tree / "Documents")
    (tree / "projects" / "link-file").symlink_to(tree / "notes.txt")
    with running_ui(brain) as ui:
        _, res = browse(ui, str(tree / "projects"))
    assert "link-dir" in names(res) and "link-file" not in names(res)


def test_symlink_loops_cannot_hang_the_listing(brain, tree):
    (tree / "loop-a").symlink_to(tree / "loop-b")
    (tree / "loop-b").symlink_to(tree / "loop-a")
    (tree / "projects" / "self").symlink_to(tree / "projects")
    with running_ui(brain) as ui:
        _, home = browse(ui, str(tree))
        _, projects = browse(ui, str(tree / "projects"))
    assert "loop-a" not in names(home) and "loop-b" not in names(home) and projects["ok"] is True


@pytest.mark.skipif(winsupport.is_root(), reason="root can open everything")
@pytest.mark.windows_unsupported("chmod makes a file or folder unreadable only on Unix; Windows ignores the permission bits")
def test_an_unreadable_folder_is_disabled_with_a_calm_reason_and_listing_it_says_so(brain, tree):
    locked = tree / "projects" / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with running_ui(brain) as ui:
            _, parent = browse(ui, str(tree / "projects"))
            _, inside = browse(ui, str(locked))
    finally:
        locked.chmod(0o755)
    entry = next(d for d in parent["dirs"] if d["name"] == "locked")
    assert entry["readable"] is False and entry["reason"]
    assert inside["ok"] is True and inside["note"]["kind"] == "permission" and inside["path"] == str(tree / "projects")


def test_a_huge_directory_is_capped_and_paged(brain, tree):
    big = tree / "big"
    big.mkdir()
    for i in range(450):
        (big / f"d{i:04d}").mkdir()
    with running_ui(brain) as ui:
        _, first = browse(ui, str(big))
        _, last = browse(ui, str(big), start=400)
    assert len(first["dirs"]) == 200 and first["is_last"] is False and first["total"] == 450 and first["next_start"] == 200
    assert len(last["dirs"]) == 50 and last["is_last"] is True


# --- shortcuts ---------------------------------------------------------------------------------------------------------
def test_shortcuts_are_home_documents_desktop_and_only_those_that_exist(brain, tree):
    (tree / "Desktop").rmdir()
    with running_ui(brain) as ui:
        _, res = browse(ui, "")
    assert [(s["id"], s["path"]) for s in res["shortcuts"]] == [("home", str(tree)), ("documents", str(tree / "Documents"))]


def test_recent_locations_are_the_folders_of_existing_pipelines_that_still_exist(brain, tree, add_pipeline):
    src = tree / "projects" / "alpha"
    add_pipeline(Pipeline.new("p", [Source(str(src), "alpha"), Source(str(tree / "gone"), "gone")],
                              destination={"type": "directory", "path": str(tree / "projects" / "gamma")}))
    with running_ui(brain) as ui:
        _, res = browse(ui, "")
    recent = [s["path"] for s in res["shortcuts"] if s["id"] == "recent"]
    assert recent == [str(src), str(tree / "projects" / "gamma")]


def test_wsl_windows_drives_appear_when_mounted(brain, tree, monkeypatch):
    monkeypatch.setattr(folderbrowser, "wsl_drives", lambda: ["/mnt/c", "/mnt/d"])
    with running_ui(brain) as ui:
        _, res = browse(ui, "")
    assert [s["path"] for s in res["shortcuts"] if s["id"] == "drive"] == ["/mnt/c", "/mnt/d"]


# --- New folder (output only) ------------------------------------------------------------------------------------------
def test_new_folder_creates_one_directory_and_returns_its_path(brain, tree):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/folder-browse/new", {"parent": str(tree / "projects"), "name": "markdown-out"})
    assert status == 200 and res["ok"] is True and res["path"] == str(tree / "projects" / "markdown-out")
    assert (tree / "projects" / "markdown-out").is_dir()


@pytest.mark.parametrize("bad", ["", "  ", ".", "..", "a/b", "a\\b", "x\x00y", "n" * 256, "../escape"])
def test_new_folder_refuses_names_that_are_not_one_plain_name(brain, tree, bad):
    before = sorted(p.name for p in (tree / "projects").iterdir())
    with running_ui(brain) as ui:
        status, res = ui.post("/api/folder-browse/new", {"parent": str(tree / "projects"), "name": bad})
    assert status == 400 and res["error"] and sorted(p.name for p in (tree / "projects").iterdir()) == before
    assert not (tree / "escape").exists()


def test_new_folder_never_overwrites(brain, tree):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/folder-browse/new", {"parent": str(tree / "projects"), "name": "alpha"})
    assert status == 409 and "already" in res["error"]


def test_new_folder_needs_an_existing_folder_as_parent(brain, tree):
    with running_ui(brain) as ui:
        s1, _ = ui.post("/api/folder-browse/new", {"parent": str(tree / "nope"), "name": "x"})
        s2, _ = ui.post("/api/folder-browse/new", {"parent": str(tree / "notes.txt"), "name": "x"})
    assert (s1, s2) == (400, 400)


# --- security and privacy ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/folder-browse", "/api/folder-browse/new"])
def test_a_request_without_the_session_token_is_refused(brain, tree, path):
    with running_ui(brain) as ui:
        status, _ = ui.post(path, {"path": str(tree), "parent": str(tree), "name": "x"}, token=False)
        wrong, _ = ui.post(path, {"path": str(tree), "parent": str(tree), "name": "x"}, token="not-the-token")
    assert (status, wrong) == (403, 403) and not (tree / "x").exists()


@pytest.mark.parametrize("origin", ["https://evil.example", "http://attacker.test:8080", "null"])
def test_a_foreign_origin_is_refused_even_with_the_token(brain, tree, origin):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/folder-browse", {"path": str(tree)}, headers={"Origin": origin})
    assert status == 403 and "dirs" not in res


def test_a_rebound_host_name_is_refused(brain, tree):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/folder-browse", {"path": str(tree)}, headers={"Host": "rebind.evil.example"})
    assert status == 403 and "dirs" not in res


def test_the_listing_is_not_a_get_so_it_cannot_be_triggered_by_a_page_embed(brain, tree):
    with running_ui(brain) as ui:
        status, _ = ui.get("/api/folder-browse?path=" + str(tree))
    assert status in (404, 405)


def test_browsing_reports_nothing_and_logs_no_path(brain, tree, sharing, capfd):  # noqa: F811
    with running_ui(brain) as ui:
        browse(ui, str(tree / "projects"))
        ui.post("/api/folder-browse/new", {"parent": str(tree), "name": "newfolder-canary"})
    out = capfd.readouterr()
    assert queued() == [] or all("folder" not in json.dumps(e["properties"]) for e in queued())
    assert str(tree) not in out.out + out.err


def test_the_folder_pick_event_has_one_fixed_property_and_drops_a_path():
    from mdpipeline.usage_events import EVENTS, sanitize

    assert set(EVENTS["folder_pick"].props) == {"method"}
    assert sanitize("folder_pick", {"method": "browse", "path": "/home/me/secret"}) == {"method": "browse"}
    assert sanitize("folder_pick", {"method": "/home/me/secret"}) == {}
    assert {"browse", "typed", "recent"} == set(EVENTS["folder_pick"].props["method"].values)


def test_the_client_can_report_a_pick_through_the_existing_event_route(brain, sharing):  # noqa: F811
    with running_ui(brain) as ui:
        ui.post("/api/usage/event", {"event": "folder_pick", "properties": {"method": "browse", "path": "/home/me/x"}})
    sent = queued("folder_pick")
    assert [e["properties"]["method"] for e in sent] == ["browse"] and "/home/me" not in json.dumps(sent)
