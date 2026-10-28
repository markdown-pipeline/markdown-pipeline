"""MD-55: the Jira data-loss suite (P3 release gate). Run alone with:

    uv run pytest tests/test_data_loss_jira.py

Scenario numbers match MD-S10 / the acceptance criteria of MD-55. Everything runs against the fake Jira (no real network). Together
with tests/test_data_loss.py (MD-30), tests/test_data_loss_names.py (MD-41) and tests/test_data_loss_github.py (MD-69) this is the gate.
"""
import json
import socket
import threading
import time

import pytest

from conftest import write
from fake_jira import FakeJira, adf_doc, adf_p
from mdpipeline import jira_api
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.jira_source import JiraSource
from mdpipeline.locking import brain_lock
from mdpipeline.rebuild import rebuild_preview
from test_jira_sync import EMAIL, SITE, TOKEN, day, jira_source, raw_files


class Kill(BaseException):
    """The process is killed (not caught by `except Exception`)."""


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.sleeps = []
    jira_api.set_transport(f, sleep=f.sleeps.append)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)
    JiraSource.page_size = 100


@pytest.fixture
def world(brain, add_pipeline, fake, brain_dir):
    write(brain_dir / "wiki/index.md", "curated")
    write(brain_dir / "raw/handwritten/note.md", "hand written")
    for n in range(6):
        fake.add_issue("MD", f"issue {n}", comments=[f"comment {n}"], created=day(2026, 10, 1 + n))
    add_pipeline(Pipeline("p", [jira_source("project = MD")], name_policy="date-name", guard_threshold_percent=40, guard_min_files=3))
    return type("W", (), {"brain": brain, "fake": fake, "sync": staticmethod(lambda **kw: sync_pipeline(brain, "p", trigger="cli", **kw)),
                          "root": brain_dir})


def jira_files(w):
    return [f for f in raw_files(w.brain) if "/jira/" in f]


def curated_intact(w):
    assert (w.root / "wiki/index.md").read_text() == "curated" and (w.root / "raw/handwritten/note.md").read_text() == "hand written"


# 1
def test_01_api_outage_mid_backfill_deletes_nothing_and_the_next_run_converges(world):
    assert world.sync().result == "ok"
    for n in range(6, 10):
        world.fake.add_issue("MD", f"new issue {n}")
    world.fake.fail("GET", "/issue/", status=503, times=3)
    res = world.sync()
    assert res.result == "error" and res.counts()["deleted"] == 0 and len(jira_files(world)) >= 6
    again = world.sync()
    assert again.result == "ok" and len(jira_files(world)) == 10 and again.counts()["conflicts"] == 0
    curated_intact(world)


# 2
def test_02_auth_failure_after_a_successful_sync_deletes_nothing_and_needs_you_shows_it(world, monkeypatch):
    from uihelper import running_ui

    world.sync()
    before = jira_files(world)
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    res = world.sync()
    assert res.result == "error" and res.counts()["deleted"] == 0 and jira_files(world) == before
    with running_ui(world.brain) as ui:
        card = next(c for c in ui.get("/api/activity")[1]["needs_you"] if c["kind"] == "error")
        assert card["connectors"][0]["category"] == "auth" and "JIRA_TOKEN" in card["connectors"][0]["next_step"]


# 3
def test_03_one_page_failing_out_of_n_deletes_nothing(world):
    world.sync()
    before = jira_files(world)
    JiraSource.page_size = 2
    world.fake.fail_page(2)
    res = world.sync()
    assert res.result == "error" and res.counts()["deleted"] == 0 and jira_files(world) == before


# 4
def test_04_a_jql_that_now_returns_zero_always_asks_the_guard(world):
    world.sync()
    cfg = load_config(world.brain)
    cfg.get("p").guard_threshold_percent, cfg.get("p").guard_min_files = 100, 50
    cfg.get("p").sources[0] = jira_source("project = NOWHERE")
    save_config(world.brain, cfg)
    before = jira_files(world)
    res = world.sync()
    assert res.result == "blocked" and "returned 0" in res.plan.guard.message and jira_files(world) == before
    assert world.sync(approve=True).counts()["deleted"] == 6


