"""MD-33: a file-name policy per pipeline (config, validation, planner, CLI, UI preview)."""
import json
from datetime import datetime, timezone

import pytest

from conftest import tree_snapshot
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.naming import SEPARATOR as S
from mdpipeline.service import ValidationFailed, create_pipeline, update_pipeline
from uihelper import running_ui

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


# config ---------------------------------------------------------------------------------------------
def test_pipeline_without_a_policy_key_loads_as_original(brain, make_source):
    src = make_source("docs", {"a.md": "x"})
    cfg_path = brain.config_path
    cfg_path.write_text(f"version: 1\npipelines:\n- name: old\n  sources:\n  - {{path: {src}, label: docs}}\n")
    p = load_config(brain).get("old")
    assert (p.name_policy, p.name_pattern) == ("original", "")


def test_policy_round_trips_through_the_config_file(brain, make_source):
    src = make_source("docs", {"a.md": "x"})
    p = Pipeline.new("p", [str(src)], name_policy="custom", name_pattern="{date}.{slug}")
    from mdpipeline.config import Config
    save_config(brain, Config([p]))
    loaded = load_config(brain).get("p")
    assert (loaded.name_policy, loaded.name_pattern) == ("custom", "{date}.{slug}")


def test_original_policy_is_not_written_to_the_config(brain, make_source):
    from mdpipeline.config import Config
    src = make_source("docs", {"a.md": "x"})
    save_config(brain, Config([Pipeline.new("p", [str(src)])]))
    assert "name_policy" not in brain.config_path.read_text()


# validation (same path as MD-9) -----------------------------------------------------------------------
@pytest.mark.parametrize("pattern", ["../{slug}", "a/{slug}", "{date}", "{slug}{nope}"])
def test_hostile_custom_pattern_is_rejected_at_save_and_nothing_is_written(brain, make_source, pattern):
    src = make_source("docs", {"a.md": "x"})
    before = brain.config_path.read_bytes()
    with pytest.raises(ValidationFailed) as exc:
        create_pipeline(brain, Pipeline.new("p", [str(src)], name_policy="custom", name_pattern=pattern))
    assert any(i.field == "name_policy" for i in exc.value.result.errors)
    assert brain.config_path.read_bytes() == before


def test_unknown_preset_is_rejected_at_save(brain, make_source):
    src = make_source("docs", {"a.md": "x"})
    with pytest.raises(ValidationFailed):
        create_pipeline(brain, Pipeline.new("p", [str(src)], name_policy="nope"))


# planner / sync ----------------------------------------------------------------------------------------
def names_in(brain, folder="raw/p/docs"):
    return sorted(p.name for p in (brain.root / folder).glob("*.md"))


