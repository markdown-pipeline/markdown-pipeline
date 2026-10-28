"""MD-103 (UI backend): the editor draft carries the On/Off switch; preview, view mode, home rows and Needs you treat an Off source as off,
not as a problem."""
import json
import shutil

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source
from uihelper import running_ui


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


@pytest.fixture
def world(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": f"doc {i}" for i in range(6)})
    add_pipeline(Pipeline("p", [Source(str(docs), "docs"), jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    return type("W", (), {"brain": brain, "docs": docs, "fake": fake})


def draft_of(ui):
    return ui.get("/api/pipelines/p")[1]["draft"]


def test_the_editor_draft_carries_each_sources_switch_and_a_save_keeps_it(world):
    with running_ui(world.brain) as ui:
        d = draft_of(ui)
        assert [s["enabled"] for s in d["sources"]] == [True, True]
        d["sources"][0]["enabled"] = False
        status, res = ui.put("/api/pipelines/p", {"draft": d})
        assert status == 200, res
        assert [s["enabled"] for s in draft_of(ui)["sources"]] == [False, True]
    assert [s.enabled for s in load_config(world.brain).get("p").sources] == [False, True]


def test_saving_with_every_source_off_is_refused_with_the_plain_message(world):
    with running_ui(world.brain) as ui:
        d = draft_of(ui)
        for s in d["sources"]:
            s["enabled"] = False
        status, res = ui.put("/api/pipelines/p", {"draft": d})
        _, pv = ui.post("/api/preview", {"draft": d, "editing": "p"})
    assert status == 422 and "Turn a source on, or pause the pipeline." in json.dumps(res)
    assert pv["validation"]["can_save"] is False and [s.enabled for s in load_config(world.brain).get("p").sources] == [True, True]


def test_the_preview_lists_an_off_source_as_kept_not_as_an_error(world):
    with running_ui(world.brain) as ui:
        d = draft_of(ui)
        d["sources"][0]["enabled"] = False
        _, pv = ui.post("/api/preview", {"draft": d, "editing": "p"})
    docs = next(g for g in pv["preview"]["groups"] if g["label"] == "docs")
    assert docs["status"] == "off" and docs["error"] == "" and {i["note"] for i in docs["items"]} == {"source off: kept"}
    assert {i["op"] for i in docs["items"]} == {"skip"} and pv["validation"]["errors"] == [] and pv["preview"]["counts"]["delete"] == 0


def test_the_preview_of_an_off_jira_source_makes_no_request(world):
    with running_ui(world.brain) as ui:
        d = draft_of(ui)
        d["sources"][1]["enabled"] = False
        world.fake.requests.clear()
        _, pv = ui.post("/api/preview", {"draft": d, "editing": "p"})
    assert world.fake.requests == [] and next(g for g in pv["preview"]["groups"] if g["label"] == "md-jira")["status"] == "off"


def test_view_mode_shows_an_off_source_without_checking_it_and_without_a_problem(world):
    cfg = load_config(world.brain)
    cfg.get("p").sources[0].enabled = False
    from mdpipeline.config import save_config

    save_config(world.brain, cfg)
    shutil.rmtree(world.docs)                       # moved folder: while Off that is nobody's business
    with running_ui(world.brain) as ui:
        view = ui.get("/api/pipelines/p/view")[1]
        plan = ui.get("/api/pipelines/p/plan")[1]
    [docs, jira] = view["config"]["sources"]
    assert docs["enabled"] is False and docs["check"]["ok"] is True and "Off" in docs["check"]["message"] and jira["enabled"] is True
    assert plan["problems"] == [] and plan["sources"]["docs"]["ok"] is True and plan["sources"]["docs"]["off"] is True
    assert [c for c in view["needs_you"] if c["kind"] != "import"] == [] and view["status"]["status"] == "ok"      # (the import card is about the Jira env references)


def test_home_row_says_how_many_sources_are_off_and_it_is_not_a_needs_you_item(world):
    cfg = load_config(world.brain)
    cfg.get("p").sources[1].enabled = False
    from mdpipeline.config import save_config

    save_config(world.brain, cfg)
    sync_pipeline(world.brain, "p", trigger="cli")
    with running_ui(world.brain) as ui:
        state = ui.get("/api/state")[1]
        activity = ui.get("/api/activity")[1]
    row = state["pipelines"][0]
    assert "1 of 2 sources off" in row["summary"] and row["status"] == "ok" and [c for c in activity["needs_you"] if c["kind"] != "import"] == []
    assert [s["enabled"] for s in row["sources"]] == [True, False]
