"""MD-124: the sample through the real first-run API: review, write, suggested question, a later sync, one-click removal."""
import socket
import time

import pytest

from mdpipeline import agents, sample
from mdpipeline.config import load_config
from uihelper import running_ui


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real = socket.create_connection

    def only_loopback(address, *a, **k):          # the test client talks to the local UI server; nothing else may connect
        if address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"the sample must not use the network: {address}")
        return real(address, *a, **k)

    monkeypatch.setattr(socket, "create_connection", only_loopback)


def _write(ui, dest):
    return ui.post("/api/first-run/write", {"source": "sample", "dest": str(dest)})


def test_review_lists_the_sample_files_and_says_it_is_sample_data(brain, tmp_path):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/review", {"source": "sample", "dest": str(tmp_path / "out")})
    assert res["ok"] is True and res["count"] >= 14 and res["noun"] == "files"
    assert res["pipeline"] == "sample" and res["sample"] is True
    assert "sample" in res["read_only"].lower() and "no sign-in" in res["read_only"].lower()
    assert not (tmp_path / "out").exists()


def test_write_syncs_the_sample_quickly_with_no_credentials(brain, tmp_path):
    dest = tmp_path / "markdown-pipeline" / "sample"
    began = time.monotonic()
    with running_ui(brain) as ui:
        status, res = _write(ui, dest)
    assert status == 200 and res["ok"] is True and res["pipeline"] == "sample"
    assert time.monotonic() - began < 30
    assert res["files"] == len(list(dest.rglob("*.md"))) >= 14


def test_every_written_file_says_sample_in_its_provenance_and_front_matter(brain, tmp_path):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        _write(ui, dest)
    for f in dest.rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        assert "source_label: Sample" in text, f
        assert "sample: true" in text, f


def test_the_sample_pipeline_does_not_sync_by_itself(brain, tmp_path):
    with running_ui(brain) as ui:
        _, res = _write(ui, tmp_path / "out")
    assert res["schedule"] == "off" and res["next_sync"] is None
    assert load_config(brain).get("sample").schedule == "off"


def test_state_marks_the_sample_row_so_every_screen_can_label_it(brain, tmp_path):
    with running_ui(brain) as ui:
        _write(ui, tmp_path / "out")
        _, state = ui.get("/api/state")
    assert [p["sample"] for p in state["pipelines"]] == [True]


def test_a_real_pipeline_is_not_marked_as_sample(brain, make_source, tmp_path):
    src = make_source("notes", {"a.md": "# A\n"})
    with running_ui(brain) as ui:
        ui.post("/api/first-run/write", {"source": "folder", "path": str(src), "dest": str(tmp_path / "o")})
        _, state = ui.get("/api/state")
    assert [p["sample"] for p in state["pipelines"]] == [False]


def test_the_suggested_question_is_about_the_sample_and_not_a_fallback(brain, tmp_path):
    with running_ui(brain) as ui:
        _write(ui, tmp_path / "out")
    q = agents.suggest_question(brain, "sample")
    assert q["fallback"] is False and "ACME" in q["question"]


def test_trying_the_sample_twice_is_refused_with_a_reason(brain, tmp_path):
    with running_ui(brain) as ui:
        _write(ui, tmp_path / "a")
        _, again = _write(ui, tmp_path / "b")
    assert again["ok"] is False and "already" in again["errors"][0]["message"].lower()
    assert [p.name for p in load_config(brain).pipelines] == ["sample"]


def landed(dest, source_rel):
    """The file written for a source path, whatever file-name policy named it (new pipelines use date-name, MD-148)."""
    matches = [p for p in dest.rglob("*.md") if source_rel in p.read_text(encoding="utf-8").split("---", 2)[1]]
    return matches[0] if matches else None


def test_a_later_sync_edits_one_issue_and_deletes_one(brain, tmp_path):
    dest = tmp_path / "out" / "raw"  # MD-140: the sample lands under raw/ in the chosen folder
    with running_ui(brain) as ui:
        _write(ui, tmp_path / "out")
        before = landed(dest, sample.LATER["edit"]).read_text(encoding="utf-8")
        status, res = ui.post("/api/sample/later", {})
    assert status == 200
    counts = res["counts"]
    assert (counts["updated"], counts["deleted"]) == (1, 1)
    assert landed(dest, sample.LATER["edit"]).read_text(encoding="utf-8") != before
    assert "status: Done" in landed(dest, sample.LATER["edit"]).read_text(encoding="utf-8")
    assert landed(dest, sample.LATER["delete"]) is None


def test_a_later_sync_before_the_sample_exists_is_a_clear_error(brain):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/sample/later", {})
    assert status == 404


def test_removing_the_sample_deletes_its_files_pipeline_and_source_copy(brain, tmp_path, app_data):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        _write(ui, dest)
        status, res = ui.post("/api/sample/remove", {})
    assert status == 200 and res["removed"] is True
    assert load_config(brain).pipelines == []
    assert not list(dest.rglob("*.md"))
    assert not (app_data.dir / "sample-source").exists()


def test_removing_never_touches_a_real_pipeline_that_is_not_the_sample(brain, make_source, tmp_path):
    src = make_source("notes", {"a.md": "# A\n"})
    with running_ui(brain) as ui:
        ui.post("/api/first-run/write", {"source": "folder", "path": str(src), "dest": str(tmp_path / "o")})
        status, _ = ui.post("/api/sample/remove", {})
    assert status == 404
    assert [p.name for p in load_config(brain).pipelines] == ["notes"]
    assert list((tmp_path / "o").rglob("*a.md"))


def test_a_pipeline_called_sample_that_is_not_the_bundled_sample_is_not_removable_here(brain, make_source, tmp_path):
    src = make_source("sample", {"a.md": "# A\n"})
    with running_ui(brain) as ui:
        ui.post("/api/first-run/write", {"source": "folder", "path": str(src), "dest": str(tmp_path / "o")})
        status, _ = ui.post("/api/sample/remove", {})
    assert status == 404 and list((tmp_path / "o").rglob("*a.md"))


def test_review_shows_the_real_paths_inside_the_chosen_folder_under_raw(brain, tmp_path):
    """MD-133 AC2 / MD-140 AC3: the Review screen shows where each file will really land."""
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/review", {"source": "sample", "dest": str(tmp_path / "out")})
    assert res["ok"] and res["paths"] and len(res["paths"]) <= len(res["first"])
    assert all(p.endswith(".md") and p.startswith("raw/") for p in res["paths"])
    assert not (tmp_path / "out").exists()
