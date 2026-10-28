"""MD-121 AC1/AC2/AC5: when a pipeline is due, and when its next scheduled sync is (pure rules, no I/O)."""
import pytest

from mdpipeline.config import Pipeline
from mdpipeline.schedule import interval_seconds, next_sync, is_due

HOUR = 3600


def pipe(**kw):
    return Pipeline.new("p", ["/x"], **kw)


@pytest.mark.parametrize("value,seconds", [("15m", 900), ("1h", 3600), ("6h", 21600), ("daily", 86400), ("off", None)])
def test_interval_seconds(value, seconds):
    assert interval_seconds(value) == seconds


def test_a_pipeline_that_never_synced_is_not_due_scheduled_sync_starts_after_the_first_sync():
    assert is_due(pipe(), None, now=10 * HOUR) is False
    assert next_sync(pipe(), None, now=10 * HOUR) is None


def test_not_due_before_the_interval_has_passed():
    assert is_due(pipe(), last_run=0, now=HOUR - 1) is False


def test_due_exactly_at_the_interval():
    assert is_due(pipe(), last_run=0, now=HOUR) is True


def test_the_interval_is_per_pipeline():
    assert is_due(pipe(schedule="15m"), last_run=0, now=900) is True
    assert is_due(pipe(schedule="6h"), last_run=0, now=HOUR * 5) is False


def test_off_is_never_due_and_has_no_next_sync():
    p = pipe(schedule="off")
    assert is_due(p, last_run=0, now=100 * HOUR) is False
    assert next_sync(p, 0, now=100 * HOUR) is None


def test_a_paused_pipeline_is_never_due():
    assert is_due(pipe(paused=True), last_run=0, now=100 * HOUR) is False


def test_next_sync_is_last_run_plus_interval():
    assert next_sync(pipe(), last_run=1000, now=1200) == 1000 + HOUR


def test_an_overdue_pipeline_has_its_next_sync_now_and_is_one_run_not_a_backlog():
    """After a night of sleep the next sync is 'now' (a single catch-up), never a list of missed runs."""
    assert next_sync(pipe(), last_run=0, now=10 * HOUR) == 10 * HOUR
