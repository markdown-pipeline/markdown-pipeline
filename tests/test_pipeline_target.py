"""MD-78: the target (directory or GitHub repo) is part of each pipeline; the brain-level target is gone and migrates."""
import json

import pytest
import yaml

from conftest import write
from mdpipeline.config import Config, Pipeline, load_config, save_config

GH = {"type": "github", "repo": "acme/brain", "branch": "main", "credential": "env:BRAIN_TOKEN"}


def test_a_pipeline_without_a_destination_lands_in_the_brain_directory(make_source):
    p = Pipeline.new("p", [str(make_source("d", {"a.md": "A"}))])
    assert p.destination is None and "destination" not in p.to_dict()
    assert Pipeline.from_dict(p.to_dict()).destination is None


def test_a_destination_round_trips_through_the_config_file(brain, make_source):
    cfg = Config([Pipeline.new("p", [str(make_source("d"))], destination=dict(GH)),
                  Pipeline.new("q", [str(make_source("e"))], destination={"type": "directory", "path": "/tmp/elsewhere"})])
    save_config(brain, cfg)
    loaded = load_config(brain)
    assert loaded.get("p").destination == GH and loaded.get("q").destination == {"type": "directory", "path": "/tmp/elsewhere"}
    assert "target" not in yaml.safe_load(brain.config_path.read_text())      # no brain-level target any more


LEGACY = """version: 1
target:
  type: github
  repo: acme/brain
  branch: main
  credential: env:BRAIN_TOKEN
  clone: /home/me/clones/brain
pipelines:
- name: notes
  sources:
  - path: /home/me/notes
    label: notes
  target: raw/{pipeline}/{source}/{relpath}
  include: ['**/*.md']
  exclude: ['.git/**']
  on_source_delete: delete
  guard: {threshold_percent: 40, min_files: 5}
  paused: false
- name: specs
  sources:
  - path: /home/me/specs
    label: specs
  target: raw/{pipeline}/{source}/{relpath}
  include: ['**/*.md']
  exclude: ['.git/**']
  on_source_delete: keep
  guard: {threshold_percent: 40, min_files: 5}
  paused: false
  name_policy: date_slug
"""


def test_a_legacy_brain_level_target_becomes_each_pipelines_destination_after_a_backup(brain):
    brain.config_path.write_text(LEGACY)
    cfg = load_config(brain)
    expected = {**GH, "clone": "/home/me/clones/brain"}
    assert [p.destination for p in cfg.pipelines] == [expected, expected]
    saved = yaml.safe_load(brain.config_path.read_text())
    assert "target" not in saved and all(p["destination"] == expected for p in saved["pipelines"])
    backups = list((brain.config_dir / "backups").glob("config-before-*.yaml"))
    assert len(backups) == 1 and backups[0].read_text() == LEGACY
    assert cfg.get("specs").name_policy == "date_slug" and cfg.get("specs").on_source_delete == "keep"


def test_the_migration_runs_once_and_never_overwrites_the_first_backup(brain):
    brain.config_path.write_text(LEGACY)
    load_config(brain)
    before = brain.config_path.read_text()
    load_config(brain)
    assert brain.config_path.read_text() == before
    assert len(list((brain.config_dir / "backups").glob("config-before-*.yaml"))) == 1


def test_a_legacy_local_config_without_a_target_needs_no_migration_and_no_backup(brain, make_source):
    save_config(brain, Config([Pipeline.new("p", [str(make_source("d"))])]))
    text = brain.config_path.read_text()
    load_config(brain)
    assert brain.config_path.read_text() == text and not (brain.config_dir / "backups").exists()


# --- validation of the target (MD-78) ---------------------------------------------------------------------------------
from mdpipeline.validation import validate_pipeline


def codes(result):
    return {i.code for i in result.errors}


def test_a_directory_target_must_be_an_existing_folder(brain, make_source, tmp_path):
    p = Pipeline.new("p", [str(make_source("d", {"a.md": "A"}))], destination={"type": "directory", "path": str(tmp_path / "nope")})
    assert "target-not-a-folder" in codes(validate_pipeline(brain, load_config(brain), p))


def test_a_source_inside_the_target_raw_folder_is_refused(brain, tmp_path):
    vault = tmp_path / "vault"
    write(vault / "raw/in/a.md", "A")
    p = Pipeline.new("p", [str(vault / "raw/in")], destination={"type": "directory", "path": str(vault)})
    assert "source-in-output" in codes(validate_pipeline(brain, load_config(brain), p))


def test_a_source_that_contains_the_target_folder_is_refused(brain, tmp_path):
    outer = tmp_path / "outer"
    write(outer / "a.md", "A")
    (outer / "vault").mkdir()
    p = Pipeline.new("p", [str(outer)], destination={"type": "directory", "path": str(outer / "vault")})
    assert "source-contains-target" in codes(validate_pipeline(brain, load_config(brain), p))


def test_an_unknown_target_type_is_refused(brain, make_source):
    p = Pipeline.new("p", [str(make_source("d", {"a.md": "A"}))], destination={"type": "ftp"})
    assert "bad-target-type" in codes(validate_pipeline(brain, load_config(brain), p))


def test_a_malformed_github_repo_is_refused_before_any_network_call(brain, make_source):
    p = Pipeline.new("p", [str(make_source("d", {"a.md": "A"}))], destination={"type": "github", "repo": "not a repo", "branch": "main"})
    assert "bad-repo" in codes(validate_pipeline(brain, load_config(brain), p))


def test_two_pipelines_with_one_target_cannot_claim_the_same_path_and_the_message_names_both(brain, make_source):
    cfg = Config([Pipeline.new("one", [str(make_source("d1", {"a.md": "A"}))], target="raw/shared/{name}", name_policy="original")])
    save_config(brain, cfg)
    p = Pipeline.new("two", [str(make_source("d2", {"a.md": "B"}))], target="raw/shared/{name}", name_policy="original")
    res = validate_pipeline(brain, load_config(brain), p)
    clash = next(i for i in res.errors if i.code == "target-collision")
    assert "one/" in clash.message and "two/" in clash.message


def test_pipelines_with_different_targets_may_use_the_same_path(brain, make_source, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = Config([Pipeline.new("one", [str(make_source("d1", {"a.md": "A"}))], target="raw/shared/{name}", name_policy="original")])
    save_config(brain, cfg)
    p = Pipeline.new("two", [str(make_source("d2", {"a.md": "B"}))], target="raw/shared/{name}", name_policy="original",
                     destination={"type": "directory", "path": str(vault)})
    assert not any(i.code == "target-collision" for i in validate_pipeline(brain, load_config(brain), p).errors)
