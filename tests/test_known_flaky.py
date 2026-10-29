"""No test may be marked flaky or re-run (MD-198, 2026-10-29). The one test that was (the missing-source browser test) turned out to be a real product bug:
the notice stayed after a new folder was chosen, and the test only passed when it caught the page's loading moment. A rerun would have hidden it."""
from __future__ import annotations

from pathlib import Path

TESTS = Path(__file__).resolve().parent


def test_no_test_is_marked_flaky_or_rerun():
    offenders = [p.name for p in sorted(TESTS.glob("test_*.py")) if p.name != Path(__file__).name
                 and ("mark.flaky" in p.read_text(encoding="utf-8") or "--reruns" in p.read_text(encoding="utf-8"))]
    assert offenders == []
