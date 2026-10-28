"""MD-133: the output folder is the root; the raw/ prefix is no longer forced. The safety boundary is "inside the output folder,
never the app's own folder or wiki/, never a file the pipeline does not own"."""
import os
from pathlib import Path

import pytest

import mdpipeline

from mdpipeline.boundary import check_target_relpath, check_write_target
from mdpipeline.brain import Brain
from mdpipeline.writer import SafeWriter, WriteRefused


# --- the pure boundary ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("rel", ["x.md", "a/b.md", "raw/a.md", "Jira/ACME/ACME-1.md", "a..b.md", ".hidden-file.md"])
def test_a_relative_path_inside_the_folder_is_allowed_with_or_without_raw(rel):
    assert check_write_target(rel) is None


@pytest.mark.parametrize("rel", [".mdpipeline/state.md", ".mdpipeline/tmp/x.md", "wiki/x.md", "wiki/a/b.md", "Wiki/x.md", "WIKI/x.md",
                                 ".MDPipeline/x.md", ".git/x.md", ".obsidian/x.md"])
def test_the_apps_own_folder_wiki_and_hidden_folders_are_never_targets(rel):
    assert check_write_target(rel) is not None


@pytest.mark.parametrize("rel", ["raw/p/wiki/c.md", "raw/Wiki/c.md", "a/.mdpipeline/x.md", "x/.hid/y.md"])
def test_deeper_down_those_names_are_ordinary_folder_names_so_existing_layouts_keep_working(rel):
    """A source may be labelled 'wiki'; raw/p/wiki/... was always legal and must stay so."""
    assert check_write_target(rel) is None


@pytest.mark.parametrize("rel", ["", "/abs.md", "../x.md", "a/../../x.md", "a//b.md", "./x.md", "a\\b.md", "a\x00.md"])
def test_hostile_paths_are_still_refused(rel):
    assert check_target_relpath(rel) is not None


@pytest.mark.parametrize("rel", ["x.txt", "x", "x.MD", ".md", "a/.md"])
def test_only_markdown_files_are_targets(rel):
    assert check_write_target(rel) is not None


# --- the writer, in a folder that has no raw/ --------------------------------------------------------------------------------
@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    return root


@pytest.fixture
def writer(folder):
    return SafeWriter(Brain(folder.resolve()))


def test_a_file_is_written_at_the_root_of_a_folder_without_raw(writer, folder):
    writer.write("a.md", b"hello")
    assert (folder / "a.md").read_bytes() == b"hello" and not (folder / "raw").exists()


def test_nested_folders_are_created_inside_the_output_folder(writer, folder):
    writer.write("Jira/ACME/ACME-1.md", b"x")
    assert (folder / "Jira/ACME/ACME-1.md").exists()


@pytest.mark.parametrize("rel", [".mdpipeline/x.md", "wiki/x.md", "../x.md"])
def test_the_writer_refuses_the_protected_places_and_writes_nothing(writer, folder, rel):
    with pytest.raises(WriteRefused):
        writer.write(rel, b"evil")
    assert not (folder / "wiki").exists() and not (folder.parent / "x.md").exists()


def test_a_symlinked_folder_pointing_at_wiki_is_refused(writer, folder):
    (folder / "wiki").mkdir()
    os.symlink(folder / "wiki", folder / "link")
    with pytest.raises(WriteRefused):
        writer.write("link/x.md", b"evil")
    assert not (folder / "wiki" / "x.md").exists()


def test_a_symlinked_folder_pointing_outside_is_refused(writer, folder, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, folder / "link")
    with pytest.raises(WriteRefused):
        writer.write("link/deep/x.md", b"evil")
    assert list(outside.iterdir()) == []


def test_an_existing_symlink_at_the_target_is_never_written_through(writer, folder, tmp_path):
    victim = tmp_path / "victim.md"
    victim.write_text("precious")
    os.symlink(victim, folder / "a.md")
    with pytest.raises(WriteRefused):
        writer.write("a.md", b"evil")
    assert victim.read_text() == "precious"


