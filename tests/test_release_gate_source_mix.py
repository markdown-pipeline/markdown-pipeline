"""MD-102: the release gate of source choice (SC-S4): any mix of sources, Jira only, switching, Off/On. Run alone with:

    uv run pytest tests/test_release_gate_source_mix.py

Everything runs against the fake Jira and local folders: no real network. Together with tests/test_data_loss.py (MD-30), tests/test_data_loss_names.py
(MD-41), tests/test_data_loss_jira.py (MD-55), tests/test_data_loss_github.py (MD-69), tests/test_release_gate_p31.py (MD-89) and
tests/test_release_gate_view_mode.py (MD-96) this is the gate. Scenario numbers match the acceptance criteria of MD-102.
"""
import json
import os
import socket

import pytest

from conftest import tree_snapshot, write
from fake_jira import FakeJira
from mdpipeline import credentials, jira_api
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.jira_source import JiraSource
from mdpipeline.state import State
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source
from uihelper import running_ui

JIRA_ARGS = ["--jira-label", "md-jira", "--jira-site", SITE, "--jira-jql", "project = MD", "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN"]
JIRA_DRAFT = {"type": "jira", "label": "md-jira", "site": SITE, "jql": "project = MD", "email_ref": "env:JIRA_EMAIL", "token_ref": "env:JIRA_TOKEN"}


class RecordingJira:
    """The fake Jira behind a transport that also records every full URL (scheme and host included)."""

    def __init__(self, fake):
        self.fake, self.urls = fake, []

    def __call__(self, method, url, headers, body):
        self.urls.append(url)
        return self.fake(method, url, headers, body)


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(4):
        f.add_issue("MD", f"issue {n}")
    f.add_issue("MP", "other project")
    f.recorder = RecordingJira(f)
    jira_api.set_transport(f.recorder, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)
    JiraSource.page_size = 100


