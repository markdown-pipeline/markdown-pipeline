"""MD-201: one file with invalid front matter is a WARNING, not a run `error`.

Product decisions (comments 11291, and dev's 11294): the bad file is skipped (never changed at the source), the other files sync as usual, the run
is `ok`, no `sync_failed`, the warning names the file and the line and gives one next step, and it is counted. A real error stays an error."""
import json

import pytest

from conftest import write
from helpers import seed_owned
from mdpipeline.cli import main
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.planner import plan_pipeline
from mdpipeline.state import State

# the exact pattern of the evidence: a quoted value followed by more text, on line 3 of the file
BAD = '---\ntitle: Post\nframing: "Notice what changed" (MM-143 section 9). We do not promise\n---\nbody text\n'
GOOD = "---\ntitle: Fine\n---\nhello\n"


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def pair(brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "notes/bad.md": BAD})
    add_pipeline(Pipeline.new("p", [str(src)]))
    return src


def test_planner_marks_the_bad_file_as_a_skipped_warning_not_an_error(brain, pair):
    plan = plan_pipeline(brain, Pipeline.new("p", [str(pair)]), State.load(brain).pipeline("p"), now=1_790_000_000.0)
    bad = next(i for i in plan.groups[0].items if i.source_path == "notes/bad.md")
    assert bad.action == "skip" and not bad.error and bad.warning == "invalid-yaml"
    counts = plan.counts()
    assert counts["errors"] == 0 and counts["skipped"] == 1 and counts["created"] == 1


def test_the_warning_carries_the_line_in_the_source_file(brain, pair):
    plan = plan_pipeline(brain, Pipeline.new("p", [str(pair)]), State.load(brain).pipeline("p"), now=1_790_000_000.0)
    [w] = plan.front_matter_warnings
    assert w["path"] == "notes/bad.md" and w["line"] == 3 and w["source"] == "docs"