def test_delete_removes_the_file_and_prunes_empty_folders_but_never_the_output_folder(writer, folder):
    writer.write("p/sub/a.md", b"1")
    writer.delete("p/sub/a.md")
    assert not (folder / "p").exists() and folder.is_dir()


def test_delete_keeps_a_raw_folder_that_a_brain_expects(folder):
    (folder / "raw").mkdir()
    w = SafeWriter(Brain(folder.resolve()))
    w.write("raw/p/a.md", b"1")
    w.delete("raw/p/a.md")
    assert (folder / "raw").is_dir() and not (folder / "raw" / "p").exists()


def test_delete_of_a_protected_path_is_refused(writer, folder):
    (folder / "wiki").mkdir()
    (folder / "wiki" / "keep.md").write_text("curated")
    with pytest.raises(WriteRefused):
        writer.delete("wiki/keep.md")
    assert (folder / "wiki" / "keep.md").read_text() == "curated"


# --- never overwrite or delete a file the pipeline does not own (AC4), directory and GitHub -----------------------------------
from conftest import write  # noqa: E402
from mdpipeline.config import Pipeline, Source  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402


def _root_pipeline(add_pipeline, src, **kw):
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], name_policy="original", target="{relpath}", **kw))


def test_a_user_file_at_the_same_root_path_is_a_conflict_and_is_left_untouched(brain, brain_dir, add_pipeline, make_source):
    write(brain_dir / "README.md", "my own readme")
    src = make_source("docs", {"README.md": "# from the source\n", "b.md": "# B\n"})
    _root_pipeline(add_pipeline, src)
    res = sync_pipeline(brain, "p")
    assert res.counts()["conflicts"] == 1 and res.counts()["created"] == 1
    assert (brain_dir / "README.md").read_text() == "my own readme" and (brain_dir / "b.md").exists()


def test_removing_the_source_file_never_deletes_the_users_file_of_the_same_name(brain, brain_dir, add_pipeline, make_source):
    write(brain_dir / "README.md", "my own readme")
    src = make_source("docs", {"README.md": "# from the source\n", "b.md": "# B\n"})
    _root_pipeline(add_pipeline, src)
    sync_pipeline(brain, "p")
    (src / "README.md").unlink()
    (src / "b.md").unlink()
    sync_pipeline(brain, "p")
    assert (brain_dir / "README.md").read_text() == "my own readme" and not (brain_dir / "b.md").exists()


def test_the_directory_listing_for_a_rebuild_skips_the_apps_folder_and_wiki(brain, brain_dir):
    write(brain_dir / "a.md", "x")
    write(brain_dir / "sub/b.md", "x")
    write(brain_dir / "raw/c.md", "x")
    write(brain_dir / "wiki/w.md", "x")
    write(brain_dir / ".mdpipeline/tmp/t.md", "x")
    write(brain_dir / ".git/g.md", "x")
    assert sorted(brain.target.list_markdown()) == ["a.md", "raw/c.md", "sub/b.md"]


@pytest.fixture
def gh(brain, monkeypatch):
    from fake_github import FakeGitHub
    from mdpipeline import github_api
    from mdpipeline.targetconfig import GitHubSpec

    monkeypatch.setenv("BRAIN_TOKEN", "ghp_ROOTSECRET0123456789")
    fake = FakeGitHub(repo="acme/brain", branch="main", token="ghp_ROOTSECRET0123456789")
    github_api.set_transport(fake)
    yield fake, GitHubSpec("acme/brain", "env:BRAIN_TOKEN", "main").to_dict()
    github_api.set_transport(None)


