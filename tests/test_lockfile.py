"""MD-169 AC2: uv.lock is committed in step with the version in pyproject.toml, so `uv run` never rewrites it (it used to change the
version string on every run, and the developer reverted it by hand). When you bump the version: run `uv lock` and commit both files."""
import re
import tomllib
from pathlib import Path

APP = Path(__file__).parent.parent


def test_the_lockfile_names_the_same_version_as_pyproject():
    project = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((APP / "uv.lock").read_text(encoding="utf-8"))
    mine = [p for p in lock["package"] if p["name"] == project["name"]]
    assert len(mine) == 1
    assert mine[0]["version"] == project["version"], (
        f"uv.lock says {mine[0]['version']} but pyproject.toml says {project['version']}: run `uv lock` and commit uv.lock with the version bump")


def test_the_release_script_tells_the_developer_to_lock_after_a_bump():
    text = (APP / "scripts" / "release.py").read_text(encoding="utf-8")
    assert re.search(r"uv lock", text)
