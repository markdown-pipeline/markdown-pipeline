"""MD-184/MD-179: a test that cannot run on Windows says why, and the reason is listed - never a silent skip."""
from __future__ import annotations

import pytest

import winsupport


def test_marker_reason_is_read_from_the_marker() -> None:
    marker = pytest.mark.windows_unsupported("chmod 0 does not make a file unreadable on Windows").mark
    assert winsupport.marker_reason(marker) == "chmod 0 does not make a file unreadable on Windows"


@pytest.mark.parametrize("marker", [pytest.mark.windows_unsupported.mark, pytest.mark.windows_unsupported("  ").mark])
def test_marker_without_a_reason_is_refused(marker) -> None:
    with pytest.raises(ValueError, match="needs a reason"):
        winsupport.marker_reason(marker)


def test_is_root_is_false_where_there_is_no_root_concept(monkeypatch) -> None:
    monkeypatch.delattr(winsupport.os, "geteuid", raising=False)
    assert winsupport.is_root() is False


def test_lines_for_the_summary_list_each_skipped_test_with_its_reason() -> None:
    assert winsupport.summary_lines([("tests/a.py::t1", "why one"), ("tests/b.py::t2", "why two")]) == [
        "Not run on Windows (2):", "  tests/a.py::t1: why one", "  tests/b.py::t2: why two"]


def test_summary_is_empty_when_nothing_was_skipped() -> None:
    assert winsupport.summary_lines([]) == []