def test_github_a_user_file_at_the_same_root_path_is_a_conflict_and_is_left_untouched(brain, add_pipeline, make_source, gh):
    fake, dest = gh
    fake.commit_as_other({"README.md": b"my own readme"})
    src = make_source("docs", {"README.md": "# from the source\n", "b.md": "# B\n"})
    _root_pipeline(add_pipeline, src, destination=dest)
    res = sync_pipeline(brain, "p")
    assert res.counts()["conflicts"] == 1 and res.counts()["created"] == 1
    files = fake.files()
    assert files["README.md"] == b"my own readme" and "b.md" in files


def test_github_removing_the_source_file_never_deletes_the_users_file(brain, add_pipeline, make_source, gh):
    fake, dest = gh
    fake.commit_as_other({"README.md": b"my own readme"})
    src = make_source("docs", {"README.md": "# from the source\n", "b.md": "# B\n"})
    _root_pipeline(add_pipeline, src, destination=dest)
    sync_pipeline(brain, "p")
    (src / "README.md").unlink()
    (src / "b.md").unlink()
    sync_pipeline(brain, "p")
    files = fake.files()
    assert files["README.md"] == b"my own readme" and "b.md" not in files


def test_github_listing_covers_the_repo_root_but_not_the_apps_folder_or_wiki(brain, add_pipeline, make_source, gh):
    fake, dest = gh
    fake.commit_as_other({"README.md": b"r", "docs/a.md": b"a", "raw/c.md": b"c", "wiki/w.md": b"w", ".mdpipeline/x.md": b"x", ".github/h.md": b"h"})
    src = make_source("docs", {"b.md": "# B\n"})
    _root_pipeline(add_pipeline, src, destination=dest)
    from mdpipeline.destinations import bind_target

    bound = bind_target(brain, brain_pipeline(brain))
    bound.target.begin()
    assert sorted(bound.target.list_markdown()) == ["README.md", "docs/a.md", "raw/c.md"]


def brain_pipeline(brain):
    from mdpipeline.config import load_config

    return load_config(brain).get("p")


# --- defaults for new pipelines, existing ones unchanged (AC1-AC3) -------------------------------------------------------------
import yaml  # noqa: E402

from mdpipeline.config import DEFAULT_TARGET, ROOT_TARGET, load_config, save_config  # noqa: E402
from uihelper import running_ui  # noqa: E402


def test_the_root_template_is_just_the_relative_path():
    assert ROOT_TARGET == "{relpath}"


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_target_means_the_root_of_the_output_folder(empty, tmp_path):
    assert Pipeline.new("p", [str(tmp_path)], target=empty).target == ROOT_TARGET


def test_a_blank_target_in_the_saved_config_loads_as_the_root(brain):
    brain.config_path.write_text("version: 1\npipelines:\n- name: p\n  sources:\n  - {path: /tmp/x, label: x}\n  target: ''\n", encoding="utf-8")
    assert load_config(brain).get("p").target == ROOT_TARGET


def test_a_pipeline_saved_before_the_upgrade_keeps_its_raw_template_and_its_paths(brain, brain_dir, make_source):
    """AC3: the config a previous version wrote (explicit raw/ template) plans and syncs exactly as before."""
    src = make_source("docs", {"a.md": "# A\n"})
    brain.config_path.write_text(yaml.safe_dump({"version": 1, "pipelines": [
        {"name": "old", "sources": [{"path": str(src), "label": "docs"}], "target": "raw/{pipeline}/{source}/{relpath}",
         "name_policy": "original"}]}), encoding="utf-8")
    assert load_config(brain).get("old").target == DEFAULT_TARGET == "raw/{pipeline}/{source}/{relpath}"
    sync_pipeline(brain, "old")
    assert (brain_dir / "raw/old/docs/a.md").is_file()
    save_config(brain, load_config(brain))                                   # a save by the new version changes nothing
    assert load_config(brain).get("old").target == "raw/{pipeline}/{source}/{relpath}"
    again = sync_pipeline(brain, "old")
    assert again.counts()["created"] == 0 and again.counts()["updated"] == 0 and again.counts()["deleted"] == 0


