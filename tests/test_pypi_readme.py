"""MD-198 AC4 (marketing MM-147, MD-195 comment 11073): the README PyPI shows is the GitHub front page plus a short, fixed list of differences."""
from __future__ import annotations

import tomllib
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
FRONT = (APP / "README.md").read_text(encoding="utf-8")
PYPI_PATH = APP / "docs" / "pypi-readme.md"
PYPI = PYPI_PATH.read_text(encoding="utf-8") if PYPI_PATH.exists() else ""
BASE = "https://github.com/markdown-pipeline/markdown-pipeline/blob/main/"
MCP_NAME = "<!-- mcp-name: io.github.markdown-pipeline/mdpipeline -->"
UVX_SENTENCE = ("If you already have [uv](https://docs.astral.sh/uv/) (Linux, tested), your agent can start the server with `uvx mdpipeline mcp`; "
                "the first time, it tells you the one step to finish: `uvx mdpipeline start`.")
UNINSTALL_SENTENCE = ("If you ran it with `uvx`, run `uvx mdpipeline uninstall` to turn background sync off (it is on after your first sync), "
                      "then `uv cache clean mdpipeline` to remove the program.")


def test_pyproject_points_the_package_readme_at_the_pypi_file():
    project = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["readme"] == "docs/pypi-readme.md"


def test_the_pypi_readme_is_the_front_page_plus_exactly_the_listed_differences():
    undone = PYPI
    for rel in ("what-we-send.md", "docs/reference.md", "LICENSE"):
        assert f"]({BASE}{rel})" in undone                                      # 1: three links made absolute
        undone = undone.replace(f"]({BASE}{rel})", f"]({rel})")
    assert undone.count(MCP_NAME) == 1                                           # 2: the hidden Registry ownership line
    undone = undone.replace(f"{MCP_NAME}\n\n", "", 1)
    assert undone.count(UVX_SENTENCE) == 1                                       # 3: the uvx start (marketing's Q1)
    undone = undone.replace(f"\n\n{UVX_SENTENCE}", "", 1)
    assert undone.count(UNINSTALL_SENTENCE) == 1                                 # 4: the true uninstall words (marketing's Q2)
    undone = undone.replace(f" {UNINSTALL_SENTENCE}", "", 1)
    assert undone == FRONT


def test_both_pages_say_the_release_comes_from_github_releases_not_from_this_page():
    for text in (FRONT, PYPI):
        assert "It installs the latest release from GitHub Releases." in text and "from this page" not in text


def test_the_uvx_start_is_one_command_and_keeps_linux_tested_next_to_it():
    assert "(Linux, tested)" in UVX_SENTENCE and PYPI.count("`uvx mdpipeline start`") == 1 and "`mdpipeline start`" not in PYPI


def test_the_uninstall_words_do_not_claim_there_is_nothing_to_uninstall():
    assert "nothing to uninstall" not in PYPI and "uvx mdpipeline uninstall" in PYPI
