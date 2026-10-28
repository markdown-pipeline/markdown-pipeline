"""MD-9: a bad configuration is rejected at save time with a clear reason."""
import os

import pytest

from mdpipeline.brain import init_brain
from mdpipeline.config import Config, Pipeline, Source
from mdpipeline.validation import validate_pipeline


@pytest.fixture
def brain(brain_dir):
    return init_brain(brain_dir)


def check(brain, pipeline, others=()):
    return validate_pipeline(brain, Config(list(others)), pipeline)


def codes(issues):
    return {i.code for i in issues}


def test_valid_pipeline_has_no_errors(brain, make_source):
    src = make_source("docs", {"a.md": "x"})
    res = check(brain, Pipeline.new("p", [str(src)]))
    assert res.errors == [] and res.warnings == [] and res.ok


# AC1 ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "template",
    ["../outside/{relpath}", "raw/../wiki/{relpath}", "/etc/{relpath}", "wiki/{relpath}", ".mdpipeline/{relpath}",
     "raw/../../x/{relpath}", "raw/{pipeline}/../../wiki/{relpath}"],
)
def test_template_resolving_outside_the_folder_or_into_a_protected_place_is_rejected(brain, make_source, template):
    src = make_source("docs", {"a.md": "x"})
    res = check(brain, Pipeline.new("p", [str(src)], target=template))
    assert "target-not-allowed" in codes(res.errors)
    assert not res.ok


def test_unknown_template_variable_is_rejected(brain, make_source):
    src = make_source("docs", {"a.md": "x"})
    res = check(brain, Pipeline.new("p", [str(src)], target="raw/{nope}/{relpath}"))
    assert "bad-template" in codes(res.errors)


def test_a_template_that_only_escapes_for_some_files_is_rejected_using_real_files(brain, make_source):
    # {dir} is empty for root files but a path segment for others: all files are checked
    src = make_source("docs", {"a.md": "x", "sub/b.md": "y"})
    res = check(brain, Pipeline.new("p", [str(src)], target="raw/{relpath}"))
    assert res.ok  # still inside raw/


# AC2 ---------------------------------------------------------------------------------
def test_collision_between_two_folders_of_one_pipeline_names_both_sources(brain, make_source):
    a = make_source("a", {"same.md": "1"})
    b = make_source("b", {"same.md": "2"})
    p = Pipeline.new("p", [Source(str(a), "one"), Source(str(b), "two")], target="raw/{pipeline}/{relpath}")
    res = check(brain, p)
    err = next(i for i in res.errors if i.code == "target-collision")
    assert "one:same.md" in err.message and "two:same.md" in err.message


def test_collision_across_pipelines_is_rejected(brain, make_source):
    a = make_source("a", {"same.md": "1"})
    b = make_source("b", {"same.md": "2"})
    other = Pipeline.new("other", [str(a)], target="raw/shared/{relpath}")
    mine = Pipeline.new("mine", [str(b)], target="raw/shared/{relpath}")
    res = check(brain, mine, others=[other])
    assert "target-collision" in codes(res.errors)


def test_collision_check_is_case_insensitive(brain, make_source):
    a = make_source("a", {"Notes.md": "1"})
    b = make_source("b", {"notes.md": "2"})
    p = Pipeline.new("p", [str(a), str(b)], target="raw/p/{relpath}")
    assert "target-collision" in codes(check(brain, p).errors)


# AC3 ---------------------------------------------------------------------------------
def test_same_folder_twice_in_a_pipeline_is_rejected(brain, make_source):
    a = make_source("a")
    p = Pipeline.new("p", [Source(str(a), "x"), Source(str(a), "y")])
    assert "duplicate-source" in codes(check(brain, p).errors)


def test_nested_folder_in_a_pipeline_is_rejected(brain, make_source):
    a = make_source("a", {"inner/x.md": "1"})
    p = Pipeline.new("p", [str(a), str(a / "inner")])
    assert "nested-source" in codes(check(brain, p).errors)


def test_nested_or_duplicate_folder_across_pipelines_is_rejected(brain, make_source):
    a = make_source("a", {"inner/x.md": "1"})
    other = Pipeline.new("other", [str(a)])
    assert "nested-source" in codes(check(brain, Pipeline.new("mine", [str(a / "inner")]), [other]).errors)
    assert "duplicate-source" in codes(check(brain, Pipeline.new("mine", [str(a)]), [other]).errors)


def test_nesting_via_symlink_is_detected(brain, make_source, tmp_path):
    a = make_source("a", {"inner/x.md": "1"})
    link = tmp_path / "link"
    os.symlink(a / "inner", link)
    p = Pipeline.new("p", [str(a), Source(str(link), "l")])
    assert codes(check(brain, p).errors) & {"nested-source", "duplicate-source"}


def test_editing_a_pipeline_does_not_collide_with_its_own_saved_copy(brain, make_source):
    a = make_source("a", {"x.md": "1"})
    saved = Pipeline.new("p", [str(a)])
    edited = Pipeline.new("p", [str(a)])
    res = validate_pipeline(brain, Config([saved]), edited, editing="p")
    assert res.ok


