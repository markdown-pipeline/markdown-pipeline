"""MD-89: the P3.1 release gate (PT-S6): per-pipeline targets, the vault and sign-in tokens. Run alone with:

    uv run pytest tests/test_release_gate_p31.py

Everything runs against the fake GitHub and a fake keychain: no real network, no real keychain. Together with
tests/test_data_loss.py (MD-30), tests/test_data_loss_names.py (MD-41) and tests/test_data_loss_github.py (MD-69) this is the gate.
Scenario numbers match PT-S6 / the acceptance criteria of MD-89.
"""
import asyncio
import io
import json

import pytest
import yaml

from conftest import tree_snapshot, write
from fake_github import FakeGitHub
from mcp.client import Client
from mdpipeline import cli, github_api, github_signin as gs, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_all, sync_pipeline
from mdpipeline.mcp_server import build_server
from mdpipeline.targetconfig import GitHubSpec
from mdpipeline.uiactivity import activity_view
from secret_search import files_containing, repo_containing
from uihelper import running_ui

PAT = "ghp_GATEPATSECRET0123456789"
CLIENT_ID = "Iv1.gateclient"


def cli_run(capsys, *argv, brain=None, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    try:
        code = main((["--brain", str(brain.root)] if brain else []) + list(argv))
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def world(brain, tmp_path, make_source, app_data, monkeypatch):
    """One brain, one fake GitHub (PAT and GitHub App), a PAT pipeline, a sign-in pipeline and a folder pipeline."""
    fake = FakeGitHub(repo="acme/brain", branch="main", token=PAT, files={"wiki/index.md": b"curated", "raw/hand/note.md": b"hand written"})
    fake.enable_app(client_id=CLIENT_ID, slug="gate-app")
    fake.install_app(["acme/brain"])
    github_api.set_transport(fake)
    gs.save_app_settings(CLIENT_ID, "gate-app")
    monkeypatch.setattr(gs.time, "sleep", lambda s: fake.authorize_device())
    pat_id = vault_mod.default_vault().add("github-token", "gate pat", PAT)
    code = gs.request_device_code(CLIENT_ID)
    fake.authorize_device()
    tokens = gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None)
    tokens.login = "tester"
    si_id = vault_mod.default_vault().add("github-sign-in", "gate sign-in", tokens.to_json())
    fake._refs["signin"] = fake.head()               # MD-97: pipelines on one repo + branch must share a credential, so the sign-in pipeline has its own branch
    cfg = load_config(brain)
    for name, dest in (("viapat", GitHubSpec("acme/brain", f"vault:{pat_id}", "main").to_dict()),
                       ("viasignin", GitHubSpec("acme/brain", f"vault:{si_id}", "signin", auth="sign-in").to_dict() | {"auth": "sign-in"})):
        cfg.pipelines.append(Pipeline.new(name, [str(make_source(name + "src", {"a.md": "A " + name, "b.md": "B " + name}))], destination=dest))
    cfg.pipelines.append(Pipeline.new("folder", [str(make_source("foldersrc", {"f.md": "F"}))]))
    save_config(brain, cfg)
    yield type("World", (), {"brain": brain, "fake": fake, "tokens": tokens, "pat_id": pat_id, "si_id": si_id, "tmp": tmp_path, "keychain": app_data.keychain,
                             "app_dir": app_data.dir, "make_source": staticmethod(make_source)})
    github_api.set_transport(None)


def raw_files(fake):
    return {p: d for p, d in fake.files().items() if p.startswith("raw/") or p.startswith(".mdpipeline/")}


