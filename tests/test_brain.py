"""MD-7: register a directory as the brain."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mdpipeline.brain import BrainError, find_brain, init_brain


def snapshot(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_init_in_dir_with_raw_creates_human_readable_config_inside_brain(brain_dir):
    brain = init_brain(brain_dir)
    assert brain.config_path == brain_dir.resolve() / ".mdpipeline" / "config.yaml"
    data = yaml.safe_load(brain.config_path.read_text())
    assert data == {"version": 1, "pipelines": []}


def test_init_without_raw_works_and_does_not_create_it(tmp_path):
    """MD-133: any folder is a valid output folder."""
    root = tmp_path / "norawbrain"
    root.mkdir()
    (root / "notes.md").write_text("x")
    brain = init_brain(root)
    assert brain.config_path.is_file() and not (root / "raw").exists() and (root / "notes.md").read_text() == "x"


def test_init_on_missing_directory_fails_and_creates_nothing(tmp_path):
    with pytest.raises(BrainError, match="does not exist"):
        init_brain(tmp_path / "nope")
    assert not (tmp_path / "nope").exists()


def test_init_does_not_care_what_raw_is(tmp_path):
    """MD-133: raw/ is not part of what makes a folder usable (a pipeline that writes there reports the problem when it writes)."""
    root = tmp_path / "b"
    root.mkdir()
    (root / "raw").write_text("not a dir")
    assert init_brain(root).config_path.is_file()


def test_registering_twice_does_not_reset_configuration(brain_dir, make_source):
    brain = init_brain(brain_dir)
    brain.config_path.write_text(
        "version: 1\npipelines:\n- name: keepme\n  sources: []\n# a human comment\n"
    )
    before = brain.config_path.read_bytes()
    again = init_brain(brain_dir)
    assert again.config_path.read_bytes() == before


def test_config_lives_in_the_brain_so_two_brains_are_independent(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for r in (a, b):
        (r / "raw").mkdir(parents=True)
    ba, bb = init_brain(a), init_brain(b)
    assert ba.config_path != bb.config_path
    assert ba.config_path.parent.parent == a.resolve()
    assert bb.config_path.parent.parent == b.resolve()


def test_runtime_files_are_gitignored_but_config_is_not(brain_dir):
    brain = init_brain(brain_dir)
    ignore = (brain.config_dir / ".gitignore").read_text().split()
    assert "state.json" in ignore and "log.jsonl" in ignore and "lock" in ignore
    assert "config.yaml" not in ignore


def test_find_brain_walks_up_from_a_subdirectory(brain_dir):
    init_brain(brain_dir)
    found = find_brain(brain_dir / "wiki")
    assert found.root == brain_dir.resolve()


def test_find_brain_without_registration_raises(tmp_path):
    with pytest.raises(BrainError, match="init"):
        find_brain(tmp_path)