# 5
@pytest.mark.parametrize("policy,deleted", [("delete", 1), ("keep", 0)])
def test_05_a_deleted_issue_follows_the_on_delete_policy(world, policy, deleted):
    cfg = load_config(world.brain)
    cfg.get("p").on_source_delete, cfg.get("p").guard_threshold_percent = policy, 100
    save_config(world.brain, cfg)
    world.sync()
    world.fake.delete_issue("MD-3")
    res = world.sync()
    assert res.counts()["deleted"] == deleted and len(jira_files(world)) == 6 - deleted


# 6
@pytest.mark.parametrize("policy,deleted", [("delete", 1), ("keep", 0)])
def test_06_an_issue_moved_out_of_scope_follows_the_on_delete_policy(world, policy, deleted):
    cfg = load_config(world.brain)
    cfg.get("p").on_source_delete, cfg.get("p").guard_threshold_percent = policy, 100
    save_config(world.brain, cfg)
    world.sync()
    world.fake.move_issue("MD-2", "ELSEWHERE")
    res = world.sync()
    assert res.counts()["deleted"] == deleted and len(jira_files(world)) == 6 - deleted


# 7
def test_07_a_key_change_by_a_project_move_keeps_the_same_file_with_no_delete_and_create(world):
    cfg = load_config(world.brain)
    cfg.get("p").sources[0] = jira_source("project in (MD, MP)")
    cfg.get("p").target = "raw/{source}/{relpath}"
    cfg.get("p").guard_threshold_percent = 100
    save_config(world.brain, cfg)
    world.fake.add_issue("MP", "seed")
    world.sync()
    owner_before = {json.loads((world.brain.config_dir / "state.json").read_text())["pipelines"]["p"]["files"][f]["source_path"] for f in jira_files(world)}
    new_key = world.fake.move_issue("MD-2", "MP")
    res = world.sync()
    c = res.counts()
    assert c["created"] == 0 and c["deleted"] == 0 and c["renamed"] == 1 and len(jira_files(world)) == 7
    owner_after = {json.loads((world.brain.config_dir / "state.json").read_text())["pipelines"]["p"]["files"][f]["source_path"] for f in jira_files(world)}
    assert owner_before == owner_after


# 8
def test_08_a_summary_change_does_not_rename(world):
    world.sync()
    before = jira_files(world)
    world.fake.update_issue("MD-1", summary="A new title entirely")
    res = world.sync()
    assert res.counts()["renamed"] == 0 and res.counts()["updated"] == 1 and jira_files(world) == before


# 9
@pytest.mark.parametrize("change", ["edit", "delete"])
def test_09_a_comment_edited_or_deleted_updates_the_file(world, change):
    world.sync()
    cid = world.fake._find("MD-1")["comments"][0]["id"]
    (world.fake.edit_comment("MD-1", cid, "rewritten comment") if change == "edit" else world.fake.delete_comment("MD-1", cid))
    res = world.sync()
    assert res.counts()["updated"] == 1
    text = (world.brain.root / next(f for f in jira_files(world) if "md-1-" in f)).read_text()
    assert ("rewritten comment" in text) if change == "edit" else ("comment 0" not in text)


# 10
def test_10_a_brain_edited_jira_file_with_an_updated_issue_is_a_conflict_never_overwritten(world):
    world.sync()
    path = world.brain.root / next(f for f in jira_files(world) if "md-1-" in f)
    path.write_text(path.read_text() + "\nmy notes\n")
    world.fake.add_comment("MD-1", "newer comment")
    res = world.sync()
    assert res.result == "conflicts" and "my notes" in path.read_text() and "newer comment" not in path.read_text()


