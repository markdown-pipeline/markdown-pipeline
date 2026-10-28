"""MD-78 / MD-80: the target is chosen per pipeline in the CLI; the brain-level commands point to the new place."""
import json

from mdpipeline.cli import main
from mdpipeline.config import load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import FileRecord, State


def run(capsys, *argv, brain=None):
    code = main((["--brain", str(brain.root)] if brain else []) + list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_init_github_points_to_the_pipeline_commands_and_saves_nothing(tmp_path, capsys):
    code, out, err = run(capsys, "init", str(tmp_path / "x"), "--github", "acme/brain")
    assert code == 2 and "pipeline add" in err and "--github" in err and not (tmp_path / "x").exists()


def test_the_old_target_command_points_to_the_pipeline_commands(brain, capsys):
    code, out, err = run(capsys, "target", "test", brain=brain)
    assert code == 2 and "pipeline test-target" in err


def test_init_create_makes_the_folder_but_not_raw(tmp_path, capsys):
    code, out, err = run(capsys, "init", str(tmp_path / "fresh"), "--create")
    assert code == 0 and not (tmp_path / "fresh/raw").exists() and (tmp_path / "fresh/.mdpipeline/config.yaml").is_file()


def test_pipeline_add_with_a_directory_target_saves_it_and_syncs_there(brain, make_source, tmp_path, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    src = make_source("docs", {"a.md": "A"})
    code, out, err = run(capsys, "pipeline", "add", "p", "--source", str(src), "--directory", str(vault), brain=brain)
    assert code == 0, err
    assert load_config(brain).get("p").destination == {"type": "directory", "path": str(vault)}
    assert sync_pipeline(brain, "p").result == "ok" and list(vault.glob("raw/*a.md"))


def test_pipeline_list_shows_each_pipelines_target(brain, make_source, tmp_path, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    run(capsys, "pipeline", "add", "here", "--source", str(make_source("a", {"a.md": "A"})), brain=brain)
    run(capsys, "pipeline", "add", "there", "--source", str(make_source("b", {"b.md": "B"})), "--directory", str(vault), brain=brain)
    code, out, _ = run(capsys, "pipeline", "list", "--json", brain=brain)
    rows = {r["name"]: r for r in json.loads(out)}
    assert rows["here"]["destination"]["path"] == str(brain.root) and rows["there"]["destination"]["path"] == str(vault)
    code, text, _ = run(capsys, "pipeline", "list", brain=brain)
    assert f"directory {vault}" in text and f"directory {brain.root}" in text


def test_status_names_the_target_of_every_pipeline(brain, make_source, tmp_path, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    run(capsys, "pipeline", "add", "there", "--source", str(make_source("b", {"b.md": "B"})), "--directory", str(vault), brain=brain)
    code, out, _ = run(capsys, "status", brain=brain)
    assert code == 0 and f"directory {vault}" in out
    code, out, _ = run(capsys, "status", "--json", brain=brain)
    assert json.loads(out)["pipelines"][0]["target"]["path"] == str(vault)


def test_state_restore_names_the_target_when_pipelines_use_several(brain, make_source, tmp_path, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    run(capsys, "pipeline", "add", "here", "--source", str(make_source("a", {"a.md": "A"})), brain=brain)
    run(capsys, "pipeline", "add", "there", "--source", str(make_source("b", {"b.md": "B"})), "--directory", str(vault), brain=brain)
    code, _, err = run(capsys, "state", "restore", brain=brain)
    assert code == 2 and "--pipeline" in err