def _create(ui, src, **extra):
    draft = {"name": "p", "sources": [{"path": str(src), "label": "docs", "type": "folder"}], "name_policy": "original", **extra}
    return ui.post("/api/pipelines", {"draft": draft, "sync": True})


def test_the_new_pipeline_default_is_raw_relpath():
    from mdpipeline.config import NEW_PIPELINE_TARGET

    assert NEW_PIPELINE_TARGET == "raw/{relpath}"


def test_the_editor_creates_a_pipeline_without_a_target_under_raw_and_saves_it_explicitly(brain, brain_dir, make_source):
    """MD-140 AC1/AC5: the default is written into the config, and raw/ appears only when the first file is written."""
    src = make_source("docs", {"a.md": "# A\n"})
    with running_ui(brain) as ui:
        status, res = _create(ui, src)
    assert status in (200, 201), res
    assert load_config(brain).get("p").target == "raw/{relpath}"
    assert "target: raw/{relpath}" in brain.config_path.read_text()
    assert (brain_dir / "raw" / "a.md").is_file() and not (brain_dir / "a.md").exists()


def test_the_editor_keeps_an_explicitly_cleared_target_as_the_root(brain, brain_dir, make_source):
    """MD-140 AC2 / MD-133 AC1: clearing the field still means the root of the output folder."""
    src = make_source("docs", {"a.md": "# A\n"})
    with running_ui(brain) as ui:
        status, res = _create(ui, src, target="")
    assert status in (200, 201), res
    assert load_config(brain).get("p").target == ROOT_TARGET
    assert (brain_dir / "a.md").is_file() and not (brain_dir / "raw" / "a.md").exists()


def test_the_editor_prefills_the_field_with_the_new_default():
    js = (Path(mdpipeline.__file__).parent / "ui_static" / "app.js").read_text(encoding="utf-8")
    assert 'target: "raw/{relpath}"' in js and "empty = the root of the output folder" in js


def test_the_cli_creates_a_pipeline_without_a_target_under_raw(brain, make_source):
    from mdpipeline.cli import main

    src = make_source("docs", {"a.md": "# A\n"})
    assert main(["--brain", str(brain.root), "pipeline", "add", "p", "--source", str(src)]) == 0
    assert load_config(brain).get("p").target == "raw/{relpath}"


def test_the_cli_empty_target_still_means_the_root(brain, make_source):
    from mdpipeline.cli import main

    src = make_source("docs", {"a.md": "# A\n"})
    assert main(["--brain", str(brain.root), "pipeline", "add", "p", "--source", str(src), "--target", ""]) == 0
    assert load_config(brain).get("p").target == ROOT_TARGET


