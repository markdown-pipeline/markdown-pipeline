"""MD-8 AC2: target path template variables (semantics defined in README: review item A3)."""
from datetime import datetime, timezone

import pytest

from mdpipeline.template import TemplateError, render_target

MTIME = datetime(2026, 9, 30, 14, 2, tzinfo=timezone.utc).timestamp()


def render(template, relpath="docs/pricing.md", **kw):
    return render_target(
        template,
        pipeline=kw.get("pipeline", "biz-mng"),
        source=kw.get("source", "main"),
        relpath=relpath,
        mtime=MTIME,
    )


def test_default_suggested_template():
    assert render("raw/{pipeline}/{source}/{relpath}") == "raw/biz-mng/main/docs/pricing.md"


def test_relpath_includes_dirs_filename_and_extension():
    assert render("raw/{relpath}") == "raw/docs/pricing.md"


def test_dir_is_relative_to_source_folder_and_empty_for_root_files():
    assert render("raw/{dir}/{name}.{ext}") == "raw/docs/pricing.md"
    assert render("raw/x/{dir}/{name}.md", relpath="top.md") == "raw/x/top.md"


def test_name_has_no_extension_and_ext_has_no_dot():
    assert render("raw/{name}", relpath="a/b.c.md") == "raw/b.c"
    assert render("raw/{ext}", relpath="a/b.c.md") == "raw/md"


def test_mtime_supports_year_month_day_in_utc():
    assert render("raw/{mtime:YYYY-MM-DD}-{name}.md") == "raw/2026-09-30-pricing.md"
    assert render("raw/{mtime:YYYY}/{mtime:MM}/{name}.md") == "raw/2026/09/pricing.md"


@pytest.mark.parametrize("fmt", ["HH", "YYYY:MM", "yyyy", "YY", "", "YYYY/MM"])
def test_mtime_rejects_other_tokens_or_characters(fmt):
    with pytest.raises(TemplateError):
        render("raw/{mtime:%s}/x.md" % fmt)


def test_unknown_variable_is_an_error_naming_it():
    with pytest.raises(TemplateError, match="bogus"):
        render("raw/{bogus}/x.md")


def test_unbalanced_braces_are_an_error():
    with pytest.raises(TemplateError):
        render("raw/{relpath")


def test_empty_segments_collapse():
    assert render("raw//a///{relpath}") == "raw/a/docs/pricing.md"


@pytest.mark.parametrize("title", ["Fix {broken} parser", "Unclosed { brace", "Stray } brace", "{{double}}"])
def test_braces_inside_a_source_file_name_are_kept_literally(title):
    """MD-151: issue titles with {...} reach the template as values and must not be re-parsed."""
    relpath = f"MD/{title}.md"
    assert render("raw/{source}/{relpath}", relpath=relpath) == f"raw/main/{relpath}"


def test_braces_in_a_source_label_or_pipeline_name_are_kept_literally():
    assert render("raw/{pipeline}/{source}/{name}.md", pipeline="a{b}", source="s}x", relpath="n.md") == "raw/a{b}/s}x/n.md"