# 1 -----------------------------------------------------------------------------------------------------------------------
def test_01_a_brain_level_config_migrates_with_a_backup_and_a_zero_change_plan(gh):
    gh.pipeline("a", {"a.md": "A"}, name_policy="date-name")
    gh.pipeline("b", {"b.md": "B", "sub/c.md": "C"}, name_policy="date-source-name")
    assert all(r.result == "ok" for r in sync_all(gh.brain, trigger="cli"))
    modern = yaml.safe_load(gh.brain.config_path.read_text())
    target = modern["pipelines"][0]["destination"]
    for p in modern["pipelines"]:
        p.pop("destination")
    original = yaml.safe_dump({"version": 1, "target": target, "pipelines": modern["pipelines"]}, sort_keys=False)
    gh.brain.config_path.write_text(original)                                   # a pre-MD-78 configuration on disk
    gh.brain._cache.clear()
    files_before = gh.fake.files()
    for r in sync_all(gh.brain, trigger="cli", dry_run=True):
        c = r.counts()
        assert (c["created"], c["updated"], c["renamed"], c["deleted"], c["conflicts"]) == (0, 0, 0, 0, 0), r.pipeline
    assert gh.fake.files() == files_before
    assert (gh.brain.config_dir / "backups" / "config-before-per-pipeline-target.yaml").read_text() == original
    assert "target" not in yaml.safe_load(gh.brain.config_path.read_text())
    assert all(p.destination == load_config(gh.brain).pipelines[0].destination for p in load_config(gh.brain).pipelines)


# 2 -----------------------------------------------------------------------------------------------------------------------
def test_02_two_pipelines_two_targets_one_fails_the_other_syncs_and_the_failed_target_is_unchanged(world):
    assert sync_pipeline(world.brain, "viapat", trigger="cli").result == "ok"
    before = world.fake.files()
    head = world.fake.head()
    world.fake.fail("GET", "/git/ref/heads", status=502, times=99)
    results = {r.pipeline: r for r in sync_all(world.brain, trigger="cli")}
    assert results["viapat"].result == "error" and results["viasignin"].result == "error"
    assert results["folder"].result == "ok" and (world.brain.root / "raw/folder/foldersrc/f.md").is_file()
    assert world.fake.files() == before and world.fake.head() == head


# 3 -----------------------------------------------------------------------------------------------------------------------
def test_03_two_pipelines_one_target_the_second_can_never_claim_the_first_ones_file(world, make_source):
    cfg = load_config(world.brain)
    for p in cfg.pipelines:
        p.target, p.name_policy = "raw/shared/{name}.md", "original"
    save_config(world.brain, cfg)
    from mdpipeline.service import ValidationFailed, create_pipeline

    assert sync_pipeline(world.brain, "viapat", trigger="cli").result == "ok"
    owned = world.fake.read("raw/shared/a.md")
    clash = Pipeline.new("clash", [str(make_source("clashsrc", {"a.md": "OTHER"}))], target="raw/shared/{name}.md", name_policy="original",
                         destination=load_config(world.brain).get("viapat").destination)
    with pytest.raises(ValidationFailed) as exc:                                    # refused at save, naming both pipelines
        create_pipeline(world.brain, clash)
    assert "viapat/" in str(exc.value) and "clash/" in str(exc.value)
    cfg = load_config(world.brain)                                                  # a hostile config written around the validation: refused at plan
    cfg.pipelines.append(clash)
    save_config(world.brain, cfg)
    res = sync_pipeline(world.brain, "clash", trigger="cli")
    assert res.result == "conflicts" and world.fake.read("raw/shared/a.md") == owned


# 5 -----------------------------------------------------------------------------------------------------------------------
def test_05_a_locked_vault_writes_and_deletes_nothing_and_secret_free_pipelines_still_sync(world):
    sync_all(world.brain, trigger="cli")
    before = world.fake.files()
    world.keychain.lose()
    for p in load_config(world.brain).pipelines:                                    # every source file is gone: a locked vault must not turn that into deletions
        if p.name != "folder":
            for f in __import__("pathlib").Path(p.sources[0].path).glob("*.md"):
                f.unlink()
    results = {r.pipeline: r for r in sync_all(world.brain, trigger="cli")}
    assert results["viapat"].category == results["viasignin"].category == "vault-locked"
    assert results["folder"].result == "ok"
    assert world.fake.files() == before
    assert {c["pipeline"] for c in activity_view(world.brain)["needs_you"]} == {"viapat", "viasignin"}


