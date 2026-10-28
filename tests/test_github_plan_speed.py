"""MD-128: the next-sync plan of a GitHub target reads the owned files' contents in parallel, once each, and a failed read is never a delete."""
import threading
import time

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api
from mdpipeline.brain import find_brain
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.targetconfig import GitHubSpec

TOKEN = "ghp_SPEEDSECRET0123456789"
FILES = 40
LATENCY = 0.05


class SlowTransport:
    """The fake plus a network delay per request (outside the fake's lock) and a count of requests in flight."""

    def __init__(self, fake: FakeGitHub) -> None:
        self.fake, self.latency = fake, 0.0
        self.now = self.peak = 0
        self.lock = threading.Lock()

    def __call__(self, method, url, headers, body):
        blob = "/git/blobs/" in url and method == "GET"
        if blob:
            with self.lock:
                self.now += 1
                self.peak = max(self.peak, self.now)
        try:
            time.sleep(self.latency)
            return self.fake(method, url, headers, body)
        finally:
            if blob:
                with self.lock:
                    self.now -= 1


@pytest.fixture
def world(brain, add_pipeline, make_source, monkeypatch):
    monkeypatch.setenv("BRAIN_TOKEN", TOKEN)
    fake = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN)
    slow = SlowTransport(fake)
    github_api.set_transport(slow)
    src = make_source("docs", {f"n{i}.md": f"note {i}" for i in range(FILES)})
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], name_policy="original", destination=GitHubSpec("acme/brain", "env:BRAIN_TOKEN", "main").to_dict()))
    sync_pipeline(brain, "p")
    yield find_brain(brain.root), src, fake, slow      # a fresh brain object: no file contents cached from the sync, like a new process
    github_api.set_transport(None)


def blob_gets(fake):
    return [p for m, p in fake.requests if m == "GET" and "/git/blobs/" in p]


def test_the_dry_run_reads_owned_files_in_parallel(world):
    brain, _, fake, slow = world
    slow.latency = LATENCY
    started = time.monotonic()
    res = sync_pipeline(brain, "p", dry_run=True)
    took = time.monotonic() - started
    assert res.result == "ok" and res.plan.counts()["unchanged"] == FILES
    assert slow.peak > 1
    assert took < FILES * LATENCY / 2          # sequential would take FILES * LATENCY


def test_each_owned_file_is_downloaded_once_per_plan(world):
    brain, _, fake, slow = world
    before = len(blob_gets(fake))
    sync_pipeline(brain, "p", dry_run=True)
    assert len(blob_gets(fake)) - before <= FILES + 1         # the state file may be read too


def test_a_failed_read_during_the_plan_is_an_error_never_a_delete(world):
    brain, src, fake, slow = world
    fake.fail("GET", "/git/blobs/", status=500, times=100)
    res = sync_pipeline(brain, "p", dry_run=True)
    assert res.plan is None or res.plan.counts()["deleted"] == 0
    assert res.result != "ok"


def test_opening_view_mode_needs_no_file_download_so_it_does_not_wait_for_the_plan(world):
    from uihelper import running_ui

    brain, _, fake, slow = world
    before = len(fake.requests)
    with running_ui(brain) as ui:
        status, res = ui.get("/api/pipelines/p/view")
    new = fake.requests[before:]
    assert status == 200 and res["status"]["status"] == "ok"
    assert len([1 for m, p in new if "/git/blobs/" in p and m == "GET"]) <= 1      # the state file at most
    assert len(new) <= 8


def test_the_plan_route_still_computes_the_plan_for_a_github_target(world):
    from uihelper import running_ui

    brain, src, fake, slow = world
    (src / "n0.md").write_text("changed")
    with running_ui(brain) as ui:
        status, res = ui.get("/api/pipelines/p/plan")
    assert status == 200 and res["preview"]["counts"]["update"] == 1 and res["preview"]["counts"]["delete"] == 0
