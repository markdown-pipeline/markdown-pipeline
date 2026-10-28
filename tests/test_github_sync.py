"""MD-62: one atomic commit per sync (content + state), zero commits when nothing changed, fast-forward only."""
import json

import pytest

from conftest import write
from mdpipeline.cli import main
from mdpipeline.engine import sync_pipeline
from mdpipeline.github_target import STATE_PATH
from mdpipeline.log import ActivityLog


def test_a_sync_is_exactly_one_commit_with_content_and_state(gh):
    gh.pipeline("p", {"a.md": "A", "b.md": "B"})
    before = len(gh.fake.history())
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["created"] == 2
    assert len(gh.fake.history()) == before + 1
    changed = gh.fake.commit_files_changed(gh.fake.head())
    assert STATE_PATH in changed
    assert sorted(p for p in changed if p.startswith("raw/")) == ["raw/p/p/a.md", "raw/p/p/b.md"]
    assert set(changed) == {"raw/p/p/a.md", "raw/p/p/b.md", STATE_PATH}
    assert set(json.loads(changed[STATE_PATH])["pipelines"]["p"]["files"]) == {p for p in changed if p.startswith("raw/")}


def test_commit_message_is_standard_and_machine_filterable(gh):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    msg = gh.fake.commit_message(gh.fake.head())
    assert msg == "mdpipeline sync [p]: 1 created, 0 updated, 0 renamed, 0 deleted"


def test_second_sync_with_no_changes_creates_no_commit(gh):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    head, seen = gh.fake.head(), len(gh.fake.requests)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and res.commit is None and gh.fake.head() == head
    assert not [r for r in gh.fake.requests[seen:] if r[0] in ("POST", "PATCH")]


def test_update_and_delete_are_in_one_commit_and_state_matches(gh):
    src = gh.pipeline("p", {"a.md": "A", "b.md": "B", "c.md": "C"}, guard_threshold_percent=100)
    sync_pipeline(gh.brain, "p", trigger="cli")
    (src / "a.md").write_text("A2")
    (src / "b.md").unlink()
    before = len(gh.fake.history())
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.counts()["updated"] == 1 and res.counts()["deleted"] == 1
    assert len(gh.fake.history()) == before + 1
    assert gh.fake.commit_message(gh.fake.head()).startswith("mdpipeline sync [p]: 0 created, 1 updated, 0 renamed, 1 deleted")
    state = json.loads(gh.fake.read(STATE_PATH))["pipelines"]["p"]["files"]
    assert sorted(state) == sorted(p for p in gh.fake.files() if p.startswith("raw/"))


@pytest.mark.parametrize("step", ["/git/blobs", "/git/trees", "/git/commits"])
def test_network_failure_at_any_step_leaves_the_branch_unchanged_and_the_next_sync_converges(gh, step):
    gh.pipeline("p", {"a.md": "A"})
    head = gh.fake.head()
    gh.fake.fail("POST", step, status=502, times=1)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "error" and res.category == "network" and gh.fake.head() == head
    again = sync_pipeline(gh.brain, "p", trigger="cli")
    assert again.result == "ok" and again.counts()["created"] == 1 and again.commit is not None


def test_failure_when_moving_the_ref_leaves_the_branch_unchanged(gh):
    gh.pipeline("p", {"a.md": "A"})
    head = gh.fake.head()
    gh.fake.fail("PATCH", "/git/refs/", status=500, times=1)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "error" and gh.fake.head() == head
    assert sync_pipeline(gh.brain, "p", trigger="cli").counts()["created"] == 1


def test_rate_limit_and_auth_failures_are_reported_with_their_category(gh, monkeypatch):
    gh.pipeline("p", {"a.md": "A"})
    head = gh.fake.head()
    gh.fake.rate_limit(times=1)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "error" and res.category == "rate-limited" and gh.fake.head() == head
    monkeypatch.setenv("BRAIN_TOKEN", "ghp_WRONGWRONGWRONG")
    gh.brain._cache.clear()
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "error" and res.category == "auth" and gh.fake.head() == head and "ghp_WRONG" not in res.message


def test_the_branch_is_only_ever_fast_forwarded(gh):
    src = gh.pipeline("p", {"a.md": "A", "b.md": "B"}, guard_threshold_percent=100)
    first = gh.fake.history()
    sync_pipeline(gh.brain, "p", trigger="cli")
    (src / "a.md").write_text("A2")
    sync_pipeline(gh.brain, "p", trigger="cli")
    assert gh.fake.forced_updates == 0 and gh.fake.is_linear_history()
    assert gh.fake.history()[: len(first)] == first            # nothing in the earlier history was rewritten
    assert not [c for c in gh.fake.log if c["method"] == "PATCH" and c["body"].get("force")]


def test_a_guard_blocked_sync_creates_no_commit_and_approval_commits_exactly_the_shown_plan(gh):
    src = gh.pipeline("p", {f"{i}.md": str(i) for i in range(10)})
    sync_pipeline(gh.brain, "p", trigger="cli")
    for i in range(8):
        (src / f"{i}.md").unlink()
    head = gh.fake.head()
    blocked = sync_pipeline(gh.brain, "p", trigger="cli")
    assert blocked.result == "blocked" and gh.fake.head() == head
    shown = sorted(f["target"] for f in blocked.to_dict()["affected_files"])
    mcp = sync_pipeline(gh.brain, "p", trigger="mcp", approve=True)
    assert mcp.result == "blocked" and gh.fake.head() == head          # the MCP can never approve
    blocked_history = gh.fake.history()
    approved = sync_pipeline(gh.brain, "p", trigger="cli", approve=True)
    assert approved.result == "ok"
    deleted = sorted(p for p, d in gh.fake.commit_files_changed(gh.fake.head()).items() if d is None)
    assert deleted == shown and len(gh.fake.history()) == len(blocked_history) + 1
    assert "change-set: cs-" in gh.fake.commit_message(gh.fake.head())


def test_the_commit_url_is_in_the_result_the_log_and_the_cli_output(gh, capsys):
    gh.pipeline("p", {"a.md": "A"})
    code = main(["--brain", str(gh.brain.root), "sync", "p"])
    out = capsys.readouterr().out
    sha = gh.fake.head()
    assert code == 0 and f"https://github.com/acme/brain/commit/{sha}" in out
    run = ActivityLog(gh.brain).read(types={"run"})[-1]
    assert run["commit"] == {"sha": sha, "url": f"https://github.com/acme/brain/commit/{sha}"}
    write(gh.tmp / "sources" / "p" / "new.md", "N")
    main(["--brain", str(gh.brain.root), "sync", "p", "--json"])
    assert json.loads(capsys.readouterr().out)[0]["commit"]["sha"] == gh.fake.head() != sha