def cli(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out + out.err


def draft(sources, **kw):
    d = {"name": "p", "sources": sources, "target": "raw/{pipeline}/{source}/{relpath}", "include": ["**/*.md"], "exclude": [".git/**"],
         "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5, "name_policy": "original"}
    d.update(kw)
    return d


def raw_md(brain, sub=""):
    """The Markdown files the pipelines wrote (MD-133: at the root of the output folder; the app's own folder is not content)."""
    base = brain.root / "raw" / sub if sub else brain.root          # a sub-path is relative to raw/ (explicit raw/ templates); none = the whole output folder
    return sorted(str(p.relative_to(brain.root)) for p in base.rglob("*.md") if ".mdpipeline" not in p.parts and "wiki" not in p.relative_to(brain.root).parts[:1])


def content_snapshot(brain):
    return {rel: (brain.root / rel).read_bytes() for rel in raw_md(brain)}


def owned(brain):
    return sorted(State.load(brain).pipeline("p").files)


@pytest.fixture
def folder_and_jira(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": f"doc {i}" for i in range(8)})
    add_pipeline(Pipeline("p", [Source(str(docs), "docs"), jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=40))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    return type("W", (), {"brain": brain, "docs": docs, "fake": fake})


# 1 -----------------------------------------------------------------------------------------------------------------------
def test_01_a_jira_only_pipeline_lifecycle(brain, fake, capsys):
    code, out = cli(capsys, brain, "pipeline", "add", "p", *JIRA_ARGS, "--name-policy", "original", "--guard-percent", "100")
    assert code == 0, out
    assert [s.type for s in load_config(brain).get("p").sources] == ["jira"]
    first = sync_pipeline(brain, "p", trigger="cli")
    assert first.result == "ok" and first.counts()["created"] == 4 and len(raw_md(brain)) == 4
    snapshot = content_snapshot(brain)
    again = sync_pipeline(brain, "p", trigger="cli")                                  # incremental, nothing changed: 0 writes
    assert again.counts()["created"] + again.counts()["updated"] + again.counts()["deleted"] == 0 and again.counts()["unchanged"] == 4
    assert content_snapshot(brain) == snapshot
    fake.delete_issue("MD-4")                                                         # deleted in Jira: on-delete = delete
    assert sync_pipeline(brain, "p", trigger="cli").counts()["deleted"] == 1 and len(raw_md(brain)) == 3
    fake.move_issue("MD-3", "OTHER")                                                  # out of scope: the same as a deletion
    assert sync_pipeline(brain, "p", trigger="cli").counts()["deleted"] == 1 and len(raw_md(brain)) == 2


def test_01_on_delete_keep_keeps_what_left_jira(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source(label="md-jira")], name_policy="original", on_source_delete="keep", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    fake.delete_issue("MD-1")
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.counts()["deleted"] == 0 and res.counts()["kept"] == 1 and len(raw_md(brain)) == 4


@pytest.mark.parametrize("failure", ["auth", "outage", "rate-limit", "partial-pages"])
def test_01_auth_failure_outage_and_partial_results_delete_nothing(brain, fake, add_pipeline, failure):
    add_pipeline(Pipeline("p", [jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    before = tree_snapshot(brain.root / "raw")
    if failure == "auth":
        fake.fail("GET", "/search/jql", status=401, times=9)
    elif failure == "outage":
        fake.fail("GET", "/search/jql", status=503, times=9)
    elif failure == "rate-limit":
        fake.rate_limit(times=99)
    else:
        JiraSource.page_size = 1
        fake.fail_page(3)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and res.counts()["deleted"] == 0
    assert tree_snapshot(brain.root / "raw") == before and len(owned(brain)) == 4


# 2 -----------------------------------------------------------------------------------------------------------------------
def test_02_removing_the_folder_deletes_nothing_by_default_in_the_cli_and_the_ui(folder_and_jira, capsys):
    w = folder_and_jira
    docs_before = tree_snapshot(w.brain.root / "raw/p/docs")
    code, out = cli(capsys, w.brain, "pipeline", "edit", "p", "--remove-source", "docs")
    assert code == 0, out
    assert tree_snapshot(w.brain.root / "raw/p/docs") == docs_before and not any("/docs/" in t for t in owned(w.brain))     # unowned, reported
    assert "8 files kept" in out and "unowned" in out


def test_02_the_ui_switch_to_jira_only_deletes_nothing_by_default(folder_and_jira):
    w = folder_and_jira
    docs_before = tree_snapshot(w.brain.root / "raw/p/docs")
    with running_ui(w.brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft([JIRA_DRAFT]), "remove_folders": [{"label": "docs", "delete_files": False}]})
    assert status == 200, res
    assert tree_snapshot(w.brain.root / "raw/p/docs") == docs_before and [s.type for s in load_config(w.brain).get("p").sources] == ["jira"]


def test_02_with_the_opt_in_exactly_those_files_go_after_the_plan_and_through_the_guard(folder_and_jira, capsys):
    w = folder_and_jira
    jira_files = [t for t in owned(w.brain) if "/md-jira/" in t]
    code, out = cli(capsys, w.brain, "pipeline", "edit", "p", "--remove-source", "docs", "--delete-files", "--dry-run")      # shown first
    assert code == 0 and "delete 8 files" in out and "blocked" in out and "Nothing was changed" in out and len(owned(w.brain)) == 12
    code, out = cli(capsys, w.brain, "pipeline", "edit", "p", "--remove-source", "docs", "--delete-files")                  # 8 of 12 = 67% > 40%: guard
    assert code == 3 and "--approve" in out and len(raw_md(w.brain, "p/docs")) == 8
    code, out = cli(capsys, w.brain, "pipeline", "edit", "p", "--remove-source", "docs", "--delete-files", "--approve")
    assert code == 0 and raw_md(w.brain, "p/docs") == [] and owned(w.brain) == sorted(jira_files)
    assert len(raw_md(w.brain, "p/md-jira")) == 4                                                                            # nothing else touched


def test_02_the_ui_opt_in_goes_through_the_guard_too(folder_and_jira):
    w = folder_and_jira
    body = {"draft": draft([JIRA_DRAFT]), "remove_folders": [{"label": "docs", "delete_files": True}]}
    with running_ui(w.brain) as ui:
        status, res = ui.put("/api/pipelines/p", body)
        assert status == 409 and res["result"] == "blocked" and res["guard"]["count"] == 8 and len(raw_md(w.brain, "p/docs")) == 8
        status, _ = ui.put("/api/pipelines/p", {**body, "approve": True})
    assert status == 200 and raw_md(w.brain, "p/docs") == []


# 3 -----------------------------------------------------------------------------------------------------------------------
def test_03_zero_sources_are_refused_by_the_ui_api_and_the_cli_and_no_config_is_written(brain, folder_and_jira, capsys):
    w = folder_and_jira
    before = w.brain.config_path.read_bytes()
    with running_ui(w.brain) as ui:
        assert ui.post("/api/pipelines", {"draft": draft([], name="empty")})[0] == 422
        assert ui.put("/api/pipelines/p", {"draft": draft([])})[0] == 422
        assert ui.put("/api/pipelines/p", {"draft": draft([]), "remove_folders": [{"label": "docs"}, {"label": "md-jira"}]})[0] == 422
    assert cli(capsys, w.brain, "pipeline", "add", "empty")[0] == 2
    cli(capsys, w.brain, "pipeline", "edit", "p", "--remove-source", "docs")
    after_one = w.brain.config_path.read_bytes()
    assert cli(capsys, w.brain, "pipeline", "edit", "p", "--remove-source", "md-jira")[0] == 2        # the last source cannot go
    assert w.brain.config_path.read_bytes() == after_one and load_config(w.brain).get("empty") is None
    assert before != after_one and len(owned(w.brain)) == 4


# 4 -----------------------------------------------------------------------------------------------------------------------
def test_04_a_jira_only_pipeline_calls_only_its_site_over_https(brain, fake, add_pipeline, monkeypatch):
    sockets = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: sockets.append(a))
    add_pipeline(Pipeline("p", [jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    assert fake.recorder.urls and all(u.startswith(SITE + "/rest/api/3/") for u in fake.recorder.urls)
    assert sockets == []                                              # the fake is the transport: nothing else tried to connect


def test_04_folder_only_pipelines_still_make_zero_network_calls(brain, add_pipeline, make_source, monkeypatch):
    calls = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a))
    jira_api.set_transport(lambda *a: calls.append(a))
    try:
        add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
        assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
        assert main(["--brain", str(brain.root), "pipeline", "show", "p"]) == 0
    finally:
        jira_api.set_transport(None)
    assert calls == []


# 5 -----------------------------------------------------------------------------------------------------------------------
def test_05_an_existing_config_loads_with_every_source_on_and_plans_zero_changes(brain, fake, make_source):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(3)})
    brain.config_path.write_text(f"""version: 1
pipelines:
- name: p
  sources:
  - path: {docs}
    label: docs
  - type: jira
    label: md-jira
    path: {SITE}
    options:
      site: {SITE}
      jql: project = MD
      email_ref: env:JIRA_EMAIL
      token_ref: env:JIRA_TOKEN
  target: raw/{{pipeline}}/{{source}}/{{relpath}}
  include: ['**/*.md']
  exclude: ['.git/**']
  on_source_delete: delete
  guard: {{threshold_percent: 40, min_files: 5}}
  paused: false
""")
    assert [s.enabled for s in load_config(brain).get("p").sources] == [True, True]
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    c = sync_pipeline(brain, "p", trigger="cli", dry_run=True).counts()
    assert (c["created"], c["updated"], c["deleted"], c["renamed"], c["conflicts"]) == (0, 0, 0, 0, 0)
    assert "enabled" not in brain.config_path.read_text()                        # nothing rewrote the file


# 6 -----------------------------------------------------------------------------------------------------------------------
def test_06_an_off_folder_is_never_read_and_its_files_never_change(folder_and_jira, monkeypatch, capsys):
    w = folder_and_jira
    assert cli(capsys, w.brain, "pipeline", "edit", "p", "--source-off", "docs")[0] == 0
    docs_before, owned_before = tree_snapshot(w.brain.root / "raw/p/docs"), owned(w.brain)
    real_scandir = os.scandir
    reads = []

    def watching(path=".", *a, **k):
        if str(path).startswith(str(w.docs)):
            reads.append(str(path))
        return real_scandir(path, *a, **k)

    monkeypatch.setattr(os, "scandir", watching)
    write(w.docs / "d0.md", "changed at the source")
    for _ in range(3):
        assert sync_pipeline(w.brain, "p", trigger="cli").result == "ok"
    os.rename(w.docs, str(w.docs) + "-moved")                                        # moved while Off: no effect
    for _ in range(2):
        res = sync_pipeline(w.brain, "p", trigger="cli")
        assert res.result == "ok" and res.counts()["deleted"] == 0
    assert reads == [] and tree_snapshot(w.brain.root / "raw/p/docs") == docs_before and owned(w.brain) == owned_before


def test_06_an_off_jira_source_makes_no_request_and_reads_no_credential(folder_and_jira, monkeypatch, capsys):
    w = folder_and_jira
    assert cli(capsys, w.brain, "pipeline", "edit", "p", "--source-off", "md-jira")[0] == 0
    jira_before, owned_before = tree_snapshot(w.brain.root / "raw/p/md-jira"), owned(w.brain)
    w.fake.recorder.urls.clear()
    boom = lambda ref: (_ for _ in ()).throw(AssertionError("a credential was read"))  # noqa: E731
    monkeypatch.setattr(credentials, "resolve_token", boom)
    monkeypatch.setattr("mdpipeline.jira_source.resolve_token", boom)
    for _ in range(3):
        assert sync_pipeline(w.brain, "p", trigger="cli").result == "ok"
    with running_ui(w.brain) as ui:                                                  # the editor and view mode read nothing either
        for path in ("/api/pipelines/p", "/api/pipelines/p/view", "/api/pipelines/p/plan", "/api/state"):
            assert ui.get(path)[0] == 200
    assert w.fake.recorder.urls == [] and tree_snapshot(w.brain.root / "raw/p/md-jira") == jira_before and owned(w.brain) == owned_before


# 7 -----------------------------------------------------------------------------------------------------------------------
def turn(brain, label, enabled):
    cfg = load_config(brain)
    next(s for s in cfg.get("p").sources if s.label == label).enabled = enabled
    save_config(brain, cfg)


def test_07_back_on_plans_exactly_what_a_normal_sync_would(folder_and_jira):
    w = folder_and_jira
    turn(w.brain, "docs", False)
    write(w.docs / "d0.md", "edited at the source")
    write(w.docs / "new.md", "brand new")
    (w.docs / "d1.md").unlink()
    sync_pipeline(w.brain, "p", trigger="cli")
    turn(w.brain, "docs", True)
    snapshot = tree_snapshot(w.brain.root / "raw")
    plan = sync_pipeline(w.brain, "p", trigger="cli", dry_run=True).counts()
    assert (plan["created"], plan["updated"], plan["deleted"]) == (1, 1, 1)
    assert tree_snapshot(w.brain.root / "raw") == snapshot                            # the plan wrote nothing
    res = sync_pipeline(w.brain, "p", trigger="cli", approve=True)
    assert res.result == "ok" and (w.brain.root / "raw/p/docs/new.md").exists() and not (w.brain.root / "raw/p/docs/d1.md").exists()


def test_07_deletions_follow_on_delete_edits_become_conflicts_and_the_guard_trips(folder_and_jira):
    w = folder_and_jira
    turn(w.brain, "docs", False)
    target = w.brain.root / "raw/p/docs/d2.md"
    target.write_text(target.read_text() + "\nmy note")                              # brain-side edit while Off
    (w.docs / "d2.md").unlink()
    for i in (1, 3, 4, 5, 6, 7):
        (w.docs / f"d{i}.md").unlink()
    turn(w.brain, "docs", True)
    res = sync_pipeline(w.brain, "p", trigger="cli")
    assert res.result == "blocked" and len(raw_md(w.brain, "p/docs")) == 8           # 6 deletions of 12 files = 50% > 40%: nothing applied
    cfg = load_config(w.brain)
    cfg.get("p").guard_threshold_percent = 100
    save_config(w.brain, cfg)
    res = sync_pipeline(w.brain, "p", trigger="cli")
    assert res.result == "conflicts" and "my note" in target.read_text()            # the edited file is never deleted
    assert len(raw_md(w.brain, "p/docs")) == 2                                      # d0 and the edited d2


def test_07_with_on_delete_keep_nothing_is_deleted_when_the_source_comes_back(folder_and_jira):
    w = folder_and_jira
    cfg = load_config(w.brain)
    cfg.get("p").on_source_delete = "keep"
    save_config(w.brain, cfg)
    turn(w.brain, "docs", False)
    for f in w.docs.glob("*.md"):
        f.unlink()
    turn(w.brain, "docs", True)
    res = sync_pipeline(w.brain, "p", trigger="cli")
    assert res.counts()["deleted"] == 0 and len(raw_md(w.brain, "p/docs")) == 8


def test_07_a_jira_source_catches_up_with_everything_that_happened_while_off(folder_and_jira):
    w = folder_and_jira
    cfg = load_config(w.brain)
    cfg.get("p").guard_threshold_percent = 100
    save_config(w.brain, cfg)
    turn(w.brain, "md-jira", False)
    w.fake.delete_issue("MD-1")
    w.fake.add_issue("MD", "brand new while off")
    w.fake.update_issue("MD-2", summary="renamed while off")
    sync_pipeline(w.brain, "p", trigger="cli")
    turn(w.brain, "md-jira", True)
    c = sync_pipeline(w.brain, "p", trigger="cli", dry_run=True).counts()
    assert c["deleted"] == 1 and c["created"] == 1
    assert sync_pipeline(w.brain, "p", trigger="cli").result == "ok"


# 8 -----------------------------------------------------------------------------------------------------------------------
def test_08_all_sources_off_is_refused_by_the_ui_api_and_the_cli(folder_and_jira, capsys):
    w = folder_and_jira
    before = w.brain.config_path.read_bytes()
    both_off = draft([{"path": str(w.docs), "label": "docs", "enabled": False}, {**JIRA_DRAFT, "enabled": False}])
    with running_ui(w.brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": both_off})
        assert status == 422 and "Turn a source on, or pause the pipeline." in json.dumps(res)
        assert ui.post("/api/pipelines", {"draft": {**both_off, "name": "new"}})[0] == 422
    assert cli(capsys, w.brain, "pipeline", "edit", "p", "--source-off", "docs")[0] == 0
    after_one = w.brain.config_path.read_bytes()
    code, out = cli(capsys, w.brain, "pipeline", "edit", "p", "--source-off", "md-jira")
    assert code == 2 and "Turn a source on, or pause the pipeline." in out
    assert w.brain.config_path.read_bytes() == after_one and before != after_one and load_config(w.brain).get("new") is None
