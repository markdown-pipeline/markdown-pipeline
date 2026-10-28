"""MD-56: the guard ignores metadata-only rewrites (front-matter/provenance change, same body) and the plan reports them separately."""
import json

import pytest

from conftest import write
from mdpipeline.cli import main
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline


def doc(title, body):
    return f"---\ntitle: {title}\n---\n{body}\n"


@pytest.fixture
def ten(brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": doc(f"t{i}", f"body {i}") for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="original", guard_threshold_percent=40, guard_min_files=5))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    return brain, src


def front_only(src, indexes):
    for i in indexes:
        write(src / f"n{i}.md", doc(f"retitled {i}", f"body {i}"))


def body_change(src, indexes):
    for i in indexes:
        write(src / f"n{i}.md", doc(f"t{i}", f"body {i} CHANGED"))


def test_metadata_only_updates_of_every_file_do_not_trip_the_guard(ten):
    brain, src = ten
    front_only(src, range(10))
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["updated"] == 10 and res.plan.guard.count == 0 and not res.plan.guard.blocked
    assert (brain.root / "raw/p/docs/n3.md").read_text().count("retitled 3") == 1


def test_the_plan_reports_metadata_only_updates_separately(ten):
    brain, src = ten
    front_only(src, range(4))
    body_change(src, [8])
    dry = sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    assert dry.plan.metadata_only == 4 and dry.counts()["updated"] == 5
    d = dry.to_dict()
    assert d["plan"]["metadata_only"] == 4 and d["plan"]["guard"]["count"] == 1
    kinds = {i["target"]: i.get("metadata_only", False) for g in d["plan"]["groups"] for i in g["items"]}
    assert kinds["raw/p/docs/n0.md"] is True and kinds["raw/p/docs/n8.md"] is False


def test_content_updates_still_count(ten):
    brain, src = ten
    body_change(src, range(5))
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "blocked" and res.plan.guard.count == 5


def test_a_mix_counts_only_the_content_updates(ten):
    brain, src = ten
    front_only(src, range(0, 6))
    body_change(src, [6, 7, 8])
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.plan.guard.count == 3 and res.plan.metadata_only == 6


def test_a_front_matter_and_body_change_is_a_content_update(ten):
    brain, src = ten
    for i in range(5):
        write(src / f"n{i}.md", doc(f"retitled {i}", f"body {i} AND MORE"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "blocked"


def test_deletions_and_renames_and_forced_overwrites_still_count(ten):
    brain, src = ten
    for i in range(5):
        (src / f"n{i}.md").unlink()
    assert sync_pipeline(brain, "p", trigger="cli").result == "blocked"


def test_forced_overwrites_of_brain_edits_are_not_metadata_only(ten):
    brain, src = ten
    path = brain.root / "raw/p/docs/n0.md"
    path.write_text(path.read_text() + "my edit\n")
    front_only(src, [0])
    res = sync_pipeline(brain, "p", trigger="cli", force=True)
    assert res.counts()["updated"] == 1 and res.plan.metadata_only == 0 and res.plan.guard.count == 1


def test_a_brain_edited_file_is_still_a_conflict_and_not_a_metadata_update(ten):
    brain, src = ten
    path = brain.root / "raw/p/docs/n0.md"
    path.write_text(path.read_text() + "my edit\n")
    front_only(src, [0])
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "conflicts" and res.plan.metadata_only == 0


def test_the_cli_says_how_many_updates_are_metadata_only_and_do_not_count(ten, capsys):
    brain, src = ten
    front_only(src, range(3))
    main(["--brain", str(brain.root), "sync", "p", "--dry-run"])
    out = capsys.readouterr().out
    assert "3 metadata-only" in out and "not counted" in out


def test_jira_assignee_change_is_a_metadata_only_update(brain, add_pipeline, monkeypatch):
    from fake_jira import FakeJira
    from mdpipeline import jira_api
    from test_jira_sync import EMAIL, TOKEN, jira_source

    f = FakeJira(site="acme", email=EMAIL, token=TOKEN)
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    try:
        for n in range(6):
            f.add_issue("MD", f"issue {n}", assignee="Dana")
        add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name", guard_threshold_percent=40, guard_min_files=2))
        sync_pipeline(brain, "p", trigger="cli")
        for n in range(1, 7):
            f.update_issue(f"MD-{n}", assignee="Dana Renamed")
        res = sync_pipeline(brain, "p", trigger="cli")
        assert res.result == "ok" and res.counts()["updated"] == 6 and res.plan.metadata_only == 6 and res.plan.guard.count == 0
    finally:
        jira_api.set_transport(None)
