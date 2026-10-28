"""MD-182 AC4 + AC5: the same source gives byte-identical files on every OS (front matter and links use "/", line ends are "\\n").

The golden files in tests/fixtures/golden_bytes/ are compared as BYTES. The only machine-specific line, `source_root` (this computer's
absolute folder), is blanked before comparing. The Windows runner runs this same test, so a pass on both runners means identical output.
"""
import os
import re
from pathlib import Path

import pytest

from conftest import write
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline

GOLDEN = Path(__file__).parent / "fixtures" / "golden_bytes"
MTIME = 1_790_000_000.0          # fixed, so source_modified is the same everywhere
NOW = 1_790_100_000.0            # fixed, so synced_at and the date in names are the same everywhere
ROOT_LINE = re.compile(rb"^(\s*source_root:).*$", re.M)


def normalised(data: bytes) -> bytes:
    return ROOT_LINE.sub(rb"\1 <machine>", data)


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    src = make_source("docs", {})
    write(src / "top.md", "# Top\n\nSee [sub](sub/deep.md) and ünïcode ✓\n", MTIME)
    write(src / "sub" / "deep.md", "---\ntitle: Deep\n---\nBody line 1\nBody line 2\n", MTIME)
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    assert sync_pipeline(brain, "p", trigger="cli", now=NOW).result == "ok"
    return brain


def written(brain):
    return {p.relative_to(brain.root).as_posix(): p.read_bytes() for p in (brain.root / "raw").rglob("*.md")}


def test_the_file_names_use_forward_slashes_and_are_the_same_everywhere(synced):
    assert sorted(written(synced)) == ["raw/p/docs/2026-09-22_top.md", "raw/p/docs/sub/2026-09-22_deep.md"]


def test_no_written_file_has_a_carriage_return(synced):
    assert all(b"\r" not in data for data in written(synced).values())


def test_provenance_paths_use_forward_slashes(synced):
    deep = written(synced)["raw/p/docs/sub/2026-09-22_deep.md"].decode("utf-8")
    assert "source_path: sub/deep.md" in deep
    assert "\\" not in normalised(deep.encode("utf-8")).decode("utf-8")      # source_root (this machine's folder) is the one allowed exception


@pytest.mark.parametrize("name", ["2026-09-22_top.md", "sub/2026-09-22_deep.md"])
def test_the_bytes_equal_the_golden_file(synced, name):
    golden = (GOLDEN / name).read_bytes() if (GOLDEN / name).exists() else None
    actual = normalised(written(synced)["raw/p/docs/" + name])
    if golden is None and os.environ.get("MDP_WRITE_GOLDEN"):
        (GOLDEN / name).parent.mkdir(parents=True, exist_ok=True)
        (GOLDEN / name).write_bytes(actual)
        golden = actual
    assert golden is not None, "golden file missing"
    assert actual == golden
