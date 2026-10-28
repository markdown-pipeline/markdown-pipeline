"""MD-20: CLI to register the brain and manage pipelines."""
import json
import subprocess
import sys

import pytest

from mdpipeline.brain import init_brain
from mdpipeline.cli import main
from mdpipeline.config import load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.validation import validate_pipeline


def run(capsys, *argv, brain=None):
    args = (["--brain", str(brain.root)] if brain else []) + list(argv)
    code = main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_init_registers_the_directory_and_is_idempotent(brain_dir, capsys):
    code, out, err = run(capsys, "init", str(brain_dir))
    assert code == 0 and (brain_dir / ".mdpipeline/config.yaml").exists()
    before = (brain_dir / ".mdpipeline/config.yaml").read_bytes()
    code, *_ = run(capsys, "init", str(brain_dir))
    assert code == 0 and (brain_dir / ".mdpipeline/config.yaml").read_bytes() == before


def test_init_without_raw_works_and_creates_no_raw(tmp_path, capsys):
    """MD-133: any folder is a valid output folder."""
    (tmp_path / "plain").mkdir()
    code, out, err = run(capsys, "init", str(tmp_path / "plain"))
    assert code == 0 and not (tmp_path / "plain" / "raw").exists()
    assert sorted(p.name for p in (tmp_path / "plain").iterdir()) == [".mdpipeline"]


def test_commands_outside_a_brain_exit_2_with_a_hint(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "pipeline", "list")
    assert code == 2 and "init" in err


def test_brain_is_found_from_the_working_directory(brain_dir, capsys, monkeypatch):
    init_brain(brain_dir)
    monkeypatch.chdir(brain_dir / "wiki")
    code, out, _ = run(capsys, "pipeline", "list")
    assert code == 0


def test_add_creates_a_pipeline_with_two_labelled_folders_and_defaults(brain, make_source, capsys):
    a, b = make_source("docs", {"x.md": "1"}), make_source("specs", {"y.md": "2"})
    code, out, err = run(capsys, "pipeline", "add", "biz", "--source", str(a), "--source", f"specifications={b}", brain=brain)
    assert code == 0, err
    p = load_config(brain).get("biz")
    assert [(s.label, s.path) for s in p.sources] == [("docs", str(a)), ("specifications", str(b))]
    assert p.target == "raw/{relpath}" and p.on_source_delete == "delete"      # MD-140: the saved default
    assert "biz" in out


def test_add_with_all_rule_options(brain, make_source, capsys):
    a = make_source("docs", {"x.md": "1"})
    code, *_ = run(capsys, "pipeline", "add", "p", "--source", str(a), "--target", "raw/x/{relpath}",
                   "--include", "**/*.md", "--include", "**/*.markdown", "--exclude", "drafts/**",
                   "--on-delete", "keep", "--guard-percent", "25", "--guard-min-files", "3", brain=brain)
    assert code == 0
    p = load_config(brain).get("p")
    assert (p.target, p.include, p.exclude, p.on_source_delete) == ("raw/x/{relpath}", ["**/*.md", "**/*.markdown"], ["drafts/**"], "keep")
    assert (p.guard_threshold_percent, p.guard_min_files) == (25, 3)


@pytest.mark.parametrize("target", ["../x/{relpath}", "wiki/{relpath}", "/abs/{relpath}"])
def test_add_runs_the_same_validation_with_the_same_message_and_saves_nothing(brain, make_source, capsys, target):
    a = make_source("docs", {"x.md": "1"})
    code, out, err = run(capsys, "pipeline", "add", "p", "--source", str(a), "--target", target, brain=brain)
    assert code == 2
    from mdpipeline.config import Config, Pipeline

    expected = validate_pipeline(brain, Config([]), Pipeline.new("p", [str(a)], target=target)).errors[0].message
    assert expected in err
    assert load_config(brain).pipelines == []


def test_add_rejects_nested_sources_and_source_inside_raw(brain, brain_dir, make_source, capsys):
    a = make_source("docs", {"inner/x.md": "1"})
    code, out, err = run(capsys, "pipeline", "add", "p", "--source", str(a), "--source", str(a / "inner"), brain=brain)
    assert code == 2 and "nested" in err
    code, out, err = run(capsys, "pipeline", "add", "q", "--source", str(brain_dir / "raw"), "--target", "raw/{pipeline}/{relpath}", brain=brain)
    assert code == 2 and "inside the folder the files are written to" in err
    code, out, err = run(capsys, "pipeline", "add", "r", "--source", str(brain_dir / "raw" / "notes"), brain=brain)      # the default writes under raw/: a source inside it would loop
    assert code == 2 and "inside the folder the files are written to" in err


def test_add_with_a_missing_folder_succeeds_with_a_warning(brain, tmp_path, capsys):
    code, out, err = run(capsys, "pipeline", "add", "p", "--source", str(tmp_path / "unmounted"), brain=brain)
    assert code == 0 and "Warning" in err and "not found" in err


