"""The UI's side of a pipeline's target (MD-81, MD-82): Test connection, token replace/remove, the vault view.

A token arrives here once (a password field) and goes only to GitHub (to check it) and into the vault; the browser only ever
gets back an opaque handle, "set / not set" and the vault name. Nothing here returns a token."""
from __future__ import annotations

import os

import re
import secrets as _secrets
import time

from . import github_signin as gs
from . import vault as vault_mod
from .brain import Brain
from .config import load_config, save_config
from .destinations import clean_destination, destination_type, directory_root
from .locking import brain_lock
from .service import NotFound, ValidationFailed
from .target import TargetError
from .targetconfig import GitHubSpec, validate_github_spec
from .targetsetup import check_token, drop_token, held_token, hold_token, replace_token
from .validation import Issue, ValidationResult


def unpack(auth: str, held: str | None):
    """What a held value is: a token (personal access token) or a set of sign-in tokens. Returns (access token, SignInTokens | None)."""
    if not held:
        return None, None
    if auth == "sign-in":
        tokens = gs.SignInTokens.from_json(held)
        return tokens.access_token, tokens
    return held, None


def _failed(code: str, message: str, field: str = "") -> ValidationFailed:
    return ValidationFailed(ValidationResult(errors=[Issue(code, message, field)]))


def test_target(brain: Brain, body: dict) -> dict:
    """Read-only check of the target typed in the editor. Always 200: ``ok`` says whether it passed.

    GitHub with a typed token: the token is checked and held in memory (a handle comes back) so the preview and the save
    can use it; with an existing credential reference the saved secret is used."""
    dest = clean_destination(body.get("destination")) or {"type": "directory", "path": ""}
    if destination_type(dest) == "directory":
        root = directory_root(brain, dest)
        if not root.is_dir():
            return {"ok": False, "errors": [{"code": "target-not-a-folder", "message": f"The target folder {root} does not exist or is not a folder.", "field": "destination"}]}
        writable = os.access(root, os.W_OK)
        return {"ok": writable, "directory": {"path": str(root), "writable": writable},
                "errors": [] if writable else [{"code": "target-not-writable", "message": f"The folder {root} is not writable.", "field": "destination"}]}
    spec = GitHubSpec.from_dict(dest)
    token, _ = unpack(spec.auth, body.get("token") or held_token(body.get("pending") or dest.get("pending")))
    try:
        if token:
            conn = check_token(spec, token)
        else:
            result, conn = validate_github_spec(spec)
            if not result.ok or conn is None:
                raise ValidationFailed(result)
    except ValidationFailed as exc:
        return {"ok": False, "errors": [vars(i) for i in exc.result.errors]}
    except TargetError as exc:                       # e.g. a locked vault
        return {"ok": False, "errors": [{"code": "github-" + exc.category, "message": str(exc), "field": "destination"}],
                **({"needs_passphrase": True} if getattr(exc, "needs_passphrase", False) else {})}
    out = {"ok": True, "connection": conn.to_dict()}
    if token and spec.auth != "sign-in":
        out["pending"] = hold_token(token)
    return out


BRANCH_LIMIT = 100


def _branch_error(code: str, message: str) -> dict:
    return {"ok": False, "errors": [{"code": code, "message": message, "field": "branch"}]}


def list_branches(brain: Brain, body: dict) -> dict:
    """MD-143: the branches of the typed repository, for the branch picker. Read-only; always 200 (``ok`` says whether it worked).

    The credential is the same one Test connection uses: a typed token, a held handle (``pending``) or the saved credential.
    Nothing returned contains a secret, and the names go only to the person's own screen."""
    from urllib.parse import urlsplit

    from .credentials import CredentialError
    from .github_api import GitHubApi
    from .targetconfig import REPO_RE, api_for

    dest = clean_destination(body.get("destination")) or {}
    if destination_type(dest) != "github":
        return _branch_error("not-github", "Branches are listed for a GitHub repository target.")
    spec = GitHubSpec.from_dict(dest)
    if not REPO_RE.match(spec.repo or ""):
        return _branch_error("bad-repo", f"'{spec.repo}' is not a repository name; use OWNER/REPO.")
    token, _ = unpack(spec.auth, body.get("token") or held_token(body.get("pending") or dest.get("pending")))
    if not token and not spec.credential:
        return _branch_error("no-token", "Enter a token (or sign in) first; the list needs to read the repository.")
    try:
        api = GitHubApi(token, api_url=spec.api_url, allowed_hosts=(urlsplit(spec.api_url).hostname or "",)) if token else api_for(spec)
        default = api.repo(spec.repo).get("default_branch", "")
        listed = api.branches(spec.repo, BRANCH_LIMIT)
    except (TargetError, CredentialError) as exc:
        return {"ok": False, "errors": [{"code": "github-" + getattr(exc, "category", "credential"), "message": str(exc), "field": "branch"}],
                **({"needs_passphrase": True} if getattr(exc, "needs_passphrase", False) else {})}
    items = [{"name": b["name"], "protected": bool(b.get("protected")), "default": b["name"] == default} for b in listed]
    items.sort(key=lambda b: (not b["default"], b["name"]))
    return {"ok": True, "default": default, "branches": items, "more": len(listed) >= BRANCH_LIMIT}


