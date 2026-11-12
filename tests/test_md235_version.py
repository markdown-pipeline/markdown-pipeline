"""MD-235: the version has ONE source (pyproject.toml, through the installed package's metadata), and the release script refuses a release whose
files disagree with the tag. 0.1.22 shipped saying 0.1.21 because ``__init__.py`` kept a hand-written copy."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import mdpipeline
from mdpipeline import _version

APP = Path(__file__).resolve().parent.parent
PYPROJECT_VERSION = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


# --- AC1: one source ---------------------------------------------------------------------------------------------------------
def test_the_package_version_is_the_pyproject_version():
    assert mdpipeline.__version__ == PYPROJECT_VERSION


def test_there_is_no_hand_kept_version_string_in_the_package():
    text = (APP / "src/mdpipeline/__init__.py").read_text(encoding="utf-8")
    assert f'"{PYPROJECT_VERSION}"' not in text and "0.1.21" not in text


def test_the_version_comes_from_the_installed_package_metadata():
    assert _version.resolve(lookup=lambda name: "9.8.7") == "9.8.7"


def test_a_source_checkout_without_metadata_reads_pyproject_next_to_the_source(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "mdpipeline"\nversion = "4.5.6"\n', encoding="utf-8")

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    assert _version.resolve(lookup=missing, root=tmp_path) == "4.5.6"


def test_with_neither_metadata_nor_pyproject_the_version_is_visibly_unknown_never_a_stale_number(tmp_path):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    assert _version.resolve(lookup=missing, root=tmp_path) == "0+unknown"


# --- AC2: every place that reports the version agrees with pyproject ------------------------------------------------------------
def test_dash_dash_version_prints_the_pyproject_version():
    out = subprocess.run([sys.executable, "-m", "mdpipeline", "--version"], capture_output=True, text=True, timeout=60).stdout
    assert out.strip() == f"mdpipeline {PYPROJECT_VERSION}"


def test_the_update_notice_compares_against_the_pyproject_version():
    from mdpipeline import updatecheck

    assert updatecheck.installed_version() == PYPROJECT_VERSION


def test_usage_events_carry_the_pyproject_version_as_app_version():
    from mdpipeline import usage

    assert usage._globals()["app_version"] == PYPROJECT_VERSION


def test_the_mcp_server_reports_the_pyproject_version(tmp_path):
    from mdpipeline import mcp_server
    from mdpipeline.brain import init_brain

    assert mcp_server.build_server(init_brain(tmp_path)).version == PYPROJECT_VERSION


def test_the_background_record_and_the_app_page_use_the_same_version():
    from mdpipeline import uiapi  # noqa: F401 - imported so the module that fills installed_version is loaded

    assert mdpipeline.__version__ == PYPROJECT_VERSION


# --- AC3: the release script refuses files that disagree with the tag -------------------------------------------------------------
@pytest.fixture
def rel():
    spec = importlib.util.spec_from_file_location("release_script_md235", APP / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_script_md235"] = module
    spec.loader.exec_module(module)
    return module


def pyproject(version: str) -> str:
    return f'[project]\nname = "mdpipeline"\nversion = "{version}"\n'


def lock(version: str) -> str:
    return f'version = 1\n\n[[package]]\nname = "pyyaml"\nversion = "6.0.3"\n\n[[package]]\nname = "mdpipeline"\nversion = "{version}"\nsource = {{ editable = "." }}\n'


def server(top: str, package: str) -> str:
    return json.dumps({"name": "io.github.x/y", "version": top, "packages": [{"registryType": "pypi", "identifier": "mdpipeline", "version": package}]})


def test_files_that_all_carry_the_tag_version_have_no_problem(rel):
    assert rel.consistency_problems("0.1.23", pyproject("0.1.23"), lock("0.1.23"), server("0.1.23", "0.1.23")) == []


@pytest.mark.parametrize("pp,lk,top,pkg,needle", [
    ("0.1.22", "0.1.23", "0.1.23", "0.1.23", "pyproject.toml"),
    ("0.1.23", "0.1.22", "0.1.23", "0.1.23", "uv.lock"),
    ("0.1.23", "0.1.23", "0.1.22", "0.1.23", "server.json version"),
    ("0.1.23", "0.1.23", "0.1.23", "0.1.22", "packages[0]"),
])
def test_each_file_that_disagrees_with_the_tag_is_named(rel, pp, lk, top, pkg, needle):
    problems = rel.consistency_problems("0.1.23", pyproject(pp), lock(lk), server(top, pkg))
    assert len(problems) == 1 and needle in problems[0] and "0.1.23" in problems[0]


def test_all_the_problems_are_listed_not_only_the_first(rel):
    problems = rel.consistency_problems("0.1.23", pyproject("0.1.21"), lock("0.1.22"), server("0.1.20", "0.1.20"))
    assert [n in " ".join(problems) for n in ("pyproject.toml", "uv.lock", "server.json")] == [True, True, True]


def test_create_draft_and_check_ci_refuse_a_mismatch_before_touching_git_or_github(rel, capsys):
    for flag in ("--create-draft", "--check-ci"):
        calls: list[list[str]] = []
        code = rel.run([flag, "--version", "9.9.9"], runner=lambda cmd: calls.append(cmd) or (0, ""))
        assert code != 0 and calls == []
        assert "9.9.9" in capsys.readouterr().err


def test_the_real_files_of_this_repo_agree_with_each_other(rel):
    rel.check_release_files(PYPROJECT_VERSION)