def test_add_duplicate_name_is_a_validation_error(brain, make_source, capsys):
    a, b = make_source("a", {"1.md": "1"}), make_source("b", {"2.md": "2"})
    run(capsys, "pipeline", "add", "p", "--source", str(a), brain=brain)
    code, out, err = run(capsys, "pipeline", "add", "p", "--source", str(b), brain=brain)
    assert code == 2 and "already exists" in err


def test_list_shows_name_folders_template_policy_status_last_sync(brain, make_source, capsys):
    a, b = make_source("docs", {"x.md": "1"}), make_source("specs", {"y.md": "2"})
    run(capsys, "pipeline", "add", "biz", "--source", str(a), "--source", str(b), brain=brain)
    code, out, _ = run(capsys, "pipeline", "list", brain=brain)
    assert code == 0
    for text in ("biz", "docs", "specs", "{relpath}", "delete", "never"):
        assert text in out
    sync_pipeline(brain, "biz")
    _, out, _ = run(capsys, "pipeline", "list", brain=brain)
    assert "ok" in out and "never" not in out


def test_list_json(brain, make_source, capsys):
    a = make_source("docs", {"x.md": "1"})
    run(capsys, "pipeline", "add", "biz", "--source", str(a), brain=brain)
    _, out, _ = run(capsys, "pipeline", "list", "--json", brain=brain)
    data = json.loads(out)
    assert data[0]["name"] == "biz" and data[0]["sources"][0]["label"] == "docs" and data[0]["status"] == "never"


def test_edit_changes_rules_and_adds_a_folder(brain, make_source, capsys):
    a, b = make_source("docs", {"x.md": "1"}), make_source("specs", {"y.md": "2"})
    run(capsys, "pipeline", "add", "p", "--source", str(a), brain=brain)
    code, out, err = run(capsys, "pipeline", "edit", "p", "--add-source", str(b), "--on-delete", "keep",
                         "--target", "raw/z/{source}/{relpath}", "--exclude", "tmp/**", brain=brain)
    assert code == 0, err
    p = load_config(brain).get("p")
    assert [s.label for s in p.sources] == ["docs", "specs"]
    assert (p.on_source_delete, p.target, p.exclude) == ("keep", "raw/z/{source}/{relpath}", ["tmp/**"])


def test_edit_that_fails_validation_leaves_the_config_unchanged(brain, make_source, capsys):
    a = make_source("docs", {"x.md": "1"})
    run(capsys, "pipeline", "add", "p", "--source", str(a), brain=brain)
    before = brain.config_path.read_bytes()
    code, out, err = run(capsys, "pipeline", "edit", "p", "--target", "../../escape/{relpath}", brain=brain)
    assert code == 2 and brain.config_path.read_bytes() == before


def test_edit_unknown_pipeline_exits_2(brain, capsys):
    code, out, err = run(capsys, "pipeline", "edit", "nope", "--on-delete", "keep", brain=brain)
    assert code == 2 and "nope" in err


def test_edit_a_folder_does_not_collide_with_itself(brain, make_source, capsys):
    a = make_source("docs", {"x.md": "1"})
    run(capsys, "pipeline", "add", "p", "--source", str(a), brain=brain)
    code, *_ = run(capsys, "pipeline", "edit", "p", "--guard-percent", "50", brain=brain)
    assert code == 0 and load_config(brain).get("p").guard_threshold_percent == 50


def test_rename_label_after_first_sync_moves_files_visible_in_a_dry_run(brain, make_source, capsys):
    a = make_source("docs", {"x.md": "1"})
    run(capsys, "pipeline", "add", "p", "--source", str(a), "--name-policy", "original", "--target", "raw/{pipeline}/{source}/{relpath}", brain=brain)
    sync_pipeline(brain, "p")
    code, *_ = run(capsys, "pipeline", "edit", "p", "--rename-label", "docs", "handbook", brain=brain)
    assert code == 0
    dry = sync_pipeline(brain, "p", dry_run=True)
    c = dry.counts()
    assert (c["created"], c["deleted"]) == (1, 1)
    assert [i.target for i in dry.plan.items() if i.action == "create"] == ["raw/p/handbook/x.md"]


def test_exit_codes_distinguish_validation_from_runtime_errors(brain, capsys):
    brain.config_path.write_text("version: 1\npipelines: [ {broken")
    code, out, err = run(capsys, "pipeline", "list", brain=brain)
    assert code == 1 and "config" in err.lower()


def test_every_command_has_help(capsys):
    for argv in (["--help"], ["init", "--help"], ["pipeline", "add", "--help"], ["pipeline", "edit", "--help"],
                 ["pipeline", "list", "--help"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 0
        assert len(capsys.readouterr().out) > 40


def test_module_entry_point_runs(brain_dir):
    r = subprocess.run([sys.executable, "-m", "mdpipeline", "init", str(brain_dir)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
