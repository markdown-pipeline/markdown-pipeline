"""MD-87: existing env:/keychain: GitHub references are imported into the vault once; after that the vault is the only source."""
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline import credentials, github_api, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.secretimport import import_references, pending_imports
from secret_search import files_containing
from uihelper import running_ui
from vaultperm import pretend_a_keychain_exists

TOKEN = "ghp_IMPORTTHISSECRET0123456"


@pytest.fixture
def world(brain, make_source, monkeypatch):
    pretend_a_keychain_exists(monkeypatch)      # the old keychain: reference is the thing being imported; Windows would refuse it up front
    fake = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    github_api.set_transport(fake)
    monkeypatch.setenv("BRAIN_TOKEN", TOKEN)
    fake._refs["other"] = fake.head()      # MD-97: a different credential on the same repo needs its own branch
    cfg = load_config(brain)
    for name, ref, branch in (("one", "env:BRAIN_TOKEN", "main"), ("two", "env:BRAIN_TOKEN", "main"), ("three", "keychain:legacy-svc", "other")):
        cfg.pipelines.append(Pipeline.new(name, [str(make_source(name + "src", {"a.md": name}))],
                                          destination={"type": "github", "repo": "acme/brain", "branch": branch, "credential": ref}))
    cfg.pipelines.append(Pipeline.new("folder", [str(make_source("fsrc", {"f.md": "F"}))]))
    save_config(brain, cfg)
    monkeypatch.setattr(credentials, "run_keychain", lambda cmd: (0, TOKEN + "\n") if "legacy-svc" in cmd else (1, ""))
    yield type("W", (), {"brain": brain, "fake": fake})
    github_api.set_transport(None)


def test_old_references_are_detected_grouped_by_reference(world):
    items = pending_imports(world.brain)
    assert {(i.ref, tuple(i.pipelines)) for i in items} == {("env:BRAIN_TOKEN", ("one", "two")), ("keychain:legacy-svc", ("three",))}


def test_until_imported_the_old_reference_keeps_working(world):
    assert sync_pipeline(world.brain, "one", trigger="cli").result == "ok" and sync_pipeline(world.brain, "three", trigger="cli").result == "ok"
    assert vault_mod.default_vault().list() == []


def test_import_moves_each_distinct_reference_into_the_vault_once_and_rewrites_the_config(world, app_data):
    results = import_references(world.brain)
    assert [r.ok for r in results] == [True, True]
    secrets = vault_mod.default_vault().list()
    assert len(secrets) == 2 and {s.kind for s in secrets} == {"github-token"}
    cfg = load_config(world.brain)
    refs = {p.name: p.destination["credential"] for p in cfg.pipelines if p.destination}
    assert refs["one"] == refs["two"] and refs["one"].startswith("vault:") and refs["three"].startswith("vault:") and refs["one"] != refs["three"]
    assert files_containing("BRAIN_TOKEN", world.brain.root) == [] and files_containing("legacy-svc", world.brain.root) == []
    assert vault_mod.default_vault().get(refs["one"][len("vault:"):]) == TOKEN
    assert pending_imports(world.brain) == []


def test_after_the_import_the_environment_variable_is_never_read_again(world, monkeypatch):
    import_references(world.brain)
    monkeypatch.setenv("BRAIN_TOKEN", "ghp_SOMETHINGELSEENTIRELY")
    monkeypatch.setattr(credentials, "run_keychain", lambda cmd: (_ for _ in ()).throw(AssertionError("the keychain was read")))
    assert sync_pipeline(world.brain, "one", trigger="cli").result == "ok" and sync_pipeline(world.brain, "three", trigger="cli").result == "ok"
    monkeypatch.delenv("BRAIN_TOKEN")
    assert sync_pipeline(world.brain, "two", trigger="cli").result == "ok"


def test_the_result_says_the_old_variable_can_be_deleted_and_never_shows_the_token(world):
    results = import_references(world.brain)
    text = " ".join(r.message for r in results)
    assert "BRAIN_TOKEN" in text and "delete" in text.lower() and "legacy-svc" in text and TOKEN not in text


def test_a_reference_that_cannot_be_read_is_reported_and_changes_nothing(world, monkeypatch):
    monkeypatch.delenv("BRAIN_TOKEN")
    before = world.brain.config_path.read_text()
    results = {r.ref: r for r in import_references(world.brain)}
    assert not results["env:BRAIN_TOKEN"].ok and "not set" in results["env:BRAIN_TOKEN"].message and results["keychain:legacy-svc"].ok
    cfg = load_config(world.brain)
    assert cfg.get("one").destination["credential"] == "env:BRAIN_TOKEN" and cfg.get("three").destination["credential"].startswith("vault:")
    assert len(vault_mod.default_vault().list()) == 1


def test_a_locked_vault_stops_the_import_without_changes(world, app_data):
    vault_mod.default_vault().add("github-token", "x", "ghp_x")          # a vault exists, then its key is lost
    app_data.keychain.lose()
    before = world.brain.config_path.read_text()
    with pytest.raises(vault_mod.VaultLocked):
        import_references(world.brain)
    assert world.brain.config_path.read_text() == before


def test_importing_twice_does_nothing_the_second_time(world):
    import_references(world.brain)
    assert import_references(world.brain) == [] and len(vault_mod.default_vault().list()) == 2


def test_the_cli_import_command_lists_then_imports(world, capsys):
    code = main(["--brain", str(world.brain.root), "vault", "import", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "env:BRAIN_TOKEN" in out and "one, two" in out and vault_mod.default_vault().list() == []
    code = main(["--brain", str(world.brain.root), "vault", "import"])
    out = capsys.readouterr().out
    assert code == 0 and "Imported" in out and TOKEN not in out and len(vault_mod.default_vault().list()) == 2
    assert main(["--brain", str(world.brain.root), "vault", "import"]) == 0 and "Nothing to import" in capsys.readouterr().out


def test_the_cli_hints_at_the_import_when_old_references_exist(world, capsys):
    main(["--brain", str(world.brain.root), "pipeline", "list"])
    assert "vault import" in capsys.readouterr().out
    main(["--brain", str(world.brain.root), "status"])
    assert "vault import" in capsys.readouterr().out


def test_the_ui_offers_the_import_as_a_needs_you_card_and_does_it(world):
    with running_ui(world.brain) as ui:
        _, act = ui.get("/api/activity")
        card = next(c for c in act["needs_you"] if c["kind"] == "import")
        assert card["items"][0]["pipelines"] and "BRAIN_TOKEN" in json.dumps(card) and act["badge"] >= 1
        status, res = ui.post("/api/vault/import", {})
        assert status == 200 and all(r["ok"] for r in res["results"]) and TOKEN not in json.dumps(res)
        _, act = ui.get("/api/activity")
    assert not any(c["kind"] == "import" for c in act["needs_you"])
    assert len(vault_mod.default_vault().list()) == 2
