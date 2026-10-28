"""MD-34: slug rule and {date} for file-name policies (pure functions)."""
from __future__ import annotations

import pytest

from mdpipeline import naming
from mdpipeline.naming import SEPARATOR, derive_name_parts, slugify, split_leading_date

FIRST_SEEN = "2026-10-04"


@pytest.mark.parametrize("stem, slug", [
    ("README", "readme"),
    ("Meeting Notes (3.10)", "meeting-notes-3-10"),
    ("סיכום פגישה", "סיכום-פגישה"),
    ("  --Hello,   World!!  ", "hello-world"),
    ("snake_case_name", "snake-case-name"),
    ("a___b", "a-b"),
    ("Café", "café"),
    ("x" * 10 + "&" + "y", "xxxxxxxxxx-y"),
])
def test_slugify_examples(stem, slug):
    assert slugify(stem) == slug


def test_slugify_normalizes_to_nfc():
    decomposed = "Café"
    assert slugify(decomposed) == "café"


def test_slugify_never_contains_underscore():
    assert "_" not in slugify("a_b c_d")


@pytest.mark.parametrize("stem", ["***", "()", "", "  ", "_"])
def test_slugify_empty_falls_back_to_untitled(stem):
    assert slugify(stem) == "untitled"


def test_slugify_truncates_to_80_at_a_dash_boundary():
    stem = "-".join(["word"] * 30)  # 149 chars
    slug = slugify(stem)
    assert len(slug) <= 80 and not slug.endswith("-")
    assert slug == "-".join(["word"] * 16)  # 16*4 + 15 = 79


def test_slugify_hard_cuts_when_there_is_no_boundary():
    assert slugify("a" * 200) == "a" * 80


def test_slugify_keeps_exactly_80_chars():
    assert slugify("a" * 80) == "a" * 80


@pytest.mark.parametrize("stem, date, rest", [
    ("20260930-seed-idea", "2026-09-30", "seed-idea"),
    ("2026-10-01_phase1-dogfood-prd", "2026-10-01", "phase1-dogfood-prd"),
    ("2026-10-01 notes", "2026-10-01", "notes"),
    ("2026-10-01.notes", "2026-10-01", "notes"),
    ("notes-2026-10-01", None, "notes-2026-10-01"),
    ("20261301-x", None, "20261301-x"),          # month 13 is not a date
    ("2026-10-01", None, "2026-10-01"),          # no separator after the date
    ("20260930x", None, "20260930x"),
])
def test_split_leading_date(stem, date, rest):
    assert split_leading_date(stem) == (date, rest)


def test_date_is_first_seen_when_name_has_none():
    parts = derive_name_parts("README.md", FIRST_SEEN)
    assert (parts.date, parts.slug) == (FIRST_SEEN, "readme")


def test_date_in_the_name_wins_and_is_not_doubled():
    parts = derive_name_parts("20260930-seed-idea.md", FIRST_SEEN)
    assert (parts.date, parts.slug) == ("2026-09-30", "seed-idea")


def test_name_part_is_original_stem_without_extension():
    assert derive_name_parts("Meeting Notes (3.10).md", FIRST_SEEN).name == "Meeting Notes (3.10)"


def test_name_part_is_nfc():
    assert derive_name_parts("Café.md", FIRST_SEEN).name == "café".replace("café", "Café")


@pytest.mark.parametrize("filename, expected", [
    ("README.md", f"{FIRST_SEEN}{{sep}}readme.md"),
    ("Meeting Notes (3.10).md", f"{FIRST_SEEN}{{sep}}meeting-notes-3-10.md"),
    ("סיכום פגישה.md", f"{FIRST_SEEN}{{sep}}סיכום-פגישה.md"),
    ("20260930-seed-idea.md", "2026-09-30{sep}seed-idea.md"),
    ("2026-10-01_phase1-dogfood-prd.md", "2026-10-01{sep}phase1-dogfood-prd.md"),
])
def test_default_policy_fixture_examples(filename, expected):
    parts = derive_name_parts(filename, FIRST_SEEN)
    assert f"{parts.date}{SEPARATOR}{parts.slug}.md" == expected.format(sep=SEPARATOR)


def test_separator_is_one_named_constant_of_allowed_value():
    assert naming.SEPARATOR in ("-", "_")


# --- MD-33: policies, custom patterns, rendering --------------------------------------------------
from mdpipeline.naming import (DEFAULT_NAME_POLICY, PRESETS, check_name_policy, render_file_name)

S = SEPARATOR


def render(policy, pattern="", filename="Meeting Notes.md", **kw):
    args = dict(first_seen=FIRST_SEEN, source="docs", pipeline="proj")
    args.update(kw)
    return render_file_name(policy, pattern, filename=filename, **args)


def test_default_policy_is_date_name_and_listed_as_preset():
    assert DEFAULT_NAME_POLICY == "date-name" and "date-name" in PRESETS


@pytest.mark.parametrize("policy, expected", [
    ("date-name", f"{FIRST_SEEN}{S}meeting-notes.md"),
    ("name", "meeting-notes.md"),
    ("source-name", "docs--meeting-notes.md"),
    ("date-source-name", f"{FIRST_SEEN}{S}docs{S}meeting-notes.md"),
    ("original", "Meeting Notes.md"),
])
def test_presets_render(policy, expected):
    assert render(policy) == expected


def test_original_keeps_the_source_name_exactly():
    assert render("original", filename="Weird NAME_v2 (final).md") == "Weird NAME_v2 (final).md"


def test_custom_pattern_variables():
    out = render("custom", "{pipeline}-{source}-{date}.{slug}", filename="A B.md")
    assert out == f"proj-docs-{FIRST_SEEN}.a-b.md"


def test_custom_name_variable_is_the_original_name_without_extension():
    assert render("custom", "{name}", filename="Keep Case.md") == "Keep Case.md"


def test_always_ends_in_md_once():
    assert render("custom", "{slug}", filename="x.md").endswith(".md") and not render("custom", "{slug}", filename="x.md").endswith(".md.md")


def test_clash_suffix_is_appended_before_md():
    assert render("date-name", filename="README.md", suffix="3f9a1c") == f"{FIRST_SEEN}{S}readme{naming.CLASH_SEPARATOR}3f9a1c.md"


@pytest.mark.parametrize("pattern, fragment", [
    ("{date}", "{slug}"),                       # neither slug nor name
    ("../{slug}", "path"),
    ("a/{slug}", "path"),
    ("a\\{slug}", "path"),
    ("{slug}..x", ".."),
    ("{slug}{bogus}", "bogus"),
    ("{slug", "brace"),
    ("{slug} x", "character"),
    ("", "pattern"),
])
def test_custom_pattern_rejected_with_clear_message(pattern, fragment):
    problems = check_name_policy("custom", pattern)
    assert problems and fragment in " ".join(problems)


@pytest.mark.parametrize("pattern", ["{slug}", "{name}", "{date}_{slug}", "{date}-{source}.{slug}", "{pipeline}_{name}"])
def test_valid_custom_patterns_accepted(pattern):
    assert check_name_policy("custom", pattern) == []


def test_unknown_preset_rejected():
    assert "date-nam" in " ".join(check_name_policy("date-nam", ""))


@pytest.mark.parametrize("policy", ["original", "date-name", "name", "source-name", "date-source-name"])
def test_presets_need_no_pattern(policy):
    assert check_name_policy(policy, "") == []
