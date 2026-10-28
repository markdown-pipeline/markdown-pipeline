"""Glob matching on POSIX relative paths (used by include/exclude rules, MD-8)."""
import pytest

from mdpipeline.globs import matches_any


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("**/*.md", "a.md", True),
        ("**/*.md", "docs/a.md", True),
        ("**/*.md", "docs/deep/a.md", True),
        ("**/*.md", "a.txt", False),
        ("*.md", "docs/a.md", False),
        ("*.md", "a.md", True),
        (".git/**", ".git/config", True),
        (".git/**", "x/.git/config", False),
        ("**/.*/**", ".claude/skills/a.md", True),
        ("**/.*/**", "docs/.ag/a.md", True),
        ("**/.*/**", "docs/a.md", False),
        ("node_modules/**", "node_modules/x/readme.md", True),
        ("docs/?.md", "docs/a.md", True),
        ("docs/?.md", "docs/ab.md", False),
        ("a.md", "a.md", True),
        ("a.md", "b/a.md", False),
        ("**/*.markdown", "n/x.markdown", True),
    ],
)
def test_pattern_matching(pattern, path, expected):
    assert matches_any([pattern], path) is expected


def test_matches_any_returns_first_matching_pattern_via_helper():
    from mdpipeline.globs import first_match

    assert first_match(["x/**", "**/*.md"], "a/b.md") == "**/*.md"
    assert first_match(["x/**"], "a/b.md") is None
