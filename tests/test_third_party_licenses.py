"""MD-186 AC3/AC4: LICENSE is the unmodified Apache-2.0 text, NOTICE exists, and every third-party package we ship is listed with a license that fits."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
APACHE_2_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"   # https://www.apache.org/licenses/LICENSE-2.0.txt
COPYLEFT = ("GPL", "AGPL", "LGPL", "SSPL", "EUPL", "CC-BY-SA")
LIST_FILE = APP / "docs" / "third-party-licenses.md"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_third_party_licenses", APP / "scripts" / "gen_third_party_licenses.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_third_party_licenses"] = module
    spec.loader.exec_module(module)
    return module


def test_license_file_is_the_unmodified_apache_2_text():
    assert hashlib.sha256((APP / "LICENSE").read_bytes()).hexdigest() == APACHE_2_SHA256


def test_notice_file_names_the_project():
    text = (APP / "NOTICE").read_text(encoding="utf-8")
    assert text.startswith("Markdown Pipeline") and "Copyright" in text


def test_pyproject_declares_the_license_and_ships_the_license_files():
    project = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "Apache-2.0" and set(project["license-files"]) >= {"LICENSE", "NOTICE"}


LOCK = '''
[[package]]
name = "mdpipeline"
version = "1.0"
source = { editable = "." }
dependencies = [{ name = "a" }]
[package.dev-dependencies]
dev = [{ name = "t" }]

[[package]]
name = "a"
version = "2.0"
dependencies = [{ name = "b", marker = "sys_platform == 'win32'" }]
[[package]]
name = "b"
version = "3.0"
[[package]]
name = "t"
version = "9.0"
[[package]]
name = "unused"
version = "1.0"
'''


def test_runtime_closure_follows_dependencies_including_platform_ones_and_skips_dev_and_unused(gen):
    assert gen.runtime_packages(LOCK) == {"a": "2.0", "b": "3.0"}


def test_dev_packages_are_the_dev_group_only(gen):
    assert gen.dev_packages(LOCK) == {"t": "9.0"}


def test_the_committed_list_is_what_the_generator_makes_from_the_lock(gen):
    assert LIST_FILE.read_text(encoding="utf-8") == gen.render(APP / "uv.lock")


def test_every_runtime_package_in_the_lock_is_listed(gen):
    lock = (APP / "uv.lock").read_text(encoding="utf-8")
    text = LIST_FILE.read_text(encoding="utf-8")
    for name, version in gen.runtime_packages(lock).items():
        assert f"| {name} | {version} |" in text, name


def test_no_shipped_dependency_is_copyleft(gen):
    lock = (APP / "uv.lock").read_text(encoding="utf-8")
    bad = {n: gen.license_of(n) for n in gen.runtime_packages(lock) if any(c in gen.license_of(n).upper() for c in COPYLEFT)}
    assert bad == {}


def test_every_runtime_package_has_a_known_license(gen):
    lock = (APP / "uv.lock").read_text(encoding="utf-8")
    assert [n for n in gen.runtime_packages(lock) if gen.license_of(n) in ("", "UNKNOWN")] == []
