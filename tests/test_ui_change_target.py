"""MD-85 (UI backend): the editor previews a change of target (plan + what stays behind) and applies it only when asked."""
import json

import pytest

from conftest import tree_snapshot
from fake_github import FakeGitHub
from mdpipeline import github_api, vault as vault_mod
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.github_target import STATE_PATH
from uihelper import running_ui

TOKEN = "ghp_UICHANGESECRET0123456"
GH = {"type": "github", "repo": "acme/brain", "branch": "main", "auth": "token"}


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


@pytest.fixture
def local(brain, brain_dir, make_source, add_pipeline, fake):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", trigger="cli")
    fake.commit_as_other({p.relative_to(brain_dir).as_posix(): p.read_bytes() for p in (brain_dir / "raw").rglob("*.md")}, "owner pushes the brain")
    return type("L", (), {"brain": brain, "root": brain_dir, "src": src})


def draft(local, **kw):
    d = {"name": "p", "sources": [{"path": str(local.src), "label": "docs"}], "target": "raw/{pipeline}/{source}/{relpath}", "include": ["**/*.md"],
         "exclude": [".git/**"], "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5, "name_policy": "date-name"}
    d.update(kw)
    return d


def test_the_preview_of_a_changed_target_shows_the_plan_and_what_stays_behind(local, fake):
    with running_ui(local.brain) as ui:
        _, tested = ui.post("/api/target/test", {"destination": GH, "token": TOKEN})
        _, pv = ui.post("/api/preview", {"draft": draft(local, destination={**GH, "pending": tested["pending"]}), "editing": "p"})
    assert pv["change"]["owned_files"] == 2 and pv["change"]["missing"] == [] and pv["change"]["stays_behind"] == 2
    assert pv["preview"]["counts"]["skip"] == 2 and pv["preview"]["counts"]["create"] == 0 and "stay untouched" in pv["change"]["stays_behind_text"]


def test_an_edit_without_change_target_cannot_move_the_pipeline(local, fake):
    with running_ui(local.brain) as ui:
        _, tested = ui.post("/api/target/test", {"destination": GH, "token": TOKEN})
        status, res = ui.put("/api/pipelines/p", {"draft": draft(local, destination={**GH, "pending": tested["pending"]})})
    assert status == 422 and "Change target" in res["error"] and load_config(local.brain).get("p").destination is None


def test_change_target_applies_the_move_with_the_token_in_the_vault_and_the_old_target_untouched(local, fake):
    before = tree_snapshot(local.root / "raw")
    with running_ui(local.brain) as ui:
        _, tested = ui.post("/api/target/test", {"destination": GH, "token": TOKEN})
        status, res = ui.put("/api/pipelines/p", {"draft": draft(local, destination={**GH, "pending": tested["pending"]}), "change_target": True, "pending": tested["pending"]})
        assert status == 200, res
        _, state = ui.get("/api/state")
    assert tree_snapshot(local.root / "raw") == before
    [secret] = vault_mod.default_vault().list()
    dest = load_config(local.brain).get("p").destination
    assert dest["credential"] == f"vault:{secret.id}" and dest["repo"] == "acme/brain" and "pending" not in dest
    assert json.loads(fake.read(STATE_PATH))["pipelines"]["p"]["files"] and TOKEN not in json.dumps(state) + json.dumps(res)
    assert state["pipelines"][0]["destination"]["repo"] == "acme/brain"


def test_a_refused_change_leaves_everything_as_it_was(local, fake, tmp_path):
    with running_ui(local.brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft(local, destination={"type": "directory", "path": str(local.root)}), "change_target": True})
    assert status == 422 and "already" in res["error"] and load_config(local.brain).get("p").destination is None


def test_a_move_to_a_folder_from_the_editor(local, tmp_path):
    other = tmp_path / "vault"
    other.mkdir()
    with running_ui(local.brain) as ui:
        _, pv = ui.post("/api/preview", {"draft": draft(local, destination={"type": "directory", "path": str(other)}), "editing": "p"})
        assert pv["change"]["missing"] and pv["preview"]["counts"]["create"] == 2
        status, res = ui.put("/api/pipelines/p", {"draft": draft(local, destination={"type": "directory", "path": str(other)}), "change_target": True})
    assert status == 200 and load_config(local.brain).get("p").destination == {"type": "directory", "path": str(other)}
