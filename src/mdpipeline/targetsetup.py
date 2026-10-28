"""Setting up a pipeline's GitHub target with a token (MD-80, MD-81): check it first, then keep it in the vault.

The token comes in as a value (a hidden prompt, stdin, a password field) and only ever goes to GitHub (to check it) and into
the vault. The configuration gets ``vault:<id>``. A token that does not pass the checks is never stored.
"""
from __future__ import annotations

import secrets as _secrets
import time

from .brain import Brain
from .config import Pipeline, load_config
from .github_api import GitHubApi
from .service import ValidationFailed, create_pipeline
from .targetconfig import GitHubSpec, validate_github_spec
from .validation import Issue, ValidationResult
from . import vault as vault_mod


def _fail(code: str, message: str, field: str = "") -> ValidationFailed:
    return ValidationFailed(ValidationResult(errors=[Issue(code, message, field)]))


def check_token(spec: GitHubSpec, token: str):
    """Everything the save checks (repository, branch, write allowed, not protected), with ``token`` and nothing stored.

    Returns the connection (so the caller can fill in the branch). Raises ``ValidationFailed``."""
    from urllib.parse import urlsplit

    host = urlsplit(spec.api_url).hostname or ""
    probe = GitHubSpec(spec.repo, "vault:v_checking", spec.branch, spec.api_url, spec.clone, spec.auth)
    result, conn = validate_github_spec(probe, GitHubApi(token, api_url=spec.api_url, allowed_hosts=(host,)))
    if not result.ok or conn is None:
        raise ValidationFailed(result)
    return conn


def store_token(spec: GitHubSpec, token: str, name: str = "", tokens=None) -> tuple[GitHubSpec, str]:
    """Check ``token`` for ``spec`` and keep it in the vault. Returns the spec with ``vault:<id>`` and its real branch, and the secret id.

    ``tokens`` (a ``SignInTokens``): a sign-in; ``token`` is then its access token and the whole token set is what the vault keeps."""
    conn = check_token(spec, token)
    if tokens is not None:
        secret_id = vault_mod.default_vault().add("github-sign-in", name or f"GitHub sign-in of {tokens.login}", tokens.to_json())
    else:
        secret_id = vault_mod.default_vault().add("github-token", name or f"GitHub token for {spec.repo}", token)
    return GitHubSpec(spec.repo, f"vault:{secret_id}", conn.branch, spec.api_url, spec.clone, spec.auth), secret_id


def replace_token(brain: Brain, pipeline: Pipeline, token: str) -> None:
    """Replace the token the pipeline's target uses. The new token must pass the same checks; otherwise the old one stays."""
    dest = pipeline.destination or {}
    ref = dest.get("credential", "")
    check_token(GitHubSpec.from_dict(dest), token)
    if ref.startswith("vault:"):
        vault_mod.default_vault().replace(ref[len("vault:"):], token)
    else:                                              # an old env:/keychain: reference: the token moves into the vault now
        secret_id = vault_mod.default_vault().add("github-token", f"GitHub token for {dest.get('repo', '')}", token)
        dest["credential"] = f"vault:{secret_id}"


# --- a tested token, held in memory until the pipeline is saved --------------------------------------------------------
HOLD_SECONDS = 900
HOLD_MAX = 16
_held: dict[str, tuple[float, str]] = {}      # opaque handle -> (expires, token): memory only, never written anywhere


def hold_token(token: str) -> str:
    """Keep a token that passed Test connection in this process for a few minutes, so the live preview and the save can use it
    without the browser sending it again. Returns an opaque handle."""
    now = time.monotonic()
    for handle in [h for h, (exp, _) in _held.items() if exp < now]:
        del _held[handle]
    while len(_held) >= HOLD_MAX:
        del _held[min(_held, key=lambda h: _held[h][0])]
    handle = "p_" + _secrets.token_hex(8)
    _held[handle] = (now + HOLD_SECONDS, token)
    return handle


def held_token(handle: str | None) -> str | None:
    entry = _held.get(handle or "")
    return entry[1] if entry and entry[0] >= time.monotonic() else None


def drop_token(handle: str | None) -> None:
    _held.pop(handle or "", None)


def create_pipeline_with_token(brain: Brain, pipeline: Pipeline, token: str | None, vault_access=lambda action: action(), tokens=None):
    """Save a new pipeline. A GitHub token is checked first and only then kept in the vault; a failed save leaves no secret behind.

    ``vault_access`` wraps the vault step (the CLI uses it to ask for a passphrase)."""
    from .validation import validate_pipeline

    created = None
    if pipeline.destination:
        pipeline.destination.pop("pending", None)
    if tokens is not None:
        token = tokens.access_token
    if token is not None:
        pipeline.destination["credential"] = "vault:v_pending"                      # a placeholder for the cheap checks
        from . import jirasetup

        result = validate_pipeline(brain, load_config(brain), jirasetup.with_placeholders(pipeline))      # no token is stored for a pipeline that cannot be saved; typed Jira values are stored by the save below
        if not result.ok:
            raise ValidationFailed(result)
        name = f"GitHub sign-in of {tokens.login} for {pipeline.name}" if tokens is not None else f"GitHub token for {pipeline.name}"
        spec, created = vault_access(lambda: store_token(GitHubSpec.from_dict(pipeline.destination or {}), token, name, tokens))
        clone = pipeline.destination.get("clone")
        pipeline.destination = spec.to_dict() | {"auth": spec.auth}
        if clone:
            pipeline.destination["clone"] = clone
    try:
        return create_pipeline(brain, pipeline)
    except Exception:
        if created:
            try:
                vault_mod.default_vault().remove(created)
            except vault_mod.VaultError:
                pass
        raise
