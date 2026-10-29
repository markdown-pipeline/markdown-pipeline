"""MD-198 (registry R3): what may go to PyPI. The gate refuses a build whose metadata, version or contents are wrong; the workflow publishes only behind it."""
from __future__ import annotations

import importlib.util
import io
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parent.parent
WORKFLOW = APP / ".github" / "workflows" / "publish-pypi.yml"

GOOD_META = ("Metadata-Version: 2.4\nName: mdpipeline\nVersion: 0.1.10\nAuthor: Avi Koren\nAuthor-email: Avi Koren <maintainers@markdownpipeline.com>\n"
             "License-Expression: Apache-2.0\nLicense-File: LICENSE\n\nShort README text\n")


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("pypi_gate", APP / "scripts" / "pypi_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pypi_gate"] = module
    spec.loader.exec_module(module)
    return module


def make_wheel(folder: Path, meta: str = GOOD_META, version: str = "0.1.10", extra: dict[str, str] | None = None) -> Path:
    path = folder / f"mdpipeline-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"mdpipeline-{version}.dist-info/METADATA", meta)
        z.writestr("mdpipeline/__init__.py", "")
        z.writestr("mdpipeline/usage_events.py", "install_completed")
        z.writestr("mdpipeline/usage.py", "is_internal")
        z.writestr("mdpipeline/defaults.json", '{"github_app": {"client_id": "Iv23liTEST", "slug": "markdown-pipeline"}}')
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return path


def make_sdist(folder: Path, meta: str = GOOD_META, version: str = "0.1.10") -> Path:
    path = folder / f"mdpipeline-{version}.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        data = meta.encode()
        info = tarfile.TarInfo(f"mdpipeline-{version}/PKG-INFO")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def dist(tmp_path):
    make_wheel(tmp_path)
    make_sdist(tmp_path)
    return tmp_path


# --- the gate on a good build ---------------------------------------------------------------------------------------------------
def test_a_good_wheel_and_sdist_pass(gate, dist):
    assert gate.problems(dist, "0.1.10") == []


# --- AC2: metadata -----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad,why", [
    (GOOD_META.replace("License-Expression: Apache-2.0", "License-Expression: MIT"), "Apache-2.0"),
    (GOOD_META.replace("License-Expression: Apache-2.0\n", ""), "Apache-2.0"),
    (GOOD_META.replace("maintainers@markdownpipeline.com", "someone@example.com"), "maintainers@markdownpipeline.com"),
    (GOOD_META.replace("Author-email: Avi Koren <maintainers@markdownpipeline.com>", "Author-email: Avi Koren <maintainers@markdownpipeline.com>, Avi <a@example.com>"), "other"),
])
def test_wrong_metadata_in_the_wheel_is_refused(gate, tmp_path, bad, why):
    make_wheel(tmp_path, meta=bad)
    make_sdist(tmp_path)
    found = gate.problems(tmp_path, "0.1.10")
    assert found and any(why in p for p in found)


def test_wrong_metadata_in_the_sdist_is_refused_too(gate, tmp_path):
    make_wheel(tmp_path)
    make_sdist(tmp_path, meta=GOOD_META.replace("Apache-2.0", "MIT"))
    assert any("sdist" in p for p in gate.problems(tmp_path, "0.1.10"))


def test_any_other_address_anywhere_in_the_metadata_is_refused(gate, tmp_path):
    make_wheel(tmp_path, meta=GOOD_META.replace("\n\nShort", "\nProject-URL: x, mailto:someone@example.com\n\nShort"))
    make_sdist(tmp_path)
    assert gate.problems(tmp_path, "0.1.10")


# --- the version must be the release's -------------------------------------------------------------------------------------------------
def test_a_build_whose_version_is_not_the_release_tag_is_refused(gate, dist):
    assert any("0.2.0" in p for p in gate.problems(dist, "0.2.0"))


def test_a_tag_with_a_v_prefix_is_the_same_version(gate, dist):
    assert gate.problems(dist, "v0.1.10") == []


# --- exactly one wheel and one sdist, nothing else ------------------------------------------------------------------------------------
def test_a_missing_sdist_is_refused(gate, tmp_path):
    make_wheel(tmp_path)
    assert any("sdist" in p for p in gate.problems(tmp_path, "0.1.10"))


def test_a_stray_file_in_the_dist_folder_is_refused(gate, dist):
    (dist / "notes.txt").write_text("x")
    assert any("notes.txt" in p for p in gate.problems(dist, "0.1.10"))


