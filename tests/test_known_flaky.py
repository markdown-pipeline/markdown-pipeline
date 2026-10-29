"""The tests that are allowed to be re-run, each with a written reason (MD-198, product choice (b) on 2026-10-29). Nothing else may be marked flaky,
so a new intermittent failure cannot be hidden by a rerun."""
from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ALLOWED = {"test_ui_browser_missing_source.py": "test_choose_new_location_refuses_a_bad_path_then_saves_a_good_one_and_shows_the_plan"}


def _marks() -> list[tuple[str, str]]:
    found = []
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"@pytest\.mark\.flaky\((?P<args>.*)\)[ \t]*\ndef (?P<name>test_\w+)", text):
            found.append((path.name, m.group("name") + "|" + m.group("args")))
    return found


def test_only_the_listed_test_is_marked_flaky():
    assert [(f, n.split("|")[0]) for f, n in _marks()] == list(ALLOWED.items())


def test_the_flaky_mark_is_bounded_and_says_why():
    [(_, entry)] = _marks()
    args = entry.split("|", 1)[1]
    assert re.search(r"reruns=[12]\b", args) and "reason=" in args and "MD-198" in args
