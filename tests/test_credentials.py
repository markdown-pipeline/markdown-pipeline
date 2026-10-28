"""MD-61/MD-65: a credential is a reference (environment variable or OS keychain), never a stored secret."""
import pytest

from mdpipeline import credentials
from mdpipeline.credentials import CredentialError, parse_ref, resolve_token


def test_env_reference_resolves_from_the_environment(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "  s3cret \n")
    assert resolve_token("env:MY_TOKEN") == "s3cret"


def test_missing_env_variable_names_the_variable_but_not_a_value(monkeypatch):
    monkeypatch.delenv("MY_TOKEN", raising=False)
    with pytest.raises(CredentialError) as exc:
        resolve_token("env:MY_TOKEN")
    assert "MY_TOKEN" in str(exc.value)


@pytest.mark.parametrize("ref", ["", "plain-token", "env:", "keychain:", "file:/etc/x", "env:BAD NAME"])
def test_malformed_references_are_rejected(ref):
    with pytest.raises(CredentialError):
        parse_ref(ref)


def test_a_literal_token_is_never_accepted_as_a_reference():
    with pytest.raises(CredentialError) as exc:
        parse_ref("github_pat_AAAAAAAAAAAAAAAAAAAA")
    assert "github_pat_AAAA" not in str(exc.value)


@pytest.mark.windows_unsupported("there is no OS keychain tool on Windows; the Windows error is tested below")
def test_keychain_reference_uses_the_platform_tool(monkeypatch):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 0, "from-keychain\n"

    monkeypatch.setattr(credentials, "run_keychain", runner)
    monkeypatch.setattr(credentials, "keychain_command", lambda service: ["tool", service])
    assert resolve_token("keychain:mdpipeline-github") == "from-keychain"
    assert calls == [["tool", "mdpipeline-github"]]


def test_keychain_failure_is_a_credential_error_without_output(monkeypatch):
    monkeypatch.setattr(credentials, "run_keychain", lambda cmd: (1, "secret-looking output"))
    monkeypatch.setattr(credentials, "keychain_command", lambda service: ["tool", service])
    with pytest.raises(CredentialError) as exc:
        resolve_token("keychain:svc")
    assert "secret-looking" not in str(exc.value)


# --- MD-180 AC4/AC5: Windows has no OS keychain tool; env: is unchanged ------------------------------------------------
WINDOWS_KEYCHAIN_TEXT = ("keychain: references don't work on Windows, so this Jira source can't read its token. Nothing was changed or deleted. "
                         "Let the app keep the token instead: mdpipeline pipeline edit <pipeline> --jira-label <label> --replace-jira-token "
                         "(or Edit in the app). Or use an environment variable: --jira-token env:VARIABLE_NAME.")


def test_keychain_reference_on_windows_says_so_in_marketings_words_and_runs_no_tool(monkeypatch):
    from mdpipeline import winacl

    def never(cmd):
        raise AssertionError("no keychain tool may run on Windows")

    monkeypatch.setattr(winacl, "is_windows", lambda: True)
    monkeypatch.setattr(credentials, "run_keychain", never)
    with pytest.raises(CredentialError) as exc:
        resolve_token("keychain:svc")
    assert str(exc.value) == WINDOWS_KEYCHAIN_TEXT


def test_the_windows_keychain_error_is_an_auth_error_like_every_credential_error():
    assert issubclass(CredentialError, credentials.TargetAuthError)


def test_env_reference_on_windows_works_unchanged(monkeypatch):
    from mdpipeline import winacl

    monkeypatch.setattr(winacl, "is_windows", lambda: True)
    monkeypatch.setenv("MY_TOKEN", " abc ")
    assert resolve_token("env:MY_TOKEN") == "abc"
