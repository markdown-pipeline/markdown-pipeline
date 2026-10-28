"""MD-186 AC8: the built wheel names the project address as its author, never a personal one."""
from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
PROJECT_EMAIL = "maintainers@markdownpipeline.com"


@pytest.fixture(scope="module")
def wheel_metadata(tmp_path_factory: pytest.TempPathFactory) -> str:
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out), str(APP)], check=True, capture_output=True)
    (wheel,) = out.glob("mdpipeline-*.whl")
    with zipfile.ZipFile(wheel) as z:
        name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        return z.read(name).decode("utf-8")


def test_wheel_metadata_carries_the_project_email(wheel_metadata: str) -> None:
    assert f"<{PROJECT_EMAIL}>" in wheel_metadata


def test_wheel_metadata_keeps_the_author_name(wheel_metadata: str) -> None:
    assert "Avi Koren" in wheel_metadata


def test_wheel_metadata_has_no_other_email(wheel_metadata: str) -> None:
    headers = wheel_metadata.split("\n\n", 1)[0]  # the README text after the headers may hold example addresses
    assert set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", headers)) == {PROJECT_EMAIL}