def test_a_missing_closing_delimiter_is_a_warning_without_a_line(brain, add_pipeline, make_source):
    src = make_source("docs", {"open.md": "---\ntitle: x\nbody but no closing line\n", "ok.md": "fine"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    res = sync_pipeline(brain, "p")
    assert res.result == "ok"
    [w] = res.plan.front_matter_warnings
    assert w["path"] == "open.md" and w["line"] is None and w["kind"] == "no-closing"


def test_the_run_is_ok_and_the_good_file_is_written_in_a_real_run(brain, pair):
    res = sync_pipeline(brain, "p")
    assert res.result == "ok"
    assert (brain.root / "raw/p/docs/good.md").exists() and not (brain.root / "raw/p/docs/notes/bad.md").exists()
    assert (pair / "notes/bad.md").read_text() == BAD                      # the source is never changed


def test_the_log_entry_is_ok_with_the_warning_and_no_errors(brain, pair):
    sync_pipeline(brain, "p")
    entry = ActivityLog(brain).read(types={"run"})[-1]
    assert entry["result"] == "ok" and entry["errors"] == []
    assert entry["front_matter_warnings"] == [{"source": "docs", "path": "notes/bad.md", "line": 3, "kind": "invalid-yaml", "had_copy": False}]


def test_a_dry_run_is_ok_too(brain, pair):
    res = sync_pipeline(brain, "p", dry_run=True)
    assert res.result == "ok" and len(res.plan.front_matter_warnings) == 1


def test_cli_exits_zero_and_prints_the_file_the_line_and_one_next_step(brain, pair, capsys):
    code, out, _ = run(capsys, brain, "sync", "p")
    assert code == 0
    assert out.splitlines()[0] == "p: ok, 1 warning"
    assert "warning" in out and "notes/bad.md" in out
    assert "is not valid YAML, near line 3." in out
    assert "This file was skipped and not changed. Your other files synced." in out
    assert "Check near line 3 (the mistake can be on the line before), or exclude the file." in out
    assert "at line" not in out and "Fix line" not in out          # MM-166: the parser can be one line late


def test_cli_dry_run_header_says_dry_run_and_the_warning(brain, pair, capsys):
    code, out, _ = run(capsys, brain, "sync", "p", "--dry-run")
    assert code == 0 and out.splitlines()[0] == "p: ok, 1 warning (dry run)"


def test_cli_json_output_carries_the_warning(brain, pair, capsys):
    code, out, _ = run(capsys, brain, "sync", "p", "--json")
    assert code == 0
    assert json.loads(out)[0]["front_matter_warnings"][0]["path"] == "notes/bad.md"


def test_a_run_with_no_bad_file_prints_no_warning_text(brain, add_pipeline, make_source, capsys):
    src = make_source("docs", {"good.md": GOOD})
    add_pipeline(Pipeline.new("p", [str(src)]))
    _, out, _ = run(capsys, brain, "sync", "p")
    assert out.splitlines()[0] == "p: ok" and "warning" not in out


def test_a_real_error_next_to_a_warning_is_still_an_error(brain, add_pipeline, make_source):
    import os
    import winsupport
    if winsupport.is_root() or os.name == "nt":
        pytest.skip("an unreadable file needs Unix permissions and a non-root user")
    src = make_source("docs", {"bad.md": BAD, "locked.md": "x"})
    os.chmod(src / "locked.md", 0)
    add_pipeline(Pipeline.new("p", [str(src)]))
    try:
        res = sync_pipeline(brain, "p")
    finally:
        os.chmod(src / "locked.md", 0o644)
    assert res.result == "error" and res.plan.counts()["errors"] == 1


def test_no_sync_failed_event_for_a_front_matter_warning(brain, pair, monkeypatch):
    from mdpipeline import usage, usage_hooks
    from mdpipeline.config import load_config
    events = []
    monkeypatch.setattr(usage, "emit", lambda name, **props: events.append(name))
    res = sync_pipeline(brain, "p")
    usage_hooks.sync_finished(res, load_config(brain).get("p"))
    assert "sync_failed" not in events


def test_two_runs_in_a_row_are_not_failed_runs_for_the_notice(brain, pair):
    from mdpipeline import notices
    sync_pipeline(brain, "p")
    sync_pipeline(brain, "p")
    runs = ActivityLog(brain).read(types={"run"})[-2:]
    assert [r["result"] for r in runs] == ["ok", "ok"] and not any(notices._failed(r) for r in runs)


def test_a_file_synced_before_and_then_broken_keeps_its_old_copy_and_says_so(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": GOOD})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    old = (brain.root / "raw/p/docs/a.md").read_bytes()
    (src / "a.md").write_text(BAD)
    res = sync_pipeline(brain, "p")
    assert res.result == "ok"
    assert (brain.root / "raw/p/docs/a.md").read_bytes() == old               # not deleted, not updated
    assert res.plan.counts()["deleted"] == 0
    assert res.plan.front_matter_warnings[0]["had_copy"] is True


def test_the_guard_counts_are_unaffected_by_a_warned_file(brain, pair):
    res = sync_pipeline(brain, "p")
    guard = res.plan.guard.to_dict()
    assert not guard["blocked"]
    assert res.plan.counts()["deleted"] == 0 and res.plan.counts()["updated"] == 0


def test_github_target_gets_the_good_file_and_an_ok_run(gh):
    gh.pipeline("p", {"good.md": GOOD, "notes/bad.md": BAD})
    res = sync_pipeline(gh.brain, "p")
    assert res.result == "ok" and res.commit is not None
    assert len(res.plan.front_matter_warnings) == 1
    assert not any(path.endswith("bad.md") for path in gh.fake.files())


def test_github_target_cli_exit_code_is_zero(gh, capsys):
    gh.pipeline("p", {"good.md": GOOD, "notes/bad.md": BAD})
    code, out, _ = run(capsys, gh.brain, "sync", "p")
    assert code == 0 and "p: ok, 1 warning" in out


# AC5: the UI. The warning is derived from the pipeline's LAST run, so an hourly run shows the same one note, never a growing pile.
def test_status_carries_the_last_runs_warning_for_the_ui(brain, pair):
    from mdpipeline.status import brain_status
    sync_pipeline(brain, "p")
    sync_pipeline(brain, "p")
    [entry] = brain_status(brain)["pipelines"]
    assert entry["status"] == "ok"
    assert entry["front_matter_warnings"] == [{"source": "docs", "path": "notes/bad.md", "line": 3, "kind": "invalid-yaml", "had_copy": False}]


def test_status_has_no_warning_after_the_file_is_fixed(brain, pair):
    from mdpipeline.status import brain_status
    sync_pipeline(brain, "p")
    (pair / "notes/bad.md").write_text(GOOD)
    sync_pipeline(brain, "p")
    assert brain_status(brain)["pipelines"][0]["front_matter_warnings"] == []


def test_an_hourly_run_does_not_add_a_needs_you_item_or_a_badge(brain, pair):
    from mdpipeline.uiactivity import activity_view
    for _ in range(3):
        sync_pipeline(brain, "p")
    view = activity_view(brain)
    assert view["needs_you"] == [] and view["badge"] == 0


# --- product comment 11328, point 4: `not-mapping` is valid YAML, so the text must not say "not valid YAML" -------------
NOT_MAPPING = "---\n- one\n- two\n---\nbody\n"


def test_not_mapping_gets_its_own_sentence_not_the_invalid_yaml_one(capsys, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "list.md": NOT_MAPPING})
    add_pipeline(Pipeline.new("p", [str(src)]))
    _, out, _ = run(capsys, brain, "sync", "p")
    assert "is not made of key: value lines." in out
    assert "Rewrite it as key: value lines, or exclude the file." in out
    assert "not valid YAML" not in out and "line 2" not in out       # the YAML is fine and the parser gave no real line


# --- product comment 11328, point 2 (AC3): the count of files, nothing else --------------------------------------------
def test_a_front_matter_warning_event_carries_only_the_file_count(brain, pair, monkeypatch):
    from mdpipeline import usage
    events = []
    monkeypatch.setattr(usage, "emit", lambda name, **props: events.append((name, props)))
    sync_pipeline(brain, "p")          # the engine reports the finished run itself
    assert [p for n, p in events if n == "front_matter_warning"] == [{"files_count": 1}]


def test_no_front_matter_warning_event_when_every_file_is_fine(brain, add_pipeline, make_source, monkeypatch):
    from mdpipeline import usage, usage_hooks
    from mdpipeline.config import load_config
    events = []
    monkeypatch.setattr(usage, "emit", lambda name, **props: events.append(name))
    add_pipeline(Pipeline.new("p", [str(make_source("docs", {"good.md": GOOD}))]))
    res = sync_pipeline(brain, "p")
    usage_hooks.sync_finished(res, load_config(brain).get("p"))
    assert "front_matter_warning" not in events


def test_the_event_is_queued_with_the_count_only_when_sharing(brain, pair):
    import json
    from mdpipeline import usage
    usage.UsageStore().set_consent("share")
    sync_pipeline(brain, "p", trigger="cli")
    q = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    (e,) = [x for x in q if x["event"] == "front_matter_warning"]
    assert e["properties"]["files_count"] == 1
    assert "notes/bad.md" not in json.dumps(q) and "line" not in {k for k in e["properties"]}


# --- MM-166 (marketing review, Jira comment 11357): the reviewed words ------------------------------------------------
def test_cli_no_closing_line_uses_the_reviewed_words(capsys, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "open.md": "---\ntitle: x\nbody\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    _, out, _ = run(capsys, brain, "sync", "p")
    assert "has no closing --- line." in out
    assert "This file was skipped and not changed. Your other files synced." in out
    assert "Add the closing --- line, or exclude the file." in out


def test_cli_invalid_yaml_without_a_line_drops_the_line(capsys):
    from mdpipeline.cli import front_matter_warning_lines
    out = "\n".join(front_matter_warning_lines("a.md", "invalid-yaml", None))
    assert "is not valid YAML." in out and "near line" not in out
    assert "Check the front matter, or exclude the file." in out


def test_cli_says_when_the_old_copy_is_out_of_date(capsys):
    from mdpipeline.cli import front_matter_warning_lines
    out = front_matter_warning_lines("a.md", "invalid-yaml", 7, had_copy=True)
    assert out[-1].strip() == "The copy in your output folder is from before your last edit."
    assert not any("old copy" in l or "from before" in l for l in front_matter_warning_lines("a.md", "invalid-yaml", 7))


def test_cli_plural_header(capsys, brain, add_pipeline, make_source):
    src = make_source("docs", {"good.md": GOOD, "a.md": BAD, "b.md": BAD})
    add_pipeline(Pipeline.new("p", [str(src)]))
    _, out, _ = run(capsys, brain, "sync", "p")
    assert out.splitlines()[0] == "p: ok, 2 warnings"