# 11
def test_11_a_hand_written_file_at_a_jira_target_path_is_never_claimed(brain, add_pipeline, fake, brain_dir):
    fake.add_issue("MD", "One", created=day(2026, 10, 1))
    add_pipeline(Pipeline("p", [jira_source()], name_policy="original"))
    write(brain_dir / "raw/p/jira/MD/MD-1 One.md", "mine")
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "conflicts" and (brain_dir / "raw/p/jira/MD/MD-1 One.md").read_text() == "mine"


# 12
def test_12_a_rate_limit_storm_gives_the_correct_result_just_slower(world):
    world.fake.rate_limit(times=3, retry_after=2)
    res = world.sync()
    assert res.result == "ok" and len(jira_files(world)) == 6 and world.fake.sleeps == [2, 2, 2]
    world.fake.rate_limit(times=99, retry_after=1)
    storm = world.sync()
    assert storm.result == "error" and storm.counts()["deleted"] == 0 and len(jira_files(world)) == 6


# 13
def test_13_a_kill_mid_sync_converges_with_no_partial_file(world):
    seen = []

    def die(item):
        seen.append(item)
        if len(seen) == 3:
            raise Kill()

    with pytest.raises(Kill):
        world.sync(after_write=die)
    assert not list((world.brain.root / "raw").rglob("*.tmp"))
    res = world.sync()
    assert res.result == "ok" and res.counts()["conflicts"] == 0 and len(jira_files(world)) == 6
    assert world.sync().counts()["unchanged"] == 6


# 14
def test_14_concurrent_syncs_the_second_is_busy_and_writes_nothing(world):
    world.sync()
    world.fake.add_issue("MD", "late")
    with brain_lock(world.brain):
        res = world.sync()
    assert res.result == "busy" and len(jira_files(world)) == 6


# 15
def test_15_the_token_and_email_are_nowhere_after_a_full_cycle(world, capsys, monkeypatch):
    outputs = []

    def cli(*argv):
        main(["--brain", str(world.brain.root), *argv])
        out = capsys.readouterr()
        outputs.extend([out.out, out.err])

    cli("jira", "test", "--pipeline", "p", "--source", "jira")
    cli("sync", "p")
    world.fake.add_comment("MD-1", "x")
    cli("sync", "p", "--json")
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    cli("sync", "p")
    cli("status", "--json")
    cli("log", "--json")
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    haystack = outputs + [p.read_text(errors="replace") for p in world.brain.root.rglob("*") if p.is_file()]
    assert not any(TOKEN in h or EMAIL in h or "ATATT-WRONG" in h for h in haystack)


# 16
def test_16_folder_only_pipelines_make_zero_network_calls(brain, add_pipeline, make_source, monkeypatch):
    calls = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a))
    jira_api.set_transport(lambda *a: calls.append(a))
    try:
        add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
        assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    finally:
        jira_api.set_transport(None)
    assert calls == []


# 17
def test_17_idempotence_the_second_sync_performs_zero_writes(world):
    world.sync()
    snapshot = {p: p.stat().st_mtime_ns for p in (world.brain.root / "raw").rglob("*.md")}
    state_before = (world.brain.config_dir / "state.json").read_bytes()
    seen = len(world.fake.requests)
    res = world.sync()
    assert res.counts()["unchanged"] == 6 and res.counts()["created"] == res.counts()["updated"] == res.counts()["deleted"] == 0
    assert snapshot == {p: p.stat().st_mtime_ns for p in (world.brain.root / "raw").rglob("*.md")}
    assert (world.brain.config_dir / "state.json").read_bytes() == state_before
    assert not [p for _, p in world.fake.requests[seen:] if "/issue/" in p]


# 18
def test_18_rebuild_with_the_site_unreachable_claims_nothing(world):
    world.sync()
    (world.brain.config_dir / "state.json").unlink()
    for b in (world.brain.config_dir / "backups").glob("*"):
        b.unlink()
    world.fake.fail("GET", "/search/jql", status=503, times=9)
    preview = rebuild_preview(world.brain)
    assert preview.claimed_total == 0 and len(preview.unclaimed) == 6 and all("cannot be read now" in u.reason for u in preview.unclaimed)
    curated_intact(world)
