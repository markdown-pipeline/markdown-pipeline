"""MD-192 (H-VAULT-1, confirmed 2026-10-29): one vault is shared by every brain, so "which pipelines use this secret" and every delete guard must look at
all of the person's brains, not only the one named. A secret another brain uses must show as used and must not be deletable from here."""
from __future__ import annotations

import json

import pytest

from mdpipeline import vault as vault_mod
from mdpipeline.brain import init_brain
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.schedulestore import register_brain
from mdpipeline.vault import VaultError, secret_users_all

TOKEN = "ghp_ALLBRAINSSECRET0123456789"
DEST = {"type": "github", "repo": "a/b", "branch": "main", "auth": "token"}


@pytest.fixture
def other(tmp_path):
    """Brain B: registered (it has synced, so the background process knows it), folder name 'brainB'."""
    brain_b = init_brain(tmp_path / "brainB", create=True)
    register_brain(brain_b)
    return brain_b


@pytest.fixture
def secret_id():
    return vault_mod.default_vault().add("github-token", "B only", TOKEN)


def uses(brain, make_source, secret_id, name="b-gh", folder="s"):
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new(name, [str(make_source(f"{brain.root.name}-{name}-{folder}"))], destination={**DEST, "credential": f"vault:{secret_id}"}))
    save_config(brain, cfg)


def uses_in_jira(brain, secret_id, name):
    """A pipeline with a Jira source and a plain folder target (removing it needs no network) that uses the secret as its token."""
    from mdpipeline.config import Source

    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new(name, [Source("https://x.atlassian.net", "jira", type="jira", options={"email_ref": "", "token_ref": f"vault:{secret_id}"})]))
    save_config(brain, cfg)


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- AC1: from brain A, brain B's pipeline shows and the removal is refused --------------------------------------------------
def test_from_another_brain_the_users_name_the_pipeline_and_its_brain(brain, other, secret_id, make_source):
    uses(other, make_source, secret_id)
    assert secret_users_all(brain, secret_id) == ['b-gh (in brain "brainB")']


def test_in_its_own_brain_a_pipeline_is_named_plainly(brain, other, secret_id, make_source):
    uses(other, make_source, secret_id)
    assert secret_users_all(other, secret_id) == ["b-gh"]


def test_the_current_brain_is_listed_first_and_never_twice(brain, other, secret_id, make_source):
    register_brain(brain)
    uses(brain, make_source, secret_id, name="a-gh")
    uses(other, make_source, secret_id)
    assert secret_users_all(brain, secret_id) == ["a-gh", 'b-gh (in brain "brainB")']


def test_removing_a_secret_that_another_brain_uses_is_refused_and_names_the_pipeline_and_brain(brain, other, secret_id, make_source):
    uses(other, make_source, secret_id)
    with pytest.raises(VaultError) as exc:
        vault_mod.remove_secret(brain, secret_id)
    assert 'b-gh (in brain "brainB")' in str(exc.value) and "Nothing was removed" in str(exc.value)
    assert vault_mod.default_vault().has(secret_id)


def test_cli_vault_list_from_brain_a_says_brain_b_uses_it(brain, other, secret_id, make_source, capsys):
    uses(other, make_source, secret_id)
    code, out, err = run(capsys, "--brain", str(brain.root), "vault", "list")
    assert code == 0 and "used by: b-gh (in brain \"brainB\")" in out


def test_cli_vault_list_json_carries_the_same_labels(brain, other, secret_id, make_source, capsys):
    uses(other, make_source, secret_id)
    code, out, err = run(capsys, "--brain", str(brain.root), "vault", "list", "--json")
    assert [row["used_by"] for row in json.loads(out)] == [['b-gh (in brain "brainB")']]


def test_cli_vault_remove_from_brain_a_is_refused_and_the_secret_stays(brain, other, secret_id, make_source, capsys):
    uses(other, make_source, secret_id)
    code, out, err = run(capsys, "--brain", str(brain.root), "vault", "remove", secret_id)
    assert code == 2 and 'b-gh (in brain "brainB")' in err and vault_mod.default_vault().has(secret_id)


def test_cli_vault_remove_with_no_brain_at_all_still_looks_at_the_registered_brains(other, secret_id, make_source, capsys, tmp_path, monkeypatch):
    uses(other, make_source, secret_id)
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    code, out, err = run(capsys, "vault", "remove", secret_id)
    assert code == 2 and 'b-gh (in brain "brainB")' in err and vault_mod.default_vault().has(secret_id)


def test_an_unused_secret_can_still_be_removed(brain, other, secret_id):
    vault_mod.remove_secret(brain, secret_id)
    assert not vault_mod.default_vault().has(secret_id)