# 6 -----------------------------------------------------------------------------------------------------------------------
def test_06_a_token_that_expires_mid_sync_a_failed_refresh_and_a_revoked_sign_in_leave_the_branch_unchanged_and_show_needs_you(world):
    head = world.fake.head("signin")
    world.fake.before("PATCH", world.fake.expire_access_tokens, times=1)
    mid = sync_pipeline(world.brain, "viasignin", trigger="cli")
    assert mid.result == "error" and world.fake.head("signin") == head                       # expired mid-sync
    stored = gs.SignInTokens.from_json(vault_mod.default_vault().get(world.si_id))
    stored.expires_at = 1.0
    vault_mod.default_vault().replace(world.si_id, stored.to_json())
    world.fake.refresh_requires_secret = True                                        # the refresh is refused
    refused = sync_pipeline(world.brain, "viasignin", trigger="ui")
    assert refused.category == "sign-in-expired" and world.fake.head("signin") == head
    world.fake.refresh_requires_secret = False
    world.fake.revoke_tokens()                                                       # the person revoked the app
    revoked = sync_pipeline(world.brain, "viasignin", trigger="ui")
    assert revoked.category == "sign-in-expired" and world.fake.head("signin") == head and "sign in again" in revoked.message.lower()
    cards = [c for c in activity_view(world.brain)["needs_you"] if c["pipeline"] == "viasignin"]
    assert cards and cards[0]["category"] == "sign-in-expired" and "again" in cards[0]["title"].lower()


# 7 -----------------------------------------------------------------------------------------------------------------------
def everything_a_person_or_an_agent_can_see(world, capsys, monkeypatch):
    """Run the CLI, the UI API and the MCP tools and collect every output as text."""
    seen = []
    for argv in (["pipeline", "list"], ["pipeline", "list", "--json"], ["status"], ["status", "--json"], ["log"], ["log", "--json"], ["vault", "list"],
                 ["vault", "list", "--json"], ["vault", "status"], ["sync", "--all"], ["sync", "--all", "--json"], ["sync", "--all", "--dry-run"],
                 ["pipeline", "test-target", "viapat"], ["pipeline", "test-target", "viasignin"], ["github-app", "show"], ["connect"],
                 ["state", "restore", "--pipeline", "viapat"], ["state", "rebuild", "--pipeline", "viapat"]):
        _, out, err = cli_run(capsys, *argv, brain=world.brain)
        seen.append(out + err)
    with running_ui(world.brain) as ui:
        for path in ("/api/state", "/api/activity", "/api/vault", "/api/pipelines/viapat", "/api/pipelines/viasignin"):
            seen.append(json.dumps(ui.get(path)[1]))
        draft = {"name": "viapat", "sources": [{"path": str(world.tmp / "sources" / "viapatsrc"), "label": "viapatsrc"}],
                 "destination": load_config(world.brain).get("viapat").destination}
        seen.append(json.dumps(ui.post("/api/preview", {"draft": draft, "editing": "viapat"})[1]))
        seen.append(json.dumps(ui.post("/api/target/test", {"destination": draft["destination"]})[1]))
        seen.append(json.dumps(ui.post("/api/target/test", {"destination": {"type": "github", "repo": "acme/brain", "branch": "main"}, "token": PAT})[1]))
        _, start = ui.post("/api/signin/start", {})
        world.fake.authorize_device()
        seen.append(json.dumps(start) + json.dumps(ui.post("/api/signin/poll", {"flow": start["flow"]})[1]))
        seen.append(json.dumps(ui.post("/api/pipelines/viapat/sync")[1]))

    async def mcp_calls():
        async with Client(build_server(world.brain)) as client:
            out = []
            for tool, args in (("sync_status", {}), ("sync", {}), ("search", {"query": "hand"}), ("list", {})):
                result = await client.call_tool(tool, args)
                out.append(json.dumps([getattr(c, "text", "") for c in result.content]))
            return out

    seen += asyncio.run(mcp_calls())
    return "\n".join(seen)


