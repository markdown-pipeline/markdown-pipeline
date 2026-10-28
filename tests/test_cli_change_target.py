"""MD-85 (CLI): `pipeline change-target`: plan first, apply with --apply (or a yes), old target untouched."""
import io
import json

import pytest

from conftest import tree_snapshot, write
from fake_github import FakeGitHub
from mdpipeline import cli, github_api, github_signin as gs, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.github_target import STATE_PATH

TOKEN = "ghp_CLICHANGESECRET0123456"


@pytest.fixture
def fake(app_data):
    f = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    f.enable_app(client_id="Iv1.cli", slug="cli-app")
    f.install_app(["acme/brain"])
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


@pytest.fixture
def local(brain, brain_dir, make_source, add_pipeline, fake):
    add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A", "b.md": "B"}))], name_policy="date-name"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    fake.commit_as_other({p.relative_to(brain_dir).as_posix(): p.read_bytes() for p in (brain_dir / "raw").rglob("*.md")}, "owner pushes the brain")
    return type("L", (), {"brain": brain, "root": brain_dir})


def run(capsys, monkeypatch, *argv, brain, stdin=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    try:
        code = main(["--brain", str(brain.root), *argv])
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_without_apply_it_shows_the_plan_and_changes_nothing(local, fake, capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: False)
    before, config, head = tree_snapshot(local.root), local.brain.config_path.read_text(), fake.head()
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--github", "acme/brain", "--branch", "main", "--stdin", brain=local.brain, stdin=TOKEN + "\n")
    assert code == 0, err
    assert "unchanged 2" in out and "created 0" in out and "Stays behind" in out and "Nothing was changed" in out
    assert tree_snapshot(local.root) == before and local.brain.config_path.read_text() == config and fake.head() == head
    assert vault_mod.default_vault().list() == [] and TOKEN not in out + err


def test_with_apply_it_stores_the_token_switches_the_target_and_leaves_the_old_one_untouched(local, fake, capsys, monkeypatch):
    before = tree_snapshot(local.root / "raw")
    state_bytes = (local.root / ".mdpipeline/state.json").read_bytes()
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--github", "acme/brain", "--branch", "main", "--stdin", "--apply", brain=local.brain, stdin=TOKEN + "\n")
    assert code == 0, err
    assert tree_snapshot(local.root / "raw") == before and (local.root / ".mdpipeline/state.json").read_bytes() == state_bytes
    [secret] = vault_mod.default_vault().list()
    dest = load_config(local.brain).get("p").destination
    assert dest["credential"] == f"vault:{secret.id}" and dest["repo"] == "acme/brain" and "Moved" in out and TOKEN not in out + err
    assert json.loads(fake.read(STATE_PATH))["pipelines"]["p"]["files"]
    code, out, _ = run(capsys, monkeypatch, "sync", "p", "--dry-run", brain=local.brain)
    assert "unchanged 2" in out and "created 0" in out


def test_interactively_a_yes_applies_and_a_no_keeps_everything(local, fake, capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": TOKEN)
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--github", "acme/brain", brain=local.brain)
    assert code == 0 and load_config(local.brain).get("p").destination is None and vault_mod.default_vault().list() == []
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--github", "acme/brain", brain=local.brain)
    assert code == 0, err and load_config(local.brain).get("p").destination["type"] == "github"


def test_a_wrong_token_changes_nothing_and_stores_nothing(local, fake, capsys, monkeypatch):
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--github", "acme/brain", "--stdin", "--apply", brain=local.brain, stdin="ghp_WRONGWRONG000\n")
    assert code == 2 and "rejected" in err and "ghp_WRONG" not in out + err
    assert load_config(local.brain).get("p").destination is None and vault_mod.default_vault().list() == []


def test_a_directory_target(local, tmp_path, capsys, monkeypatch):
    other = tmp_path / "vault"
    for p in (local.root / "raw").rglob("*.md"):
        write(other / p.relative_to(local.root), p.read_text())
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--directory", str(other), "--apply", brain=local.brain)
    assert code == 0, err and load_config(local.brain).get("p").destination == {"type": "directory", "path": str(other)}


def test_back_to_the_brains_own_folder(local, tmp_path, capsys, monkeypatch):
    other = tmp_path / "vault"
    other.mkdir()
    run(capsys, monkeypatch, "pipeline", "change-target", "p", "--directory", str(other), "--apply", brain=local.brain)
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--brain-folder", "--apply", brain=local.brain)
    assert code == 0, err and load_config(local.brain).get("p").destination is None


def test_the_same_target_and_a_missing_target_are_usage_errors(local, capsys, monkeypatch):
    assert run(capsys, monkeypatch, "pipeline", "change-target", "p", "--brain-folder", brain=local.brain)[0] == 2
    assert run(capsys, monkeypatch, "pipeline", "change-target", "p", brain=local.brain)[0] == 2
    assert run(capsys, monkeypatch, "pipeline", "change-target", "nope", "--brain-folder", brain=local.brain)[0] == 2


def test_with_sign_in(local, fake, capsys, monkeypatch):
    gs.save_app_settings("Iv1.cli", "cli-app")
    monkeypatch.setattr(gs.time, "sleep", lambda s: fake.authorize_device())
    code, out, err = run(capsys, monkeypatch, "pipeline", "change-target", "p", "--github", "acme/brain", "--auth", "sign-in", "--apply", brain=local.brain)
    assert code == 0, err and "Signed in as tester" in out
    dest = load_config(local.brain).get("p").destination
    assert dest["auth"] == "sign-in" and vault_mod.default_vault().list()[0].kind == "github-sign-in"


def test_the_move_is_logged_without_secrets(local, fake, capsys, monkeypatch):
    run(capsys, monkeypatch, "pipeline", "change-target", "p", "--github", "acme/brain", "--stdin", "--apply", brain=local.brain, stdin=TOKEN + "\n")
    text = (local.root / ".mdpipeline/log.jsonl").read_text()
    assert "change_target" in text and TOKEN not in text