# --- edge cases ---------------------------------------------------------------------------------------------------------------
def test_a_registered_brain_whose_folder_is_gone_is_skipped(brain, other, secret_id, make_source):
    uses(other, make_source, secret_id)
    other.config_path.unlink()
    assert secret_users_all(brain, secret_id) == []
    vault_mod.remove_secret(brain, secret_id)
    assert not vault_mod.default_vault().has(secret_id)


def test_a_brain_whose_configuration_cannot_be_read_refuses_the_removal_but_not_the_list(brain, other, secret_id):
    other.config_path.write_text("pipelines: [unclosed\n", encoding="utf-8")
    assert secret_users_all(brain, secret_id) == []
    with pytest.raises(VaultError) as exc:
        vault_mod.remove_secret(brain, secret_id)
    assert str(exc.value) == (f"The secret '{secret_id}' may be used by the brain 'brainB', but that brain's configuration can't be read, so we couldn't check. "
                              "Nothing was removed. Fix that brain's configuration, then try again.")      # MD-192, marketing MM-155 string 2
    assert vault_mod.default_vault().has(secret_id)                                                       # "Nothing was removed" is true


def test_the_ui_remove_names_a_brain_whose_configuration_cannot_be_read_and_keeps_the_secret(brain, other, secret_id):
    from mdpipeline import uiremove

    uses_in_jira(brain, secret_id, "a-jira")
    other.config_path.write_text("pipelines: [unclosed\n", encoding="utf-8")
    answer = uiremove.remove(brain, "a-jira", {})
    assert vault_mod.default_vault().has(secret_id)
    assert answer["credentials_kept"] == [{"name": "B only", "used_by": ['brain "brainB" (configuration can\'t be read)']}] and answer["credentials_removed"] == []


# --- every other path that deletes a secret has the same guard -----------------------------------------------------------------
def test_removing_a_target_token_in_the_ui_keeps_a_secret_another_brain_uses(brain, other, secret_id, make_source):
    from mdpipeline import uitargets

    uses(brain, make_source, secret_id, name="a-gh")
    uses(other, make_source, secret_id)
    uitargets.remove_pipeline_token(brain, "a-gh")
    assert vault_mod.default_vault().has(secret_id)


def test_removing_a_pipeline_in_the_ui_keeps_a_secret_another_brain_uses_and_says_so(brain, other, secret_id, make_source):
    from mdpipeline import uiremove

    uses_in_jira(brain, secret_id, "a-jira")
    uses(other, make_source, secret_id)
    answer = uiremove.remove(brain, "a-jira", {})
    assert vault_mod.default_vault().has(secret_id)
    assert answer["credentials_kept"] == [{"name": "B only", "used_by": ['b-gh (in brain "brainB")']}] and answer["credentials_removed"] == []


def test_the_remove_preview_also_lists_the_other_brains_pipeline(brain, other, secret_id, make_source):
    from mdpipeline import uiremove

    uses_in_jira(brain, secret_id, "a-jira")
    uses(other, make_source, secret_id)
    assert uiremove.preview(brain, "a-jira")["credentials_kept"] == [{"name": "B only", "used_by": ['b-gh (in brain "brainB")']}]


def test_cli_remove_token_keeps_a_secret_another_brain_uses(brain, other, secret_id, make_source, capsys):
    uses(brain, make_source, secret_id, name="a-gh")
    uses(other, make_source, secret_id)
    code, out, err = run(capsys, "--brain", str(brain.root), "pipeline", "edit", "a-gh", "--remove-token")
    assert code == 0, err
    assert vault_mod.default_vault().has(secret_id)


def test_cli_sign_out_keeps_a_secret_another_brain_uses(brain, other, secret_id, make_source, capsys):
    for b, name in ((brain, "a-gh"), (other, "b-gh")):
        cfg = load_config(b)
        cfg.pipelines.append(Pipeline.new(name, [str(make_source(f"so-{name}"))], destination={**DEST, "auth": "sign-in", "credential": f"vault:{secret_id}"}))
        save_config(b, cfg)
    code, out, err = run(capsys, "--brain", str(brain.root), "pipeline", "sign-out", "a-gh")
    assert code == 0, err
    assert vault_mod.default_vault().has(secret_id)


def test_the_ui_vault_view_labels_the_other_brain(brain, other, secret_id, make_source):
    from mdpipeline import uitargets

    uses(other, make_source, secret_id)
    [row] = uitargets.vault_view(brain)["secrets"]
    assert row["used_by"] == ['b-gh (in brain "brainB")']
