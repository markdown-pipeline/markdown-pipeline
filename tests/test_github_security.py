"""MD-65: GitHub target write boundary, credentials and the network rule."""
import json
import re
from pathlib import Path

import pytest

from conftest import write
from mdpipeline import github_api
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.github_target import STATE_PATH
from test_mcp_server import payload, session


def allowed(path: str) -> bool:
    return path == STATE_PATH or (path.startswith("raw/") and path.endswith(".md") and ".." not in path.split("/"))


@pytest.mark.parametrize("template", ["../evil/{relpath}", "raw/../wiki/{relpath}", "wiki/{relpath}", ".mdpipeline/{relpath}", ".github/{relpath}",
                                      "raw/{pipeline}/../../wiki/{relpath}", "raw/{pipeline}/{name}.txt", ".mdpipeline/state.json"])
def test_hostile_templates_never_change_anything_but_raw_and_state(gh, template):
    gh.pipeline("p", {"a.md": "A"}, target=template)
    before = gh.fake.files()
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "error"
    assert gh.fake.files() == before and len(gh.fake.history()) == 1


@pytest.mark.parametrize("template", ["{relpath}", "/abs/{name}.md", "docs/{relpath}"])
def test_root_templates_commit_only_pipeline_markdown_inside_the_repo_and_the_state_path(gh, template):
    """MD-133: no forced raw/. A template at the repo root writes there; wiki/ and the state path are still the only other things a commit touches."""
    from mdpipeline.boundary import check_write_target

    gh.pipeline("p", {"a.md": "A"}, target=template)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["created"] == 1
    changed = set()
    for sha in gh.fake.history()[1:]:
        changed |= set(gh.fake.commit_files_changed(sha))
    assert changed and all(p == STATE_PATH or check_write_target(p) is None for p in changed), changed
    assert gh.fake.read("wiki/w.md") == b"W"


def test_a_readme_in_the_repo_is_a_conflict_not_an_overwrite(gh):
    gh.fake.commit_as_other({"README.md": b"mine"})
    gh.pipeline("p", {"README.md": "from the source"}, target="{relpath}")
    sync_pipeline(gh.brain, "p", trigger="cli")
    assert gh.fake.read("README.md") == b"mine"


@pytest.mark.parametrize("name,label", [("../wiki", "docs"), ("p", "../wiki"), ("p", "a/../../wiki"), ("wiki", "docs")])
def test_hostile_pipeline_names_and_labels_never_leave_raw(gh, name, label):
    src = gh.tmp / "sources" / "x"
    write(src / "a.md", "A")
    cfg = load_config(gh.brain)
    cfg.pipelines.append(Pipeline.new(name, [Source(str(src), label)]))
    save_config(gh.brain, cfg)
    sync_pipeline(gh.brain, name, trigger="cli")
    for sha in gh.fake.history()[1:]:
        assert all(allowed(p) for p in gh.fake.commit_files_changed(sha)), gh.fake.commit_files_changed(sha)
    assert gh.fake.read("wiki/w.md") == b"W"


def test_every_commit_only_touches_raw_markdown_and_the_state_path(gh):
    src = gh.pipeline("p", {"a.md": "A", "sub/b.md": "B"}, guard_threshold_percent=100)
    sync_pipeline(gh.brain, "p", trigger="cli")
    (src / "a.md").write_text("A2")
    (src / "sub" / "b.md").unlink()
    sync_pipeline(gh.brain, "p", trigger="cli")
    for sha in gh.fake.history()[1:]:
        assert all(allowed(p) for p in gh.fake.commit_files_changed(sha))


def all_outputs_and_artifacts(gh, outputs):
    """Everything the token could leak into: the repo (every commit's files and message), the control dir, outputs."""
    found = []
    for blob in gh.fake._blobs.values():
        found.append(blob.decode(errors="replace"))
    found += [c["message"] for c in gh.fake._commits.values()]
    for path in Path(gh.brain.root).rglob("*"):
        if path.is_file():
            found.append(path.read_text(errors="replace"))
            found.append(str(path))
    found += outputs
    return found


def test_the_token_never_appears_in_the_repo_state_log_backups_or_any_output(gh, capsys, monkeypatch):
    src = gh.pipeline("p", {"a.md": "A", "b.md": "B"}, guard_threshold_percent=100)
    outputs = []

    def cli(*argv):
        code = main(["--brain", str(gh.brain.root), *argv])
        out = capsys.readouterr()
        outputs.extend([out.out, out.err])
        return code

    cli("target", "test")
    cli("sync", "p", "--dry-run")
    cli("sync", "p")
    cli("sync", "p", "--json")
    (src / "a.md").write_text("A2")
    cli("sync", "p")
    cli("status")
    cli("status", "--json")
    cli("pipeline", "list")
    cli("log")
    cli("log", "--json")
    gh.fake.fail("POST", "/git/blobs", status=500, times=1)
    (src / "c.md").write_text("C")
    cli("sync", "p")
    gh.fake.rate_limit(times=1)
    cli("sync", "p")
    monkeypatch.setenv("BRAIN_TOKEN", "ghp_WRONGWRONGWRONG")
    gh.brain._cache.clear()
    cli("sync", "p")
    cli("target", "test")
    monkeypatch.setenv("BRAIN_TOKEN", gh.token)
    gh.brain._cache.clear()

    async def fn(c):
        return json.dumps(payload(await c.call_tool("sync_status", {})))

    outputs.append(session(gh.brain, fn))
    for text in all_outputs_and_artifacts(gh, outputs):
        assert gh.token not in text
    assert any("ghp_WRONG" not in t for t in outputs)
    assert not any("ghp_WRONG" in t for t in all_outputs_and_artifacts(gh, outputs))


def test_logs_never_contain_file_content(gh):
    gh.pipeline("p", {"a.md": "TOP-SECRET-CONTENT-ABC"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    log = (gh.brain.config_dir / "log.jsonl").read_text()
    assert "TOP-SECRET-CONTENT-ABC" not in log


def test_all_requests_are_https_to_the_configured_host_only(gh):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    assert gh.fake.requests and all(p.startswith("/repos/acme/brain") or p == "/user" for _, p in gh.fake.requests)


def test_a_local_only_brain_with_folder_sources_makes_zero_network_calls(brain, add_pipeline, make_source, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("network used")

    github_api.set_transport(boom)
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: calls.append(a))
    monkeypatch.setattr("urllib.request.urlopen", boom)
    try:
        add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
        assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    finally:
        github_api.set_transport(None)
    assert calls == []


def test_the_config_never_stores_a_token_literal(gh, tmp_path):
    gh.pipeline("p", {"a.md": "A"})
    cfg = (gh.brain.config_path).read_text()
    assert gh.token not in cfg and "env:BRAIN_TOKEN" in cfg
