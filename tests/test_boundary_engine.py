"""MD-17 at engine level: hostile configurations (bypassing save-time validation) never write outside raw/."""
import os

import pytest

from conftest import tree_snapshot
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    return d


def run(brain, add_pipeline, pipeline):
    add_pipeline(pipeline)
    return sync_pipeline(brain, pipeline.name, trigger="cli")


def wiki_and_outside_untouched(brain_dir, outside, before):
    assert tree_snapshot(brain_dir / "wiki") == before["wiki"]
    assert tree_snapshot(brain_dir / "work") == before["work"]
    assert list(outside.iterdir()) == []
    assert not (brain_dir.parent / "evil.md").exists()


@pytest.fixture
def before(brain_dir):
    (brain_dir / "wiki" / "index.md").write_text("curated")
    return {"wiki": tree_snapshot(brain_dir / "wiki"), "work": tree_snapshot(brain_dir / "work")}


@pytest.mark.parametrize("template", ["../evil/{relpath}", "raw/../wiki/{relpath}", "wiki/{relpath}", ".mdpipeline/{relpath}",
                                      "raw/{pipeline}/../../wiki/{relpath}", "raw"])
def test_hostile_templates_write_nothing_and_report_errors(brain, brain_dir, add_pipeline, make_source, outside, before, template):
    src = make_source("docs", {"a.md": "A"})
    res = run(brain, add_pipeline, Pipeline.new("p", [str(src)], target=template))
    assert res.result == "error" and res.counts()["created"] == 0
    wiki_and_outside_untouched(brain_dir, outside, before)
    assert not os.path.exists("/tmp/abs-a.md")


@pytest.mark.parametrize("template,expected", [("{relpath}", "a.md"), ("/tmp/abs-{name}.md", "tmp/abs-a.md"), ("work/{relpath}", "work/a.md")])
def test_templates_that_stay_inside_the_folder_write_there_and_nowhere_else(brain, brain_dir, add_pipeline, make_source, outside, template, expected):
    """MD-133: no forced raw/. The output folder is the root; even an absolute-looking template lands inside it."""
    (brain_dir / "wiki" / "index.md").write_text("curated")
    wiki = tree_snapshot(brain_dir / "wiki")
    src = make_source("docs", {"a.md": "A"})
    res = run(brain, add_pipeline, Pipeline.new("p", [str(src)], target=template, name_policy="original"))
    assert res.result == "ok" and res.counts()["created"] == 1
    assert (brain_dir / expected).is_file() and not os.path.exists("/tmp/abs-a.md")
    assert tree_snapshot(brain_dir / "wiki") == wiki and list(outside.iterdir()) == []


def test_symlinked_target_folder_into_wiki_is_refused(brain, brain_dir, add_pipeline, make_source, outside, before):
    os.symlink(brain_dir / "wiki", brain_dir / "raw" / "p")
    src = make_source("docs", {"a.md": "A"})
    res = run(brain, add_pipeline, Pipeline.new("p", [str(src)]))
    assert res.result == "error"
    assert "symlink" in res.errors[0]["error"]
    wiki_and_outside_untouched(brain_dir, outside, before)


def test_symlinked_target_folder_to_outside_is_refused(brain, brain_dir, add_pipeline, make_source, outside, before):
    os.symlink(outside, brain_dir / "raw" / "p")
    src = make_source("docs", {"a.md": "A"})
    res = run(brain, add_pipeline, Pipeline.new("p", [str(src)]))
    assert res.result == "error"
    wiki_and_outside_untouched(brain_dir, outside, before)


@pytest.mark.parametrize("name", ["a..b.md", "..hidden.md", "x...md"])
def test_source_file_names_containing_double_dots_stay_inside_raw(brain, brain_dir, add_pipeline, make_source, outside, before, name):
    src = make_source("docs", {name: "A"})
    res = run(brain, add_pipeline, Pipeline.new("p", [str(src)], include=["**/*.md", "**/.*.md"], exclude=[]))
    assert res.counts()["created"] == 1
    for f in brain_dir.rglob(name):
        assert (brain_dir / "raw") in f.parents
    wiki_and_outside_untouched(brain_dir, outside, before)


@pytest.mark.parametrize("label", ["a/b", "../../wiki", "..", "../x", "/abs"])
def test_source_label_with_path_separators_is_refused(brain, brain_dir, add_pipeline, make_source, outside, before, label):
    src = make_source("docs", {"a.md": "A"})
    res = run(brain, add_pipeline, Pipeline.new("p", [Source(str(src), label)]))
    assert res.counts()["created"] == 0 and res.result == "error"
    wiki_and_outside_untouched(brain_dir, outside, before)


def test_only_md_files_are_ever_written_even_with_a_template_that_changes_the_extension(brain, brain_dir, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A"})
    res = run(brain, add_pipeline, Pipeline.new("p", [str(src)], target="raw/{pipeline}/{name}.txt"))
    assert res.result == "error"
    assert [p for p in brain_dir.rglob("*") if p.is_file() and p.suffix == ".txt"] == []


def test_a_tampered_state_record_pointing_at_wiki_is_never_deleted(brain, brain_dir, add_pipeline, make_source):
    from mdpipeline.state import FileRecord, State

    (brain_dir / "wiki" / "keep.md").write_text("curated")
    src = make_source("docs", {})
    add_pipeline(Pipeline.new("p", [str(src)]))
    st = State.load(brain)
    st.pipeline("p").files["wiki/keep.md"] = FileRecord("docs", "keep.md", "sha256:x", "sha256:y")
    st.save()
    sync_pipeline(brain, "p", trigger="cli")
    assert (brain_dir / "wiki" / "keep.md").read_text() == "curated"


def test_nothing_is_written_to_wiki_in_a_normal_sync(brain, brain_dir, add_pipeline, make_source, before):
    src = make_source("docs", {"a.md": "A", "b/c.md": "C"})
    run(brain, add_pipeline, Pipeline.new("p", [str(src)]))
    assert tree_snapshot(brain_dir / "wiki") == before["wiki"]
    assert tree_snapshot(brain_dir / "work") == before["work"]