def test_sibling_folders_with_shared_name_prefix_are_not_nested(brain, make_source):
    a = make_source("docs", {"x.md": "1"})
    b = make_source("docs2", {"y.md": "1"})
    assert check(brain, Pipeline.new("p", [str(a), str(b)])).ok


# AC4 ---------------------------------------------------------------------------------
def test_source_inside_raw_is_rejected(brain, brain_dir):
    inner = brain_dir / "raw" / "projects"
    inner.mkdir()
    assert "source-in-output" in codes(check(brain, Pipeline.new("p", [str(inner)])).errors)
    assert "source-in-output" in codes(check(brain, Pipeline.new("p", [str(brain_dir / "raw")])).errors)


def test_source_that_contains_the_brain_is_rejected(brain, brain_dir):
    assert "source-contains-brain" in codes(check(brain, Pipeline.new("p", [str(brain_dir.parent)])).errors)
    assert "source-contains-brain" in codes(check(brain, Pipeline.new("p", [str(brain_dir)])).errors)


def test_source_elsewhere_in_the_brain_is_allowed(brain, brain_dir):
    work = brain_dir / "work" / "notes"
    work.mkdir()
    (work / "a.md").write_text("x")
    assert check(brain, Pipeline.new("p", [str(work)])).ok


def test_source_inside_raw_via_symlink_is_rejected(brain, brain_dir, tmp_path):
    (brain_dir / "raw" / "x").mkdir()
    link = tmp_path / "sneaky"
    os.symlink(brain_dir / "raw" / "x", link)
    assert "source-in-output" in codes(check(brain, Pipeline.new("p", [str(link)])).errors)


# AC5 ---------------------------------------------------------------------------------
def test_empty_label_is_rejected(brain, make_source):
    a = make_source("a")
    p = Pipeline.new("p", [Source(str(a), "x")])
    p.sources[0].label = ""
    assert "bad-label" in codes(check(brain, p).errors)


def test_duplicate_label_is_rejected(brain, make_source):
    a, b = make_source("a"), make_source("b")
    p = Pipeline.new("p", [Source(str(a), "same"), Source(str(b), "same")])
    assert "duplicate-label" in codes(check(brain, p).errors)


@pytest.mark.parametrize("label", ["a/b", "..", ".", "a\\b"])
def test_label_with_path_separators_or_dots_is_rejected(brain, make_source, label):
    a = make_source("a")
    p = Pipeline.new("p", [Source(str(a), label)])
    assert "bad-label" in codes(check(brain, p).errors)


# warnings ------------------------------------------------------------------------------
def test_missing_source_folder_is_allowed_with_a_warning(brain, tmp_path, make_source):
    ok = make_source("ok", {"a.md": "1"})
    p = Pipeline.new("p", [str(ok), str(tmp_path / "unmounted")])
    res = check(brain, p)
    assert res.ok
    assert "source-missing" in codes(res.warnings)


def test_zero_matching_files_warns_and_flags_no_save_and_sync(brain, make_source):
    empty = make_source("empty", {"readme.txt": "x"})
    res = check(brain, Pipeline.new("p", [str(empty)]))
    assert res.ok and "no-files" in codes(res.warnings)
    assert res.can_save_and_sync is False


def test_save_and_sync_allowed_when_files_match(brain, make_source):
    src = make_source("a", {"a.md": "1"})
    assert check(brain, Pipeline.new("p", [str(src)])).can_save_and_sync


# structural ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["", "a/b", "..", "has space", "x" * 100])
def test_bad_pipeline_names_are_rejected(brain, make_source, name):
    a = make_source("a")
    assert "bad-name" in codes(check(brain, Pipeline.new(name, [str(a)])).errors)


def test_duplicate_pipeline_name_is_rejected_on_create(brain, make_source):
    a, b = make_source("a"), make_source("b")
    res = check(brain, Pipeline.new("p", [str(b)]), others=[Pipeline.new("p", [str(a)])])
    assert "duplicate-name" in codes(res.errors)


def test_at_least_one_source_is_required(brain):
    assert "no-sources" in codes(check(brain, Pipeline.new("p", [])).errors)


def test_on_source_delete_must_be_delete_or_keep(brain, make_source):
    a = make_source("a")
    assert "bad-policy" in codes(check(brain, Pipeline.new("p", [str(a)], on_source_delete="purge")).errors)


@pytest.mark.parametrize("pct,minf", [(-1, 5), (101, 5), (40, -1)])
def test_guard_values_must_be_sane(brain, make_source, pct, minf):
    a = make_source("a")
    p = Pipeline.new("p", [str(a)], guard_threshold_percent=pct, guard_min_files=minf)
    assert "bad-guard" in codes(check(brain, p).errors)


def test_source_path_must_not_be_a_file(brain, make_source, tmp_path):
    f = tmp_path / "f.md"
    f.write_text("x")
    assert "source-not-a-folder" in codes(check(brain, Pipeline.new("p", [str(f)])).errors)
