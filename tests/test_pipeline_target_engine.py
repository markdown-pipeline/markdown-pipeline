"""MD-78: every engine behaviour runs per pipeline against that pipeline's own target."""
import json

from conftest import write
from fake_github import FakeGitHub
from mdpipeline import github_api
from mdpipeline.brain import init_brain
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.destinations import bind_target
from mdpipeline.engine import sync_all, sync_pipeline
from mdpipeline.github_target import STATE_PATH, GitHubTarget
from mdpipeline.state import State
from mdpipeline.target import LocalTarget


def add(brain, name, src, **rules):
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new(name, [str(src)], **rules))
    save_config(brain, cfg)


def test_a_pipeline_without_a_destination_uses_the_brains_own_target(brain, make_source):
    add(brain, "p", make_source("d", {"a.md": "A"}))
    bound = bind_target(brain, load_config(brain).get("p"))
    assert bound is brain and isinstance(bound.target, LocalTarget)


def test_a_directory_destination_is_synced_into_that_directory_with_its_own_state(brain, brain_dir, make_source, tmp_path):
    other = tmp_path / "vault"
    other.mkdir()
    add(brain, "p", make_source("d", {"a.md": "A"}), destination={"type": "directory", "path": str(other)})
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["created"] == 1
    assert (other / "raw/p/d/a.md").is_file() and list((brain_dir / "raw").iterdir()) == []
    assert "raw/p/d/a.md" in json.loads((other / ".mdpipeline/state.json").read_text())["pipelines"]["p"]["files"]
    assert not (brain_dir / ".mdpipeline/state.json").exists()


def test_a_github_destination_commits_to_the_repo_and_keeps_the_state_there(gh):
    gh.pipeline("p", {"a.md": "A"})
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and gh.fake.read("raw/p/p/a.md") is not None and gh.fake.read(STATE_PATH) is not None
    assert isinstance(bind_target(gh.brain, load_config(gh.brain).get("p")).target, GitHubTarget)


def test_pipelines_with_different_targets_sync_independently_and_one_failing_target_changes_nothing_in_the_other(gh, make_source, tmp_path):
    local_src = make_source("loc", {"l.md": "L"})
    add(gh.brain, "local", local_src)
    gh.pipeline("remote", {"r.md": "R"})
    gh.fake.fail("GET", "/git/ref/heads", status=502, times=99)       # the repo is down
    results = {r.pipeline: r for r in sync_all(gh.brain, trigger="cli")}
    assert results["remote"].result == "error" and results["remote"].category == "network"
    assert results["local"].result == "ok" and (gh.brain.root / "raw/local/loc/l.md").is_file()
    assert gh.fake.read("raw/remote/remote/r.md") is None


def test_two_pipelines_sharing_a_github_target_share_one_state_and_commit_separately(gh):
    gh.pipeline("a", {"x.md": "X"})
    gh.pipeline("b", {"y.md": "Y"})
    sync_pipeline(gh.brain, "a", trigger="cli")
    sync_pipeline(gh.brain, "b", trigger="cli")
    files = json.loads(gh.fake.read(STATE_PATH))["pipelines"]
    assert set(files) == {"a", "b"} and list(files["a"]["files"]) == ["raw/a/a/x.md"] and list(files["b"]["files"]) == ["raw/b/b/y.md"]


def test_state_is_read_through_the_pipelines_own_target(gh, make_source):
    add(gh.brain, "local", make_source("loc", {"l.md": "L"}))
    gh.pipeline("remote", {"r.md": "R"})
    sync_all(gh.brain, trigger="cli")
    local_state = State.load(bind_target(gh.brain, load_config(gh.brain).get("local")))
    remote_state = State.load(bind_target(gh.brain, load_config(gh.brain).get("remote")))
    assert set(local_state.pipelines) == {"local"} and set(remote_state.pipelines) == {"remote"}


def test_a_migrated_legacy_github_config_plans_zero_changes_and_keeps_names(gh):
    """The state in the repo was written under the brain-level target model; after the migration the first plan changes nothing."""
    import yaml

    gh.pipeline("p", {"a.md": "A", "sub/b.md": "B"}, name_policy="date-name")
    assert sync_pipeline(gh.brain, "p", trigger="cli").result == "ok"
    names = sorted(p for p in gh.fake.files() if p.startswith("raw/"))
    modern = yaml.safe_load(gh.brain.config_path.read_text())
    legacy = {"version": 1, "target": modern["pipelines"][0].pop("destination"), "pipelines": modern["pipelines"]}
    gh.brain.config_path.write_text(yaml.safe_dump(legacy, sort_keys=False))      # what a pre-MD-78 install has on disk
    gh.brain._cache.clear()
    res = sync_pipeline(gh.brain, "p", trigger="cli", dry_run=True)
    c = res.counts()
    assert (c["created"], c["updated"], c["renamed"], c["deleted"], c["conflicts"]) == (0, 0, 0, 0, 0) and c["unchanged"] == 2
    assert sorted(p for p in gh.fake.files() if p.startswith("raw/")) == names
    assert (gh.brain.config_dir / "backups" / "config-before-per-pipeline-target.yaml").is_file()
