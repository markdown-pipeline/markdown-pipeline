"""MD-153 AC2/AC4/AC6: what a long first-run step is doing right now, for the screen to poll."""
import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, progress
from test_jira_sync import EMAIL, TOKEN
from uihelper import running_ui


@pytest.fixture(autouse=True)
def _clean():
    progress.finish()
    yield
    progress.finish()


def test_nothing_is_reported_when_no_step_is_active():
    progress.report("reading", 3, 10)
    assert progress.current() is None


def test_an_active_step_reports_phase_counts_and_a_plain_sentence():
    progress.start("reading", total=210, noun="issues", source="Jira")
    progress.report("reading", 120)
    cur = progress.current()
    assert (cur["phase"], cur["done"], cur["total"]) == ("reading", 120, 210)
    assert cur["text"] == "Reading issues from Jira… 120 of 210"


def test_listing_without_a_known_total_says_how_many_so_far():
    progress.start("listing", noun="issues", source="Jira")
    progress.report("listing", 100)
    assert progress.current()["text"] == "Finding issues in Jira… 100 so far"


def test_writing_phase_sentence_and_finish_clears():
    progress.start("writing", total=5, noun="files")
    progress.report("writing", 2)
    assert progress.current()["text"] == "Writing files… 2 of 5"
    progress.finish()
    assert progress.current() is None


def _jira(n):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for i in range(n):
        f.add_issue("ACME", f"Issue number {i}")
    jira_api.set_transport(f, sleep=lambda s: None)
    return f


def test_review_emits_progress_events_and_clears_when_done(brain):
    seen = []
    progress.listen(seen.append)
    f = _jira(250)
    try:
        with running_ui(brain) as ui:
            _, res = ui.post("/api/first-run/review", {"source": "jira", "site": "acme", "email": EMAIL, "token": TOKEN})
            assert ui.get("/api/first-run/activity")[1] == {"activity": None}
    finally:
        jira_api.set_transport(None)
        progress.listen(None)
    assert res["ok"] is True
    assert any(e["phase"] == "listing" and e["done"] >= 100 for e in seen), seen[:5]


def test_write_reads_in_parallel_reports_every_step_and_writes_all_files(brain, tmp_path):
    seen = []
    progress.listen(seen.append)
    f = _jira(120)
    dest = tmp_path / "out"
    try:
        with running_ui(brain) as ui:
            _, res = ui.post("/api/first-run/write", {"source": "jira", "site": "acme", "email": EMAIL, "token": TOKEN, "dest": str(dest)})
    finally:
        jira_api.set_transport(None)
        progress.listen(None)
    assert res["ok"] is True and res["files"] == 120 and len(list(dest.rglob("*.md"))) == 120
    reading = [e for e in seen if e["phase"] == "reading"]
    writing = [e for e in seen if e["phase"] == "writing"]
    assert reading and reading[-1]["done"] == reading[-1]["total"] == 120
    assert writing and writing[-1]["done"] == writing[-1]["total"] == 120
    assert progress.current() is None