def test_the_gitignore_that_uv_build_writes_into_the_folder_is_not_a_stray_file(gate, dist):
    (dist / ".gitignore").write_text("*")                  # `uv build --out-dir` always leaves this file next to the build
    assert gate.problems(dist, "0.1.10") == []


def test_a_real_uv_build_passes_the_gate(gate, tmp_path):
    subprocess.run(["uv", "build", "--out-dir", str(tmp_path), str(APP)], check=True, capture_output=True)
    version = re.search(r'^version\s*=\s*"([^"]+)"', (APP / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    assert gate.problems(tmp_path, version) == []


# --- contents: the same secret rules as the GitHub release, applied to the wheel --------------------------------------------------------
def test_a_secret_inside_the_wheel_is_refused(gate, tmp_path):
    make_wheel(tmp_path, extra={"mdpipeline/x.py": "T = '" + "ghp_" + "a" * 36 + "'"})
    make_sdist(tmp_path)
    assert any("GitHub token" in p for p in gate.problems(tmp_path, "0.1.10"))


# --- AC4: the PyPI page shows the short README ------------------------------------------------------------------------------------------
def test_the_package_readme_is_the_short_front_page():
    import tomllib

    project = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["readme"] == "docs/pypi-readme.md" and len((APP / "README.md").read_text(encoding="utf-8").splitlines()) < 80


# --- the command line the workflow runs ---------------------------------------------------------------------------------------------------
def test_the_command_exits_0_for_a_good_build_and_1_for_a_bad_one(dist, tmp_path):
    ok = subprocess.run([sys.executable, str(APP / "scripts" / "pypi_gate.py"), str(dist), "--version", "0.1.10"], capture_output=True, text=True)
    bad = subprocess.run([sys.executable, str(APP / "scripts" / "pypi_gate.py"), str(dist), "--version", "9.9.9"], capture_output=True, text=True)
    assert ok.returncode == 0 and bad.returncode == 1 and "9.9.9" in bad.stdout


# --- AC3: the workflow: gate first, Trusted Publishing, no token in the repo ---------------------------------------------------------------
# The workflow file reaches the public repo only after the owner's PyPI steps are confirmed (MD-198 product decision, 2026-10-29):
# until then these tests are skipped there, with the reason in the run summary, and run in the private tree.
needs_workflow = pytest.mark.skipif(not WORKFLOW.exists(), reason="publish-pypi.yml is not in this tree yet (added to the public repo after the owner's PyPI steps)")


def load():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@needs_workflow
def test_the_workflow_runs_on_a_published_release_and_by_hand():
    on = load()[True]
    assert on["release"]["types"] == ["published"] and "workflow_dispatch" in on


@needs_workflow
def test_publishing_needs_the_tests_the_gate_and_the_clean_container_check():
    jobs = load()["jobs"]
    assert set(jobs["publish"]["needs"]) == {"tests", "build", "clean-install"}


@needs_workflow
def test_only_the_publish_job_can_get_an_identity_token_and_only_for_a_release():
    wf = load()
    assert wf["permissions"] == {"contents": "read"}
    for name, job in wf["jobs"].items():
        grants = job.get("permissions", {})
        assert ("id-token" in grants) == (name == "publish"), name
    assert "release" in load()["jobs"]["publish"]["if"] and load()["jobs"]["publish"]["environment"] == "pypi"


@needs_workflow
def test_the_workflow_uses_trusted_publishing_and_holds_no_token():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "uv publish" in text and "--trusted-publishing always" in text
    assert "PYPI_TOKEN" not in text and "UV_PUBLISH_TOKEN" not in text and "secrets." not in text


@needs_workflow
def test_a_manual_run_never_publishes():
    assert "workflow_dispatch" not in load()["jobs"]["publish"]["if"] and "github.event_name == 'release'" in load()["jobs"]["publish"]["if"]


@needs_workflow
def test_the_clean_install_job_runs_uvx_help_from_the_built_wheel_in_a_container():
    job = load()["jobs"]["clean-install"]
    text = yaml.safe_dump(job)
    assert "container" in job or "docker run" in text
    assert "uvx" in text and "--help" in text


@needs_workflow
def test_the_runner_is_pinned_like_the_test_workflow():
    for name, job in load()["jobs"].items():
        assert job["runs-on"] == "ubuntu-24.04", name