def _pipeline(brain: Brain, name: str):
    pipeline = load_config(brain).get(name)
    if pipeline is None:
        raise NotFound(name)
    if destination_type(pipeline.destination) != "github":
        raise _failed("not-github", "This pipeline's target is a folder: there is no token to change.")
    return pipeline


def replace_pipeline_token(brain: Brain, name: str, body: dict) -> dict:
    pipeline = _pipeline(brain, name)
    token = body.get("token") or held_token(body.get("pending"))
    if not token:
        raise _failed("no-token", "Enter the new token.", "token")
    with brain_lock(brain):
        replace_token(brain, pipeline, token)          # checked first: a failing token leaves the old one in place
        config = load_config(brain)
        config.get(name).destination = pipeline.destination
        save_config(brain, config)
        brain._cache.clear()
    drop_token(body.get("pending"))
    return {"saved": True}


def remove_pipeline_token(brain: Brain, name: str) -> dict:
    """Detach the token from the pipeline; the secret is deleted from the vault when no other pipeline uses it."""
    pipeline = _pipeline(brain, name)
    ref = pipeline.destination.get("credential", "")
    with brain_lock(brain):
        config = load_config(brain)
        config.get(name).destination["credential"] = ""
        save_config(brain, config)
        brain._cache.clear()
        if ref.startswith("vault:") and not vault_mod.secret_users(brain, ref[len("vault:"):]):
            vault_mod.default_vault().remove(ref[len("vault:"):])
    return {"saved": True}


def vault_view(brain: Brain) -> dict:
    """The secrets: id, name, kind, dates and the pipelines that use them. Never a value."""
    vault = vault_mod.default_vault()
    try:
        info = vault.describe()
    except vault_mod.VaultUnsafe as exc:
        return {"path": str(vault.path), "exists": True, "protection": "file", "locked": False, "unsafe": True, "message": str(exc), "secrets": []}
    try:
        secrets = [{**vars(i), "used_by": vault_mod.secret_users(brain, i.id)} for i in vault.list()]
        return {**info, "locked": False, "secrets": secrets}
    except vault_mod.VaultLocked as exc:
        return {**info, "locked": True, "needs_passphrase": exc.needs_passphrase, "message": str(exc), "secrets": []}


def unlock_vault(body: dict) -> dict:
    """Only for a vault made by v0.1.1-v0.1.3 (protected by a passphrase): opens it once and moves its secrets, so the passphrase is never needed again."""
    passphrase = str(body.get("passphrase") or "")
    if not passphrase:
        raise _failed("no-passphrase", "Enter the passphrase.", "passphrase")
    vault_mod.unlock(passphrase)                      # raises VaultLocked when the passphrase is wrong; moves an old passphrase vault to the user-only file (MD-163)
    return {"unlocked": True}


def remove_vault_secret(brain: Brain, body: dict) -> dict:
    vault_mod.remove_secret(brain, str(body.get("id", "")))
    return {"removed": True}


# --- Sign in with GitHub (MD-83) ---------------------------------------------------------------------------------------------
FLOW_SECONDS = 900
_flows: dict[str, dict] = {}            # opaque handle -> the device code (memory only; the browser never sees it)


def github_app_view() -> dict:
    settings = gs.app_settings()
    return {"configured": bool(settings["client_id"]), "client_id": settings["client_id"], "slug": settings["slug"], "install_url": gs.install_url(),
            "source": gs.app_source(), "has_default": bool(gs.shipped_github_app()["client_id"])}


def reset_github_app() -> dict:
    """Go back to the GitHub App the app ships with."""
    gs.reset_app_settings()
    return github_app_view()


CLIENT_ID_RE = re.compile(r"^Iv(?:1\.[0-9a-fA-F]{16}|23[A-Za-z0-9]{12,40})$|^Iv1\.[A-Za-z0-9]{8,40}$")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38})$")


def save_github_app(body: dict) -> dict:
    """Record the registered GitHub App's client ID and slug (public values, not secrets: not in the vault). Raises ValidationFailed (nothing saved)."""
    client_id, slug = str(body.get("client_id") or "").strip(), str(body.get("slug") or "").strip().lower()
    errors = []
    if not CLIENT_ID_RE.match(client_id):
        errors.append(Issue("bad-client-id", "The client ID looks like Iv1.0123456789abcdef or Iv23liXXXXXXXXXXXX: copy it from the app's page on github.com "
                            "(Settings, Developer settings, GitHub Apps, your app, \"Client ID\"). It is not the App ID and not a secret.", "client_id"))
    if not SLUG_RE.match(slug):
        errors.append(Issue("bad-slug", "The app slug is the last part of https://github.com/apps/<slug>: lowercase letters, digits and hyphens only.", "slug"))
    if errors:
        raise ValidationFailed(ValidationResult(errors=errors))
    gs.save_app_settings(client_id, slug)
    return github_app_view()


