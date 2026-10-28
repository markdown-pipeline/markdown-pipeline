"""MD-124: try with sample data. A bundled, fictional, clearly labelled sample that needs no network and no credentials."""
import os
import re
import socket

import pytest
import yaml

from mdpipeline import firstrun, sample

REAL_NAMES = re.compile(r"atlassian\.net|@[a-z0-9-]+\.(com|io|dev|org)|github\.com/|slack\.com", re.I)


def _front(text: str) -> dict:
    assert text.startswith("---\n"), "every sample file starts with a front-matter block"
    return yaml.safe_load(text.split("---\n", 2)[1])


@pytest.fixture
def files(tmp_path):
    root = sample.source_dir()
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.md"))}


def test_source_dir_lives_in_the_app_data_folder_not_the_users(app_data):
    assert sample.source_dir().is_relative_to(app_data.dir)


def test_source_dir_is_rebuilt_to_the_bundled_state_every_time(files):
    root = sample.source_dir()
    victim = next(root.rglob("*.md"))
    victim.write_text("changed", encoding="utf-8")
    (root / "stray.md").write_text("x", encoding="utf-8")
    assert sample.source_dir() == root
    assert not (root / "stray.md").exists()
    assert victim.read_text(encoding="utf-8") != "changed"


def test_the_sample_has_a_believable_size_and_both_kinds_of_content(files):
    jira = [f for f in files if f.startswith("ACME/")]
    docs = [f for f in files if f.startswith("docs/")]
    assert 12 <= len(jira) <= 25
    assert 2 <= len(docs) <= 5


def test_every_jira_file_has_the_same_front_matter_the_real_renderer_writes(files):
    from mdpipeline.jira_render import render_issue  # noqa: F401  (the keys below mirror its output)

    allowed = {"key", "type", "status", "status_category", "priority", "labels", "components", "assignee", "reporter", "parent",
               "created", "updated", "resolved", "due", "fix_versions", "sprints"}
    for name, text in files.items():
        if name.startswith("ACME/"):
            meta = _front(text)["jira"]
            assert {"key", "type", "status", "status_category"} <= set(meta), name
            assert set(meta) <= allowed, name
            assert os.path.basename(name).startswith(meta["key"] + " "), name


def test_the_sample_reads_like_an_engineering_team(files):
    metas = [_front(t)["jira"] for n, t in files.items() if n.startswith("ACME/")]
    assert {"Epic", "Bug", "Story"} <= {m["type"] for m in metas}
    assert {"In Progress", "Done", "To Do"} <= {m["status_category"] for m in metas}
    assert any(m.get("sprints") for m in metas)
    assert any(m.get("parent") for m in metas)
    assert any("## Comments" in t for t in files.values())
    assert any("decision:" in t.lower() for t in files.values()), "a decision lives in the comments"


def test_something_is_in_progress_so_the_suggested_question_is_not_a_fallback(files):
    assert any(_front(t)["jira"]["status_category"] == "In Progress" for n, t in files.items() if n.startswith("ACME/"))


def test_it_is_labelled_sample_inside_every_file(files):
    for name, text in files.items():
        assert _front(text).get("sample") is True, name


def test_it_is_fictional_no_real_hosts_addresses_or_companies(files):
    for name, text in files.items():
        assert not REAL_NAMES.search(text), name


def test_people_appear_by_display_name_only(files):
    for name, text in files.items():
        assert "@" not in text.replace("\\@", ""), name


def test_the_sample_names_the_one_later_edit_and_the_one_later_deletion_that_exist():
    root = sample.source_dir()
    assert (root / sample.LATER["delete"]).is_file()
    assert (root / sample.LATER["edit"]).is_file()


def test_the_draft_is_labelled_sample_everywhere():
    d = firstrun.build_draft({"source": "sample"})
    assert d["name"] == "sample"
    assert d["sources"] == [{"path": str(sample.source_dir()), "label": "Sample"}]
    assert d["destination"]["path"] == os.path.expanduser("~/markdown-pipeline/sample")


def test_a_chosen_destination_still_applies_to_the_sample(tmp_path):
    assert firstrun.build_draft({"source": "sample", "dest": str(tmp_path / "x")})["destination"]["path"] == str(tmp_path / "x")


def test_building_the_draft_opens_no_network_connection(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the sample must not use the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    firstrun.build_draft({"source": "sample"})