def test_the_cli_help_names_the_new_default(capsys):
    from mdpipeline.cli import main

    with pytest.raises(SystemExit):
        main(["pipeline", "add", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "default: raw/{relpath}" in out


def test_pipelines_saved_before_the_upgrade_keep_their_templates_and_plans(brain, brain_dir, make_source):
    """MD-140 AC4: one empty-template (MD-133) pipeline and one old raw/ pipeline plan no changes after the upgrade."""
    s1, s2 = make_source("one", {"a.md": "# A\n"}), make_source("two", {"b.md": "# B\n"})
    brain.config_path.write_text(yaml.safe_dump({"version": 1, "pipelines": [
        {"name": "rooty", "sources": [{"path": str(s1), "label": "one"}], "target": "{relpath}", "name_policy": "original"},
        {"name": "old", "sources": [{"path": str(s2), "label": "two"}], "target": "raw/{pipeline}/{source}/{relpath}", "name_policy": "original"}]}), encoding="utf-8")
    for n in ("rooty", "old"):
        sync_pipeline(brain, n)
    save_config(brain, load_config(brain))
    assert [load_config(brain).get(n).target for n in ("rooty", "old")] == ["{relpath}", "raw/{pipeline}/{source}/{relpath}"]
    for n in ("rooty", "old"):
        c = sync_pipeline(brain, n).counts()
        assert (c["created"], c["updated"], c["deleted"]) == (0, 0, 0)
    assert (brain_dir / "a.md").is_file() and (brain_dir / "raw/old/two/b.md").is_file()


def test_two_new_default_pipelines_with_overlapping_relpaths_are_reported_at_save_time(brain, make_source):
    s1, s2 = make_source("one", {"a.md": "# A\n"}), make_source("two", {"a.md": "# Other\n"})
    cfg = load_config(brain)
    from mdpipeline.config import NEW_PIPELINE_TARGET

    cfg.pipelines.append(Pipeline.new("p1", [Source(str(s1), "one")], target=NEW_PIPELINE_TARGET))
    save_config(brain, cfg)
    p2 = Pipeline.new("p2", [Source(str(s2), "two")], target=NEW_PIPELINE_TARGET)
    errs = validate_pipeline(brain, load_config(brain), p2).errors
    assert errs, "overlapping default targets must be reported"


def test_the_cli_still_accepts_an_explicit_raw_template(brain, make_source):
    from mdpipeline.cli import main

    src = make_source("docs", {"a.md": "# A\n"})
    assert main(["--brain", str(brain.root), "pipeline", "add", "p", "--source", str(src), "--target", "raw/{pipeline}/{relpath}"]) == 0
    assert load_config(brain).get("p").target == "raw/{pipeline}/{relpath}"


# --- a folder no longer needs raw/ (AC5) ----------------------------------------------------------------------------------------
from mdpipeline.brain import init_brain  # noqa: E402


def test_a_folder_without_raw_is_a_valid_output_folder_and_init_does_not_create_raw(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "notes.md").write_text("mine")
    brain = init_brain(root)
    assert brain.config_path.is_file() and not (root / "raw").exists() and (root / "notes.md").read_text() == "mine"


def test_init_with_create_makes_the_folder_but_not_raw(tmp_path):
    brain = init_brain(tmp_path / "new", create=True)
    assert (tmp_path / "new").is_dir() and not (tmp_path / "new" / "raw").exists() and brain.config_path.is_file()


def test_a_sync_into_a_folder_without_raw_never_creates_raw(tmp_path, make_source):
    root = tmp_path / "plain"
    root.mkdir()
    brain = init_brain(root)
    src = make_source("docs", {"a.md": "# A\n"})
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new("p", [Source(str(src), "docs")], target="{relpath}", name_policy="original"))
    save_config(brain, cfg)
    sync_pipeline(brain, "p")
    assert (root / "a.md").is_file() and not (root / "raw").exists()


# --- sources inside the written area would loop (generalises "source inside raw/") --------------------------------------------
from mdpipeline.validation import validate_pipeline  # noqa: E402


def _codes(brain, pipeline):
    return {i.code for i in validate_pipeline(brain, load_config(brain), pipeline).errors}


def test_a_source_inside_a_root_output_folder_is_refused(brain, brain_dir):
    inner = brain_dir / "notes"
    inner.mkdir()
    (inner / "a.md").write_text("x")
    assert "source-in-output" in _codes(brain, Pipeline.new("p", [str(inner)], target="{relpath}"))


def test_a_source_inside_raw_is_still_refused_for_the_old_template(brain, brain_dir):
    inner = brain_dir / "raw" / "projects"
    inner.mkdir()
    assert "source-in-output" in _codes(brain, Pipeline.new("p", [str(inner)]))


def test_a_source_in_work_is_still_fine_for_the_old_template(brain, brain_dir):
    work = brain_dir / "work" / "notes"
    work.mkdir()
    (work / "a.md").write_text("x")
    assert "source-in-output" not in _codes(brain, Pipeline.new("p", [str(work)]))


def test_a_source_inside_wiki_or_the_apps_folder_is_not_an_output_loop(brain, brain_dir):
    (brain_dir / "wiki" / "notes").mkdir()
    assert "source-in-output" not in _codes(brain, Pipeline.new("p", [str(brain_dir / "wiki" / "notes")], target="{relpath}"))