def test_date_name_policy_names_new_files_and_keeps_the_directory_from_the_template(brain, add_pipeline, make_source):
    src = make_source("docs", {"README.md": "x", "sub/Meeting Notes (3.10).md": "y"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    res = sync_pipeline(brain, "p", trigger="cli", now=NOW)
    assert res.result == "ok"
    assert names_in(brain) == [f"2026-10-04{S}readme.md"]
    assert names_in(brain, "raw/p/docs/sub") == [f"2026-10-04{S}meeting-notes-3-10.md"]


def test_original_policy_is_exactly_the_p1_behaviour(brain, add_pipeline, make_source):
    src = make_source("docs", {"My File.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli", now=NOW)
    assert names_in(brain) == ["My File.md"]


def test_custom_policy_applies(brain, add_pipeline, make_source):
    src = make_source("docs", {"Alpha Beta.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="custom", name_pattern="{source}.{slug}"))
    sync_pipeline(brain, "p", trigger="cli", now=NOW)
    assert names_in(brain) == ["docs.alpha-beta.md"]


def test_dry_run_lists_resulting_names_and_writes_nothing(brain, add_pipeline, make_source):
    src = make_source("docs", {"README.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    before = tree_snapshot(brain.raw_dir)
    res = sync_pipeline(brain, "p", trigger="cli", dry_run=True, now=NOW)
    item = res.plan.items()[0]
    assert item.source_path == "README.md" and item.target == f"raw/p/docs/2026-10-04{S}readme.md"
    assert tree_snapshot(brain.raw_dir) == before


def test_policy_never_writes_outside_raw_even_if_saved_by_hand(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="custom", name_pattern="../../{slug}"))
    before = tree_snapshot(brain.root / "wiki")
    res = sync_pipeline(brain, "p", trigger="cli", now=NOW)
    assert res.result == "error" and names_in(brain) == []
    assert tree_snapshot(brain.root / "wiki") == before


# CLI -----------------------------------------------------------------------------------------------------
def test_cli_add_defaults_to_the_date_name_policy(brain, make_source, capsys):
    src = make_source("docs", {"a.md": "x"})
    code, *_ = run(capsys, brain, "pipeline", "add", "p", "--source", f"docs={src}")
    assert code == 0 and load_config(brain).get("p").name_policy == "date-name"


def test_cli_add_accepts_a_preset_and_a_custom_pattern(brain, make_source, capsys):
    src, src2 = make_source("docs", {"a.md": "x"}), make_source("docs2", {"a.md": "x"})
    assert run(capsys, brain, "pipeline", "add", "a", "--source", f"d1={src}", "--name-policy", "original")[0] == 0
    code, *_ = run(capsys, brain, "pipeline", "add", "b", "--source", f"d2={src2}", "--name-pattern", "{slug}.{date}")
    cfg = load_config(brain)
    assert code == 0 and cfg.get("a").name_policy == "original"
    assert (cfg.get("b").name_policy, cfg.get("b").name_pattern) == ("custom", "{slug}.{date}")


def test_cli_add_rejects_a_bad_pattern_with_exit_2(brain, make_source, capsys):
    src = make_source("docs", {"a.md": "x"})
    code, _, err = run(capsys, brain, "pipeline", "add", "p", "--source", str(src), "--name-pattern", "../{slug}")
    assert code == 2 and "path separator" in err and load_config(brain).get("p") is None


def test_cli_list_shows_the_policy(brain, add_pipeline, make_source, capsys):
    src = make_source("docs", {"a.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="custom", name_pattern="{slug}.{date}"))
    _, out, _ = run(capsys, brain, "pipeline", "list")
    assert "file names" in out and "{slug}.{date}" in out
    _, out, _ = run(capsys, brain, "pipeline", "list", "--json")
    row = json.loads(out)[0]
    assert row["name_policy"] == "custom" and row["name_pattern"] == "{slug}.{date}"


def test_cli_list_shows_original_for_legacy_pipelines(brain, add_pipeline, make_source, capsys):
    src = make_source("docs", {"a.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    _, out, _ = run(capsys, brain, "pipeline", "list")
    assert "original" in out


def test_cli_edit_changes_the_policy_on_a_pipeline_with_no_files_yet(brain, add_pipeline, make_source, capsys):
    src = make_source("docs", {"a.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    code, *_ = run(capsys, brain, "pipeline", "edit", "p", "--name-policy", "name")
    assert code == 0 and load_config(brain).get("p").name_policy == "name"


# UI ------------------------------------------------------------------------------------------------------
def draft(src, **kw):
    d = {"name": "p", "sources": [{"path": str(src), "label": "docs"}], "target": "raw/{pipeline}/{source}/{relpath}",
         "include": ["**/*.md"], "exclude": [], "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5}
    d.update(kw)
    return d


def test_ui_preview_lists_the_resulting_names(brain, make_source):
    src = make_source("docs", {"README.md": "x"})
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft(src, name_policy="name")})
    item = res["preview"]["groups"][0]["items"][0]
    assert item["source"] == "README.md" and item["target"] == "raw/p/docs/readme.md"


def test_ui_preview_reports_a_bad_pattern_next_to_the_field(brain, make_source):
    src = make_source("docs", {"README.md": "x"})
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft(src, name_policy="custom", name_pattern="{date}")})
    assert any(e["field"] == "name_policy" for e in res["validation"]["errors"])


def test_ui_pipeline_view_and_save_carry_the_policy(brain, make_source):
    src = make_source("docs", {"README.md": "x"})
    with running_ui(brain) as ui:
        status, _ = ui.post("/api/pipelines", {"draft": draft(src, name_policy="date-name")})
        assert status in (200, 201)
        _, view = ui.get("/api/pipelines/p")
    assert view["draft"]["name_policy"] == "date-name"
    assert load_config(brain).get("p").name_policy == "date-name"


def test_ui_draft_without_a_policy_key_gets_the_new_pipeline_default(brain, make_source):      # MD-148: was "stays original"
    src = make_source("docs", {"README.md": "x"})
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft(src)})
    assert res["preview"]["groups"][0]["items"][0]["target"].endswith(f"{S}readme.md")


def test_ui_draft_that_asks_for_original_still_gets_original(brain, make_source):
    src = make_source("docs", {"README.md": "x"})
    with running_ui(brain) as ui:
        _, res = ui.post("/api/preview", {"draft": draft(src, name_policy="original")})
    assert res["preview"]["groups"][0]["items"][0]["target"] == "raw/p/docs/README.md"
