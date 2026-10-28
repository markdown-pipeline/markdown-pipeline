"""MD-80 / MD-81: choose a GitHub repository as a pipeline's target in the CLI; the token goes into the vault from a hidden prompt or stdin."""
import io
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline import cli, github_api, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import load_config
from secret_search import files_containing

TOKEN = "ghp_SECRETSECRETSECRET"


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


def run(capsys, monkeypatch, *argv, stdin=None, brain=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    try:
        code = main((["--brain", str(brain.root)] if brain else []) + list(argv))
    except SystemExit as exc:                  # argparse's own usage error
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


def add(capsys, monkeypatch, brain, make_source, *extra, token=TOKEN, repo="acme/brain", name="p", files=None):
    src = make_source(name + "src", files or {"a.md": "A"})
    return run(capsys, monkeypatch, "pipeline", "add", name, "--source", str(src), "--github", repo, "--stdin", *extra,
               stdin=(token + "\n") if token is not None else "", brain=brain)


def test_add_with_a_github_target_validates_stores_the_token_in_the_vault_and_saves_only_a_reference(fake, brain, make_source, capsys, monkeypatch, app_data):
    code, out, err = add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    assert code == 0, err
    dest = load_config(brain).get("p").destination
    [secret] = vault_mod.default_vault().list()
    assert dest["type"] == "github" and dest["repo"] == "acme/brain" and dest["branch"] == "main" and dest["credential"] == f"vault:{secret.id}"
    assert dest["auth"] == "token" and secret.kind == "github-token"
    assert TOKEN not in out + err and files_containing(TOKEN, brain.root, app_data.dir) == []


def test_the_branch_defaults_to_the_repositorys_default_branch(fake, brain, make_source, capsys, monkeypatch):
    r = add(capsys, monkeypatch, brain, make_source); assert r[0] == 0, r
    assert load_config(brain).get("p").destination["branch"] == "main"


@pytest.mark.parametrize("kwargs,needle", [({"repo": "nobody/nothing"}, "not found"), ({"extra": ("--branch", "nope")}, "branch")])
def test_validation_failures_save_nothing_and_store_no_token(fake, brain, make_source, capsys, monkeypatch, kwargs, needle):
    code, out, err = add(capsys, monkeypatch, brain, make_source, *kwargs.get("extra", ()), repo=kwargs.get("repo", "acme/brain"))
    assert code == 2 and needle in err.lower()
    assert load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_a_read_only_token_is_refused_with_a_plain_message(fake, brain, make_source, capsys, monkeypatch):
    fake.set_permission("read")
    code, out, err = add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    assert code == 2 and "read and write" in err and load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_a_wrong_token_is_refused_not_stored_and_not_echoed(fake, brain, make_source, capsys, monkeypatch):
    code, out, err = add(capsys, monkeypatch, brain, make_source, "--branch", "main", token="ghp_WRONGWRONGWRONG")
    assert code == 2 and "rejected" in err and "ghp_WRONG" not in out + err
    assert load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_a_protected_branch_is_refused_with_a_dedicated_branch_suggestion(fake, brain, make_source, capsys, monkeypatch):
    fake.protect("main")
    code, _, err = add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    assert code == 2 and "md-sync" in err and load_config(brain).pipelines == []


def test_a_token_given_as_an_argument_is_refused_not_echoed_and_nothing_is_saved(fake, brain, make_source, capsys, monkeypatch):
    src = make_source("s", {"a.md": "A"})
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", "--source", str(src), "--github", "acme/brain", "--token", TOKEN, brain=brain)
    assert code == 2 and TOKEN not in out + err and load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_a_mistyped_command_line_never_echoes_a_token(fake, brain, capsys, monkeypatch):
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", TOKEN, brain=brain)
    assert code == 2 and TOKEN not in out + err


def test_without_a_terminal_and_without_stdin_no_token_is_read(fake, brain, make_source, capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: False)
    src = make_source("s", {"a.md": "A"})
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", "--source", str(src), "--github", "acme/brain", brain=brain)
    assert code == 2 and "--stdin" in err and load_config(brain).pipelines == []


def test_the_hidden_prompt_is_used_when_interactive(fake, brain, make_source, capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": TOKEN)
    src = make_source("s", {"a.md": "A"})
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", "--source", str(src), "--github", "acme/brain", brain=brain)
    assert code == 0, err and TOKEN not in out + err


def test_a_second_pipeline_can_reuse_a_stored_secret(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    [secret] = vault_mod.default_vault().list()
    src = make_source("second", {"b.md": "B"})
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "q", "--source", str(src), "--github", "acme/brain", "--use-secret", secret.id, brain=brain)
    assert code == 0, err
    assert load_config(brain).get("q").destination["credential"] == f"vault:{secret.id}" and len(vault_mod.default_vault().list()) == 1


def test_a_failing_pipeline_save_does_not_leave_a_secret_behind(fake, brain, make_source, capsys, monkeypatch, tmp_path):
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "bad name!", "--source", str(make_source("s", {"a.md": "A"})), "--github", "acme/brain",
                         "--stdin", stdin=TOKEN + "\n", brain=brain)
    assert code == 2 and vault_mod.default_vault().list() == []


def test_test_target_reports_user_repo_head_and_write_permission_and_writes_nothing(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    fake.requests.clear()
    code, out, _ = run(capsys, monkeypatch, "pipeline", "test-target", "p", brain=brain)
    assert code == 0
    for needle in ("tester", "acme/brain", "main", fake.head()[:7], "allowed"):
        assert needle in out
    assert TOKEN not in out and not [r for r in fake.requests if r[0] != "GET"]


def test_test_target_failure_exits_nonzero(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    fake.protect("main")
    code, out, err = run(capsys, monkeypatch, "pipeline", "test-target", "p", brain=brain)
    assert code == 1 and "protected" in (out + err).lower()


def test_test_target_of_a_directory_checks_the_folder(brain, make_source, tmp_path, capsys, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    run(capsys, monkeypatch, "pipeline", "add", "d", "--source", str(make_source("s", {"a.md": "A"})), "--directory", str(vault), brain=brain)
    code, out, _ = run(capsys, monkeypatch, "pipeline", "test-target", "d", brain=brain)
    assert code == 0 and str(vault) in out and "writable" in out
    vault.rmdir()
    code, out, err = run(capsys, monkeypatch, "pipeline", "test-target", "d", brain=brain)
    assert code == 1


def test_test_target_of_an_unknown_pipeline_is_a_usage_error(brain, capsys, monkeypatch):
    assert run(capsys, monkeypatch, "pipeline", "test-target", "nope", brain=brain)[0] == 2


def test_a_dry_run_through_the_cli_writes_nothing_to_the_repository(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main", files={"a.md": "A", "b.md": "B"})
    head = fake.head()
    code, out, _ = run(capsys, monkeypatch, "sync", "p", "--dry-run", brain=brain)
    assert code == 0 and "created 2" in out and fake.head() == head and not [r for r in fake.requests if r[0] in ("POST", "PATCH")]


def test_a_real_sync_commits_to_the_repository(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    code, out, err = run(capsys, monkeypatch, "sync", "p", brain=brain)
    assert code == 0, err
    assert any(p.startswith("raw/") and p.endswith("_a.md") for p in fake.files()) and "commit:" in out        # MD-140: raw/ by default


def test_status_and_list_show_the_github_target_and_never_the_token(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    code, out, _ = run(capsys, monkeypatch, "pipeline", "list", brain=brain)
    assert "acme/brain" in out and "main" in out and TOKEN not in out
    code, out, _ = run(capsys, monkeypatch, "pipeline", "list", "--json", brain=brain)
    row = json.loads(out)[0]
    assert row["destination"]["label"] == "github acme/brain (main)" and "credential" not in row["destination"] and TOKEN not in out
    code, out, _ = run(capsys, monkeypatch, "status", "--json", brain=brain)
    assert json.loads(out)["pipelines"][0]["target"]["repo"] == "acme/brain" and TOKEN not in out


def test_replace_token_validates_first_and_keeps_the_old_one_when_the_new_one_fails(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    [secret] = vault_mod.default_vault().list()
    code, out, err = run(capsys, monkeypatch, "pipeline", "edit", "p", "--replace-token", "--stdin", stdin="ghp_BADBADBADBAD\n", brain=brain)
    assert code == 2 and "ghp_BAD" not in out + err and vault_mod.default_vault().get(secret.id) == TOKEN
    fake.token = "ghp_ROTATEDVALUE0000"
    code, out, err = run(capsys, monkeypatch, "pipeline", "edit", "p", "--replace-token", "--stdin", stdin="ghp_ROTATEDVALUE0000\n", brain=brain)
    assert code == 0, err and vault_mod.default_vault().get(secret.id) == "ghp_ROTATEDVALUE0000"


def test_remove_token_detaches_it_and_deletes_the_secret_when_nobody_else_uses_it(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    code, out, err = run(capsys, monkeypatch, "pipeline", "edit", "p", "--remove-token", brain=brain)
    assert code == 0, err
    assert vault_mod.default_vault().list() == [] and not load_config(brain).get("p").destination.get("credential")
    code, out, err = run(capsys, monkeypatch, "sync", "p", brain=brain)
    assert code == 1 and "token" in (out + err).lower() and fake.read("raw/p/p/a.md") is None


def test_clone_can_be_set_and_removed(fake, brain, make_source, capsys, monkeypatch, tmp_path):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    clone = tmp_path / "clone"
    clone.mkdir()
    assert run(capsys, monkeypatch, "pipeline", "edit", "p", "--clone", str(clone), brain=brain)[0] == 0
    assert load_config(brain).get("p").destination["clone"] == str(clone)
    assert run(capsys, monkeypatch, "pipeline", "edit", "p", "--clone", str(tmp_path / "nope"), brain=brain)[0] == 2
    assert run(capsys, monkeypatch, "pipeline", "edit", "p", "--remove-clone", brain=brain)[0] == 0
    assert "clone" not in load_config(brain).get("p").destination


def test_changing_the_repository_in_edit_points_to_change_target(fake, brain, make_source, capsys, monkeypatch):
    add(capsys, monkeypatch, brain, make_source, "--branch", "main")
    code, out, err = run(capsys, monkeypatch, "pipeline", "edit", "p", "--github", "other/repo", brain=brain)
    assert code == 2 and "change-target" in err


def test_github_and_directory_cannot_be_combined(brain, make_source, tmp_path, capsys, monkeypatch):
    code, out, err = run(capsys, monkeypatch, "pipeline", "add", "p", "--source", str(make_source("s", {"a.md": "A"})), "--github", "a/b",
                         "--directory", str(tmp_path), brain=brain)
    assert code == 2 and "either" in err.lower()
