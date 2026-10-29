"""MD-187 AC1: the daily pattern of the owner's install, reproduced. A machine that is on for part of a day syncs every hour with a sign-in
target; GitHub's access token really stops working 8 hours after it was issued (fake clock). Every sync must be ok."""
import time

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api, github_signin as gs, vault as vault_mod
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.targetconfig import GitHubSpec

CLIENT_ID = "Iv1.testclient"
HOUR = 3600.0


@pytest.fixture
def world(brain, make_source, app_data, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    fake = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    fake.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    fake.install_app(["acme/brain"])
    fake.clock = lambda: now[0]
    github_api.set_transport(fake)
    gs.save_app_settings(CLIENT_ID, "md-pipeline-test")
    code = gs.request_device_code(CLIENT_ID)
    fake.authorize_device()
    tokens = gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None)
    tokens.login = "tester"
    secret_id = vault_mod.default_vault().add("github-sign-in", "tester", tokens.to_json())
    cfg = load_config(brain)
    dest = GitHubSpec("acme/brain", f"vault:{secret_id}", "main").to_dict() | {"auth": "sign-in"}
    cfg.pipelines.append(Pipeline.new("p", [str(make_source("src", {"a.md": "A"}))], destination=dest))
    save_config(brain, cfg)
    yield now, fake, brain
    github_api.set_transport(None)


def test_hourly_syncs_across_the_8_hour_expiry_are_all_ok(world):
    now, fake, brain = world
    results = []
    for _ in range(14):
        results.append(sync_pipeline(brain, "p", trigger="schedule"))
        now[0] += HOUR + 17                                  # the real schedule drifts a few seconds per hour
    assert [r.result for r in results] == ["ok"] * 14, [(r.result, r.message) for r in results if r.result != "ok"]


def test_the_first_sync_after_a_night_off_is_ok_and_so_are_the_next_ones_across_the_next_expiry(world):
    now, fake, brain = world
    assert sync_pipeline(brain, "p", trigger="schedule").result == "ok"
    now[0] += 17 * HOUR                                      # the machine was off overnight: the access token expired long ago
    seen = []
    for _ in range(12):
        seen.append(sync_pipeline(brain, "p", trigger="schedule").result)
        now[0] += HOUR + 17
    assert seen == ["ok"] * 12


def test_a_sync_that_starts_just_before_the_expiry_margin_and_runs_long_is_ok(world):
    now, fake, brain = world
    now[0] += 8 * HOUR - 130                                  # 130 s left: outside the 120 s refresh margin, so the old token is used
    fake.before("PATCH", lambda: now.__setitem__(0, now[0] + 200), times=1)   # and the sync takes long enough for it to expire before the commit
    assert sync_pipeline(brain, "p", trigger="schedule").result == "ok"
