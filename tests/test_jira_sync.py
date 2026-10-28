"""MD-49, MD-50, MD-51, MD-52: names, incremental sync, provenance/recovery and deletions/outages for Jira sources (fake Jira, no network)."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from conftest import write
from fake_jira import FakeJira, adf_doc, adf_p
from mdpipeline import jira_api
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.naming import SEPARATOR
from mdpipeline.state import State

EMAIL, TOKEN = "me@example.com", "ATATT-SECRET-TOKEN"
SITE = "https://acme.atlassian.net"


def day(y, m, d):
    return datetime(y, m, d, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.sleeps = []
    jira_api.set_transport(f, sleep=f.sleeps.append)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def jira_source(jql="project = MD", label="jira"):
    return Source(SITE, label, type="jira", options={"site": SITE, "jql": jql, "email_ref": "env:JIRA_EMAIL", "token_ref": "env:JIRA_TOKEN"})


@pytest.fixture
def env(brain, add_pipeline, fake):
    def make(jql="project = MD", **rules):
        rules.setdefault("name_policy", "date-name")
        rules.setdefault("guard_threshold_percent", 100)
        add_pipeline(Pipeline("p", [jira_source(jql)], **rules))
        return SimpleNamespace(brain=brain, fake=fake, sync=lambda **kw: sync_pipeline(brain, "p", trigger="cli", **kw))

    return make


def issue_requests(fake, since=0):
    return [p for _, p in fake.requests[since:] if "/issue/" in p]


def raw_files(brain):
    return sorted(str(p.relative_to(brain.root)) for p in (brain.root / "raw").rglob("*.md"))


# ---- MD-49: names and folders -------------------------------------------------------------------
def test_default_template_puts_each_issue_under_its_project_with_key_and_summary(env, fake):
    e = env(name_policy="original")
    fake.add_issue("MD", "First issue")
    e.sync()
    assert raw_files(e.brain) == ["raw/p/jira/MD/MD-1 First issue.md"]


def test_date_name_policy_uses_the_issue_created_date_not_the_sync_date(env, fake):
    e = env(target="raw/{source}/{relpath}")
    fake.add_issue("MD", "P2 File-name policy for raw/", created=day(2026, 10, 4))
    e.sync()
    [path] = raw_files(e.brain)
    assert path == f"raw/jira/MD/2026-10-04{SEPARATOR}md-1-p2-file-name-policy-for-raw.md"


def test_a_summary_change_does_not_rename_the_file(env, fake):
    e = env()
    fake.add_issue("MD", "Original title", created=day(2026, 10, 4))
    e.sync()
    before = raw_files(e.brain)
    fake.update_issue("MD-1", summary="A completely different title")
    res = e.sync()
    assert res.counts()["updated"] == 1 and res.counts()["renamed"] == 0 and raw_files(e.brain) == before
    assert "A completely different title" in (e.brain.root / before[0]).read_text()


def test_a_key_change_by_a_project_move_is_one_rename_with_no_delete_and_no_duplicate(env, fake):
    e = env("project in (MD, MP)", target="raw/{source}/{relpath}")
    fake.add_issue("MP", "seed so that MP-1 exists")
    fake.add_issue("MD", "Moves later", created=day(2026, 10, 4))
    e.sync()
    old = next(f for f in raw_files(e.brain) if "/MD/" in f)
    new_key = fake.move_issue("MD-1", "MP")
    fake.update_issue(new_key, summary="Moves later")
    res = e.sync()
    c = res.counts()
    assert c["created"] == 0 and c["deleted"] == 0 and c["renamed"] == 1
    assert len(raw_files(e.brain)) == 2 and not any("/MD/" in f for f in raw_files(e.brain))
    new = old.replace("/MD/", "/MP/")
    assert new in raw_files(e.brain)                                          # same name, the project folder follows the key
    meta, _ = parse_document((e.brain.root / new).read_text())
    assert meta["provenance"]["issue_key"] == new_key and meta["jira"]["key"] == new_key


# ---- MD-50: incremental sync --------------------------------------------------------------------
def test_second_sync_with_no_jira_change_performs_zero_writes_and_fetches_no_issue(env, fake):
    e = env()
    for n in range(3):
        fake.add_issue("MD", f"issue {n}", comments=["a comment"])
    e.sync()
    seen = len(fake.requests)
    before = {p: p.stat().st_mtime_ns for p in (e.brain.root / "raw").rglob("*.md")}
    res = e.sync()
    assert res.counts()["unchanged"] == 3 and res.counts()["updated"] == res.counts()["created"] == 0
    assert issue_requests(fake, seen) == [] and before == {p: p.stat().st_mtime_ns for p in (e.brain.root / "raw").rglob("*.md")}


def test_only_the_changed_issue_is_fetched_and_rewritten(env, fake):
    e = env()
    for n in range(4):
        fake.add_issue("MD", f"issue {n}")
    e.sync()
    seen = len(fake.requests)
    fake.update_issue("MD-3", summary="issue 2, edited")
    res = e.sync()
    assert res.counts()["updated"] == 1
    fetched = {p.split("/")[5] for p in issue_requests(fake, seen)}
    assert fetched == {fake.issue_id("MD-3")}


@pytest.mark.parametrize("change", ["add", "edit", "delete"])
def test_a_comment_added_edited_or_deleted_updates_the_file(env, fake, change):
    e = env()
    fake.add_issue("MD", "Talked about", comments=["alpha comment"])
    e.sync()
    cid = fake._find("MD-1")["comments"][0]["id"]
    {"add": lambda: fake.add_comment("MD-1", "second"), "edit": lambda: fake.edit_comment("MD-1", cid, "alpha comment, edited"),
     "delete": lambda: fake.delete_comment("MD-1", cid)}[change]()
    res = e.sync()
    assert res.counts()["updated"] == 1
    text = (e.brain.root / raw_files(e.brain)[0]).read_text()
    assert {"add": "second" in text, "edit": "alpha comment, edited" in text, "delete": "alpha comment" not in text}[change]


def test_a_killed_first_backfill_converges_with_no_duplicates_or_false_conflicts(env, fake):
    class Kill(BaseException):
        pass

    e = env()
    for n in range(8):
        fake.add_issue("MD", f"issue {n}")
    seen = []

    def die(item):
        seen.append(item)
        if len(seen) == 3:
            raise Kill()

    with pytest.raises(Kill):
        e.sync(after_write=die)
    res = e.sync()
    assert res.result == "ok" and res.counts()["conflicts"] == 0 and len(raw_files(e.brain)) == 8
    assert e.sync().counts()["unchanged"] == 8


def test_a_rate_limit_storm_is_slower_never_wrong(env, fake):
    e = env()
    fake.add_issue("MD", "One")
    fake.rate_limit(times=2, retry_after=5)
    res = e.sync()
    assert res.result == "ok" and fake.sleeps == [5, 5] and len(raw_files(e.brain)) == 1


def test_a_rate_limit_that_never_clears_fails_the_sync_and_deletes_nothing(env, fake):
    e = env()
    fake.add_issue("MD", "One")
    e.sync()
    fake.rate_limit(times=999, retry_after=1)
    res = e.sync()
    assert res.result == "error" and res.errors[0]["category"] == "rate-limited" and len(raw_files(e.brain)) == 1


def test_a_changed_renderer_version_rewrites_every_file_once(env, fake, monkeypatch):
    e = env()
    fake.add_issue("MD", "One")
    e.sync()
    import mdpipeline.jira_source as js

    monkeypatch.setattr(js, "RENDER_VERSION", 99)
    assert e.sync().counts()["unchanged"] == 1           # same bytes: adopted without a write
    assert State.load(e.brain).pipeline("p").files[raw_files(e.brain)[0]].source_version.endswith("|r99")


# ---- MD-51: provenance, ownership, recovery ------------------------------------------------------
def test_provenance_names_the_site_issue_and_updated_stamp(env, fake):
    e = env()
    fake.add_issue("MD", "One")
    e.sync()
    meta, _ = parse_document((e.brain.root / raw_files(e.brain)[0]).read_text())
    prov = meta["provenance"]
    assert prov["source"] == "jira" and prov["pipeline"] == "p" and prov["source_label"] == "jira" and prov["site"] == SITE
    assert prov["issue_id"] == fake.issue_id("MD-1") and prov["source_path"] == prov["issue_id"] and prov["issue_key"] == "MD-1"
    assert prov["issue_url"] == f"{SITE}/browse/MD-1" and prov["source_updated"].endswith("+0000") and prov["content_hash"].startswith("sha256:")
    assert {"original_name", "first_seen", "name_policy"} <= set(prov)


def test_a_brain_edited_jira_file_with_an_updated_issue_is_a_conflict_never_overwritten(env, fake):
    e = env()
    fake.add_issue("MD", "One")
    e.sync()
    path = e.brain.root / raw_files(e.brain)[0]
    path.write_text(path.read_text() + "\nmy own notes\n")
    fake.add_comment("MD-1", "new comment")
    res = e.sync()
    assert res.result == "conflicts" and "my own notes" in path.read_text() and "new comment" not in path.read_text()


def test_a_hand_written_file_at_a_jira_target_is_never_claimed(env, fake, brain_dir):
    e = env(name_policy="original")
    write(brain_dir / "raw/p/jira/MD/MD-1 One.md", "hand written")
    fake.add_issue("MD", "One")
    res = e.sync()
    assert res.result == "conflicts" and (brain_dir / "raw/p/jira/MD/MD-1 One.md").read_text() == "hand written"


def test_state_backup_and_restore_cover_jira_sources(env, fake):
    from mdpipeline.recovery import apply_restore

    e = env()
    fake.add_issue("MD", "One")
    e.sync()
    fake.add_issue("MD", "Two")
    e.sync()
    (e.brain.config_dir / "state.json").write_text("{ corrupt")
    apply_restore(e.brain)
    files = State.load(e.brain).pipeline("p").files
    assert files and all(r.source_version and r.source_path.isdigit() for r in files.values())
    res = e.sync()
    assert res.result == "ok" and res.counts()["conflicts"] == 0 and len(raw_files(e.brain)) == 2


def lose_state(e):
    (e.brain.config_dir / "state.json").unlink()
    for g in (e.brain.config_dir / "backups").glob("*"):
        g.unlink()


def test_rebuild_reowns_matching_jira_files_when_the_site_is_reachable(env, fake):
    from mdpipeline.rebuild import apply_rebuild, rebuild_preview

    e = env()
    fake.add_issue("MD", "One")
    fake.add_issue("MD", "Two")
    e.sync()
    lose_state(e)
    assert rebuild_preview(e.brain).claimed_total == 2
    apply_rebuild(e.brain)
    assert e.sync().counts()["unchanged"] == 2


def test_rebuild_claims_nothing_when_the_site_is_unreachable(env, fake):
    from mdpipeline.rebuild import rebuild_preview

    e = env()
    fake.add_issue("MD", "One")
    e.sync()
    lose_state(e)
    fake.fail("GET", "/search/jql", status=503, times=9)
    preview = rebuild_preview(e.brain)
    assert preview.claimed_total == 0 and preview.unclaimed and "cannot be read now" in preview.unclaimed[0].reason


def test_rebuild_never_claims_a_file_that_does_not_match_the_current_render(env, fake):
    from mdpipeline.rebuild import MISMATCH, rebuild_preview

    e = env()
    fake.add_issue("MD", "One")
    e.sync()
    fake.add_comment("MD-1", "the issue moved on")
    lose_state(e)
    preview = rebuild_preview(e.brain)
    assert preview.claimed_total == 0 and preview.unclaimed[0].reason == MISMATCH


# ---- MD-52: deletions, scope exits and outages ---------------------------------------------------
def test_an_issue_deleted_in_jira_follows_the_on_delete_policy(env, fake):
    e = env()
    fake.add_issue("MD", "One")
    fake.add_issue("MD", "Two")
    e.sync()
    fake.delete_issue("MD-2")
    res = e.sync()
    assert res.counts()["deleted"] == 1 and len(raw_files(e.brain)) == 1


def test_on_delete_keep_keeps_a_deleted_issue_file(env, fake):
    e = env(on_source_delete="keep")
    fake.add_issue("MD", "One")
    e.sync()
    fake.delete_issue("MD-1")
    assert e.sync().counts()["kept"] == 1 and len(raw_files(e.brain)) == 1


def test_an_issue_that_leaves_the_scope_is_treated_as_a_deletion_within_one_sync(env, fake):
    e = env(name_policy="original")
    fake.add_issue("MD", "Stays")
    fake.add_issue("MD", "Leaves")
    e.sync()
    fake.move_issue("MD-2", "OTHER")
    res = e.sync()
    assert res.counts()["deleted"] == 1 and raw_files(e.brain) == ["raw/p/jira/MD/MD-1 Stays.md"]


@pytest.mark.parametrize("failure", [("fail", 401), ("fail", 403), ("fail", 404), ("fail", 503), ("page", 2), ("garbage", None)])
def test_no_deletions_when_the_site_fails_in_any_way(env, fake, failure):
    e = env()
    for n in range(3):
        fake.add_issue("MD", f"issue {n}")
    e.sync()
    kind, arg = failure
    if kind == "fail":
        fake.fail("GET", "/search/jql", status=arg, times=9)
    elif kind == "page":
        from mdpipeline.jira_source import JiraSource

        JiraSource.page_size = 1
        fake.fail_page(arg)
    else:
        original = fake.__call__

        class Garbage:
            def __call__(self, method, url, headers, body):
                status, h, data = original(method, url, headers, body)
                return (status, h, b"<html>maintenance</html>") if "/search/jql" in url else (status, h, data)

        jira_api.set_transport(Garbage(), sleep=lambda s: None)
    try:
        res = e.sync()
    finally:
        from mdpipeline.jira_source import JiraSource

        JiraSource.page_size = 100
    assert res.result == "error" and res.counts()["deleted"] == 0 and len(raw_files(e.brain)) == 3
    assert res.errors[0]["category"] in ("auth", "permission", "site-not-found", "network", "malformed")


def test_a_failing_jira_source_does_not_stop_the_other_sources(env, fake, brain_dir, make_source):
    e = env()
    folder = make_source("docs", {"a.md": "A"})
    cfg = load_config(e.brain)
    cfg.get("p").sources.append(Source(str(folder), "docs"))
    save_config(e.brain, cfg)
    fake.add_issue("MD", "One")
    fake.fail("GET", "/search/jql", status=503, times=9)
    res = e.sync()
    assert res.result == "error" and list((brain_dir / "raw/p/docs").glob("*.md"))


def test_zero_matches_for_a_source_that_owns_files_always_asks_the_guard(env, fake):
    e = env(guard_threshold_percent=100, guard_min_files=50)
    for n in range(3):
        fake.add_issue("MD", f"issue {n}")
    e.sync()
    for n in range(1, 4):
        fake.delete_issue(f"MD-{n}")
    res = e.sync()
    assert res.result == "blocked" and "returned 0" in res.plan.guard.message and len(raw_files(e.brain)) == 3
    assert e.sync(approve=True).counts()["deleted"] == 3


def test_editing_the_jql_is_a_planned_change_the_dry_run_shows_and_the_guard_applies(env, fake):
    e = env(guard_threshold_percent=40, guard_min_files=2)
    for n in range(3):
        fake.add_issue("MD", f"md {n}")
    fake.add_issue("MP", "mp 0")
    cfg = load_config(e.brain)
    cfg.get("p").sources[0] = jira_source("project in (MD, MP)")
    save_config(e.brain, cfg)
    e.sync()
    cfg = load_config(e.brain)
    cfg.get("p").sources[0] = jira_source("project = MP")
    save_config(e.brain, cfg)
    dry = e.sync(dry_run=True)
    assert dry.counts()["deleted"] == 3 and dry.result == "blocked" and len(raw_files(e.brain)) == 4
    assert e.sync().result == "blocked" and len(raw_files(e.brain)) == 4


def test_a_permission_loss_that_hides_issues_counts_as_leaving_the_scope_when_jira_reports_success(env, fake):
    e = env(name_policy="original")
    fake.add_issue("MD", "Visible")
    fake.add_issue("MD", "Becomes hidden")
    e.sync()
    fake.hide("MD-2")
    assert e.sync().counts()["deleted"] == 1


# --- MD-174: a rejected token fails the sync; it never plans deletions -----------------------------------------
@pytest.mark.parametrize("files", [3, 8])       # 3 is under guard_min_files (no guard at all); 8 is guarded
def test_a_revoked_token_fails_the_sync_as_auth_and_touches_nothing(env, fake, monkeypatch, files):
    e = env(guard_threshold_percent=40, guard_min_files=5)
    for n in range(files):
        fake.add_issue("MD", f"issue {n}")
    e.sync()
    before = {f: (e.brain.root / f).read_bytes() for f in raw_files(e.brain)}
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-REVOKED")
    res = e.sync()
    assert res.result == "error" and res.errors[0]["category"] == "auth"
    assert res.counts()["deleted"] == 0
    assert {f: (e.brain.root / f).read_bytes() for f in raw_files(e.brain)} == before


def test_a_revoked_token_dry_run_reports_auth_and_offers_no_change_set(env, fake, monkeypatch):
    e = env()
    for n in range(8):
        fake.add_issue("MD", f"issue {n}")
    e.sync()
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-REVOKED")
    res = e.sync(dry_run=True)
    assert res.result == "error" and res.errors[0]["category"] == "auth" and res.counts()["deleted"] == 0