def test_07_secret_search_finds_no_token_and_no_sign_in_token_anywhere(world, capsys, monkeypatch):
    sync_all(world.brain, trigger="cli")
    world.fake.expire_access_tokens()
    stored = gs.SignInTokens.from_json(vault_mod.default_vault().get(world.si_id))
    stored.expires_at = 1.0
    vault_mod.default_vault().replace(world.si_id, stored.to_json())
    sync_pipeline(world.brain, "viasignin", trigger="cli")                           # refreshes: the rotated tokens are the ones to look for too
    tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(world.si_id))
    secrets = [PAT, tokens.access_token, tokens.refresh_token, world.tokens.access_token, world.tokens.refresh_token]
    world.keychain.lose()                                                            # also the error paths of a locked vault
    sync_all(world.brain, trigger="ui")
    world.keychain.key = None
    outputs = everything_a_person_or_an_agent_can_see(world, capsys, monkeypatch)
    assert "viapat" in outputs and "acme/brain" in outputs and "vault" in outputs.lower() and len(outputs) > 5000      # the search looked at real output
    for secret in secrets:
        assert secret not in outputs, secret[:8]
        assert files_containing(secret, world.brain.root, world.tmp / "sources") == [], secret[:8]      # config, state, log, backups, runtime, source folders
        assert files_containing(secret, world.app_dir) == [], secret[:8]                                  # the vault file holds no plaintext, nor does settings.json
        assert repo_containing(secret, world.fake) == [], secret[:8]                                      # repository content and commit messages
    vault_text = (world.app_dir / "vault.json").read_text()
    for needle in ("gate pat", "gate sign-in", "github-token", "github-sign-in", "tester"):
        assert needle not in vault_text                                                                   # not even names, kinds or the login


# 8 -----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["vault", "add", "--kind", "github-token", "--name", "x", "--value", PAT],
    ["vault", "add", "--kind", "github-token", "--name", "x", "--token", PAT],
    ["vault", "replace", "v_x", "--secret", PAT],
    ["pipeline", "add", "q", "--source", ".", "--github", "acme/brain", "--token", PAT],
    ["pipeline", "edit", "viapat", "--replace-token", "--token", PAT],
    ["pipeline", "add", "q", "--source", ".", "--github", "acme/brain", PAT],
    ["init", "somewhere", "--credential", PAT],
])
def test_08_a_token_given_as_a_command_line_argument_is_refused_and_never_echoed(world, capsys, argv):
    cfg_before = world.brain.config_path.read_text()
    code, out, err = cli_run(capsys, *argv, brain=world.brain)
    assert code == 2 and PAT not in out + err
    assert world.brain.config_path.read_text() == cfg_before and PAT not in "\n".join(str(p) for p in world.brain.config_dir.rglob("*"))


def test_08_the_keychain_tool_gets_only_the_vault_key_and_never_a_token_in_its_arguments(monkeypatch):
    calls = []

    class Done:
        returncode, stdout = 0, ""

    monkeypatch.setattr(vault_mod.sys, "platform", "linux")
    monkeypatch.setattr(vault_mod, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(vault_mod.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Done())
    key = bytes(range(32))
    vault_mod.SystemKeychain().set(key)
    store = calls[0]
    assert key.hex() not in " ".join(store[0]) and store[1]["input"] == key.hex()       # on Linux the key goes to stdin, not the process list


# 4 -----------------------------------------------------------------------------------------------------------------------
def test_04_change_target_leaves_the_old_target_byte_identical_in_both_directions(world, tmp_path):
    from mdpipeline.retarget import apply_change

    assert sync_pipeline(world.brain, "folder", trigger="cli").result == "ok"
    world.fake.commit_as_other({p.relative_to(world.brain.root).as_posix(): p.read_bytes() for p in (world.brain.root / "raw").rglob("*.md")}, "owner pushes the brain")
    local_before, state_before = tree_snapshot(world.brain.root / "raw"), (world.brain.root / ".mdpipeline/state.json").read_bytes()
    apply_change(world.brain, "folder", load_config(world.brain).get("viapat").destination)            # folder -> GitHub
    assert tree_snapshot(world.brain.root / "raw") == local_before and (world.brain.root / ".mdpipeline/state.json").read_bytes() == state_before
    c = sync_pipeline(world.brain, "folder", trigger="cli", dry_run=True).counts()
    assert (c["created"], c["updated"], c["renamed"], c["deleted"], c["conflicts"]) == (0, 0, 0, 0, 0)
    assert sync_pipeline(world.brain, "folder", trigger="cli").result == "ok"                              # the first real sync there
    repo_before = world.fake.files()
    head = world.fake.head()
    other = tmp_path / "elsewhere"
    other.mkdir()
    for path, data in repo_before.items():
        if path.startswith("raw/folder/"):
            write(other / path, data.decode())
    apply_change(world.brain, "folder", {"type": "directory", "path": str(other)})                         # GitHub -> another folder
    assert world.fake.files() == repo_before and world.fake.head() == head                                  # the old repository: byte-identical, no commit
    assert sync_pipeline(world.brain, "folder", trigger="cli", dry_run=True).counts()["created"] == 0
