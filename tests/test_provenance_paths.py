"""MD-72: a GitHub target never commits a machine path (provenance source_root); existing files are migrated once, on request."""
import json

import pytest

from conftest import write
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.github_target import STATE_PATH
from mdpipeline.rebuild import apply_rebuild, rebuild_preview
from mdpipeline.state import State
from secret_search import repo_containing


def provenance(fake, path):
    return parse_document(fake.read(path).decode())[0]["provenance"]


def repo_md(fake):
    return [p for p in fake.files() if p.startswith("raw/") and p.endswith(".md")]


def test_a_github_sync_of_a_folder_commits_a_stable_label_instead_of_the_machine_path(gh):
    src = gh.pipeline("p", {"a.md": "A", "sub/b.md": "B"})
    assert sync_pipeline(gh.brain, "p", trigger="cli").result == "ok"
    for path in repo_md(gh.fake):
        assert provenance(gh.fake, path)["source_root"] == f"local:{src.name}"
    assert repo_containing(str(gh.tmp), gh.fake) == [] and repo_containing("/home/", gh.fake) == [] and repo_containing(str(src), gh.fake) == []


def test_local_targets_keep_the_absolute_path(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    text = next((brain.root / "raw").rglob("a.md")).read_text()
    assert f"source_root: {src}" in text


def test_a_changed_source_file_is_rewritten_with_the_label(gh):
    src = gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    (src / "a.md").write_text("A2")
    sync_pipeline(gh.brain, "p", trigger="cli")
    assert provenance(gh.fake, repo_md(gh.fake)[0])["source_root"] == f"local:{src.name}"


def legacy_repo(gh):
    """A repository written before the change: every owned file carries the absolute source folder."""
    src = gh.pipeline("p", {"a.md": "A", "b.md": "B"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    edits = {}
    for path in repo_md(gh.fake):
        edits[path] = gh.fake.read(path).decode().replace(f"source_root: local:{src.name}", f"source_root: {src}").encode()
    changed = gh.fake.commit_as_other(edits, "legacy provenance")
    state = json.loads(gh.fake.read(STATE_PATH))
    from mdpipeline.frontmatter import content_hash

    for path, data in edits.items():
        state["pipelines"]["p"]["files"][path]["written_hash"] = content_hash(data)
    gh.fake.commit_as_other({STATE_PATH: json.dumps(state, indent=1, sort_keys=True).encode()}, "state follows")
    return src


def test_existing_files_are_not_churned_by_a_normal_sync(gh):
    src = legacy_repo(gh)
    head = gh.fake.head()
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and res.commit is None and gh.fake.head() == head
    assert provenance(gh.fake, repo_md(gh.fake)[0])["source_root"] == str(src)


def test_the_one_time_scrub_shows_only_a_provenance_change_explains_it_and_converges(gh):
    src = legacy_repo(gh)
    plan = sync_pipeline(gh.brain, "p", trigger="cli", dry_run=True, scrub_paths=True)
    assert plan.counts()["updated"] == 2 and plan.plan.metadata_only == 2 and plan.counts()["conflicts"] == 0
    assert any("machine path" in n for n in plan.plan.notes) and not plan.plan.guard.blocked
    assert sync_pipeline(gh.brain, "p", trigger="cli", dry_run=True, scrub_paths=True).plan.guard.count == 0
    res = sync_pipeline(gh.brain, "p", trigger="cli", scrub_paths=True)
    assert res.result == "ok" and res.commit is not None
    for path in repo_md(gh.fake):
        meta, body = parse_document(gh.fake.read(path).decode())
        assert meta["provenance"]["source_root"] == f"local:{src.name}" and body.strip() in ("A", "B")
    assert not any(str(src).encode() in data for data in gh.fake.files().values())          # the head of the branch holds no machine path any more
    again = sync_pipeline(gh.brain, "p", trigger="cli", scrub_paths=True)
    assert again.commit is None and again.counts()["updated"] == 0


def test_the_scrub_never_overwrites_a_file_someone_edited(gh):
    src = legacy_repo(gh)
    path = repo_md(gh.fake)[0]
    gh.fake.commit_as_other({path: gh.fake.read(path) + b"\nteammate edit\n"}, "edit")
    res = sync_pipeline(gh.brain, "p", trigger="cli", scrub_paths=True)
    assert res.counts()["conflicts"] == 1 and b"teammate edit" in gh.fake.read(path)


def test_the_cli_scrub_command_shows_the_plan_then_applies(gh, capsys):
    src = legacy_repo(gh)
    code = main(["--brain", str(gh.brain.root), "pipeline", "scrub-paths", "p"])
    out = capsys.readouterr().out
    assert code == 0 and "machine path" in out and "updated 2" in out and "Nothing was changed" in out
    assert provenance(gh.fake, repo_md(gh.fake)[0])["source_root"] == str(src)
    code = main(["--brain", str(gh.brain.root), "pipeline", "scrub-paths", "p", "--apply"])
    assert code == 0 and provenance(gh.fake, repo_md(gh.fake)[0])["source_root"] == f"local:{src.name}"


def test_the_scrub_only_applies_to_a_github_target(brain, add_pipeline, make_source, capsys):
    add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
    code = main(["--brain", str(brain.root), "pipeline", "scrub-paths", "p"])
    assert code == 2 and "GitHub" in capsys.readouterr().err


def test_rebuild_from_provenance_works_for_a_github_target_with_label_roots(gh):
    gh.pipeline("p", {"a.md": "A", "b.md": "B"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    gh.fake.commit_as_other({STATE_PATH: json.dumps({"version": 1, "pipelines": {}}).encode()}, "lose the state")
    gh.brain._cache.clear()
    preview = rebuild_preview(gh.brain, "p")
    assert preview.claimed_total == 2 and preview.unclaimed == []