def signin_start(body: dict) -> dict:
    client_id = gs.require_client_id()
    code = gs.request_device_code(client_id)
    now = time.monotonic()
    for handle in [h for h, f in _flows.items() if f["expires"] < now]:
        del _flows[handle]
    handle = "f_" + _secrets.token_hex(8)
    _flows[handle] = {"device_code": code.device_code, "expires": now + code.expires_in, "interval": code.interval, "client_id": client_id}
    return {"flow": handle, "user_code": code.user_code, "verification_uri": code.verification_uri, "interval": code.interval, "expires_in": code.expires_in}


def signin_poll(body: dict) -> dict:
    """ONE poll of the device flow (the browser calls this at the interval GitHub gave). Never returns a token."""
    flow = _flows.get(body.get("flow") or "")
    if flow is None or flow["expires"] < time.monotonic():
        return {"status": "error", "message": "The sign-in code expired. Start the sign-in again."}
    try:
        result = gs.poll_once(flow["client_id"], flow["device_code"])
    except gs.SignInDenied as exc:
        _flows.pop(body.get("flow"), None)
        return {"status": "denied", "message": str(exc)}
    except gs.SignInError as exc:
        _flows.pop(body.get("flow"), None)
        return {"status": "error", "message": str(exc)}
    if isinstance(result, int):
        if result:
            flow["interval"] = result
        return {"status": "pending", "interval": flow["interval"]}
    from .github_api import GitHubApi

    _flows.pop(body.get("flow"), None)
    api = GitHubApi(result.access_token)
    result.login = api.user()
    return {"status": "ok", "login": result.login, "repos": gs.installed_repos(api), "pending": hold_token(result.to_json())}


def signin_repos(body: dict) -> dict:
    """The repositories the GitHub App is installed on for the signed-in person (to check again after installing it)."""
    from .github_api import GitHubApi

    token, tokens = unpack("sign-in", held_token(body.get("pending")))
    if not token:
        raise _failed("no-sign-in", "Sign in with GitHub first.", "destination")
    return {"repos": gs.installed_repos(GitHubApi(token)), "install_url": gs.install_url()}


def _signin_pipeline(brain: Brain, name: str):
    pipeline = _pipeline(brain, name)
    if pipeline.destination.get("auth") != "sign-in":
        raise _failed("not-sign-in", "This pipeline uses a token, not Sign in with GitHub.")
    return pipeline


def sign_in_again(brain: Brain, name: str, body: dict) -> dict:
    """Replace the stored sign-in of a saved pipeline with a fresh one (checked first)."""
    pipeline = _signin_pipeline(brain, name)
    token, tokens = unpack("sign-in", held_token(body.get("pending")))
    if tokens is None:
        raise _failed("no-sign-in", "Sign in with GitHub first.", "destination")
    check_token(GitHubSpec.from_dict(pipeline.destination), token)
    ref = pipeline.destination.get("credential", "")
    with brain_lock(brain):
        if ref.startswith("vault:") and vault_mod.default_vault().has(ref[len("vault:"):]):
            vault_mod.default_vault().replace(ref[len("vault:"):], tokens.to_json())
        else:
            secret_id = vault_mod.default_vault().add("github-sign-in", f"GitHub sign-in of {tokens.login} for {name}", tokens.to_json())
            config = load_config(brain)
            config.get(name).destination["credential"] = f"vault:{secret_id}"
            save_config(brain, config)
            brain._cache.clear()
    drop_token(body.get("pending"))
    return {"saved": True, "login": tokens.login}


SIGN_OUT_MESSAGE = ("Signed out: the sign-in was removed from the vault; syncs of this pipeline stop until you sign in again. The app cannot revoke its access "
                    "at GitHub without a client secret: to revoke it, open https://github.com/settings/applications and remove the app.")


def sign_out(brain: Brain, name: str) -> dict:
    _signin_pipeline(brain, name)
    remove_pipeline_token(brain, name)
    return {"saved": True, "message": SIGN_OUT_MESSAGE}


def signin_login(pipeline) -> dict | None:
    """Who a saved sign-in belongs to (the login is not a secret). None when there is none or the vault is locked."""
    dest = pipeline.destination or {}
    ref = dest.get("credential", "")
    if dest.get("auth") != "sign-in" or not ref.startswith("vault:"):
        return None
    try:
        return {"login": gs.SignInTokens.from_json(vault_mod.default_vault().get(ref[len("vault:"):])).login}
    except (vault_mod.VaultError, ValueError, TypeError):
        return None


def import_secrets(brain: Brain) -> dict:
    """Move the old env:/keychain: references into the vault (the "Needs you" card). Never returns a token."""
    from .secretimport import import_references

    return {"results": [r.to_dict() for r in import_references(brain, trigger="ui")]}
