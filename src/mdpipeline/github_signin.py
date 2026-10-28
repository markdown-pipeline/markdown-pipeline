"""Sign in with GitHub (MD-83, MD-84): the GitHub App device flow and token refresh, with no client secret.

The only module (besides ``github_api``) that talks to GitHub, and only to the two sign-in endpoints on ``github.com``
(``/login/device/code`` and ``/login/oauth/access_token``), only over https. The app ships only its client ID. The user's access
and refresh tokens are kept in the vault (kind ``github-sign-in``), never in the configuration, the state, the log or a repository.
See ``docs/github-app-signin-spike.md``.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable
from urllib.parse import urlsplit

from . import github_api
from . import vault as vault_mod
from .target import TargetAuthError, TargetError, TargetUnavailable

LOGIN_HOST = "github.com"
DEVICE_CODE_URL = f"https://{LOGIN_HOST}/login/device/code"
TOKEN_URL = f"https://{LOGIN_HOST}/login/oauth/access_token"
ALLOWED_PATHS = ("/login/device/code", "/login/oauth/access_token")
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_MARGIN_SECONDS = 120                    # a token that expires within this is refreshed before use
REGISTRATION_DOC = "docs/github-app-registration.md"


class SignInError(TargetError):
    """The sign-in did not complete (message is user-facing)."""

    category = "sign-in"


class SignInNotConfigured(SignInError):
    category = "sign-in-not-configured"


class SignInDenied(SignInError):
    category = "sign-in-denied"


class SignInAgain(TargetAuthError):
    """The stored sign-in can no longer be used (expired, revoked, uninstalled): the person must sign in again. Nothing is written."""

    category = "sign-in-expired"


# --- the app's identity (a client ID is public information) --------------------------------------------------------------
def _settings_path():
    return vault_mod.data_dir() / "settings.json"


def shipped_github_app() -> dict:
    """The GitHub App the app ships with (public values), read from the packaged ``defaults.json``: the one place that names it."""
    from importlib.resources import files

    try:
        app = json.loads((files("mdpipeline") / "defaults.json").read_text(encoding="utf-8")).get("github_app") or {}
    except (OSError, ValueError):
        app = {}
    return {"client_id": str(app.get("client_id", "")), "slug": str(app.get("slug", ""))}


def _saved_app() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"client_id": "", "slug": ""}
    app = data.get("github_app") or {}
    return {"client_id": str(app.get("client_id", "")), "slug": str(app.get("slug", ""))}


def app_source() -> str:
    """Where the app identity in use comes from: ``user`` (saved by the user), ``default`` (shipped with the app) or ``none``."""
    if _saved_app()["client_id"]:
        return "user"
    return "default" if shipped_github_app()["client_id"] else "none"


def app_settings() -> dict:
    """The GitHub App in use: what the user saved wins over the shipped default."""
    saved = _saved_app()
    return saved if saved["client_id"] else shipped_github_app()


def reset_app_settings() -> None:
    """Forget the user's own app so the shipped default is used again (other settings in the file are kept)."""
    import os

    path = _settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.pop("github_app", None) is None:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def save_app_settings(client_id: str, slug: str) -> None:
    """Record the user's own GitHub App (client ID and slug, public values) in the app's data directory; it wins over the shipped default."""
    import os

    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["github_app"] = {"client_id": client_id.strip(), "slug": slug.strip()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def require_client_id() -> str:
    client_id = app_settings()["client_id"]
    if not client_id:
        raise SignInNotConfigured("Sign in with GitHub is not set up yet: the GitHub App has not been registered. Register it once with "
                                  f"{REGISTRATION_DOC}, then run `mdpipeline github-app set --client-id ID --slug SLUG`. "
                                  "Until then use a personal access token.")
    return client_id


# --- the network (two endpoints, https only) -------------------------------------------------------------------------------
def _post(url: str, form: dict) -> dict:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != LOGIN_HOST or parts.path not in ALLOWED_PATHS:
        raise ValueError(f"refusing to call {url}: only https://{LOGIN_HOST}{ALLOWED_PATHS} is allowed")
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "mdpipeline"}
    body = urllib.parse.urlencode(form).encode()
    transport = github_api._override or (lambda m, u, h, b: github_api.urllib_transport(m, u, h, b, allowed_host=LOGIN_HOST))
    try:
        status, _, raw = transport("POST", url, headers, body)
    except ValueError:
        raise
    except Exception as exc:                       # never include request details
        raise TargetUnavailable(f"Could not reach GitHub to sign in ({type(exc).__name__}). Check your network and try again.") from None
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    if status >= 500:
        raise TargetUnavailable(f"GitHub is not available right now (HTTP {status}). Try again later.")
    return payload if isinstance(payload, dict) else {}


# --- the device flow -----------------------------------------------------------------------------------------------------
@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def _device_error(error: str) -> SignInError:
    messages = {
        "device_flow_disabled": "The GitHub App has Device Flow switched off. In the app's settings on github.com tick \"Enable Device Flow\" (see "
                                f"{REGISTRATION_DOC}).",
        "incorrect_client_credentials": "GitHub does not know this app (wrong client ID). Check it with `mdpipeline github-app show`.",
        "expired_token": "The sign-in code expired. Start the sign-in again.",
        "access_denied": "The sign-in was cancelled on GitHub. Nothing was changed.",
        "incorrect_device_code": "GitHub did not recognise the sign-in code. Start the sign-in again.",
    }
    cls = SignInDenied if error == "access_denied" else SignInError
    return cls(messages.get(error, "GitHub refused the sign-in. Try again."))


def request_device_code(client_id: str) -> DeviceCode:
    data = _post(DEVICE_CODE_URL, {"client_id": client_id})
    if data.get("error") or not data.get("device_code"):
        raise _device_error(data.get("error", ""))
    return DeviceCode(data["device_code"], data["user_code"], data["verification_uri"], int(data.get("expires_in", 900)), int(data.get("interval", 5)))


@dataclass
class SignInTokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    refresh_expires_at: float | None = None
    login: str = ""

    @classmethod
    def from_response(cls, data: dict, now: float | None = None, login: str = "") -> "SignInTokens":
        now = time.time() if now is None else now
        return cls(data["access_token"], data.get("refresh_token"), now + data["expires_in"] if data.get("expires_in") else None,
                   now + data["refresh_token_expires_in"] if data.get("refresh_token_expires_in") else None, login)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "SignInTokens":
        return cls(**json.loads(text))

    def needs_refresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.expires_at is not None and now >= self.expires_at - REFRESH_MARGIN_SECONDS


def poll_once(client_id: str, device_code: str) -> "SignInTokens | int":
    """One poll of the device flow: the tokens when approved, otherwise the number of seconds to wait before the next poll."""
    data = _post(TOKEN_URL, {"client_id": client_id, "device_code": device_code, "grant_type": GRANT_DEVICE})
    error = data.get("error")
    if not error and data.get("access_token"):
        return SignInTokens.from_response(data)
    if error == "authorization_pending":
        return 0
    if error == "slow_down":
        return int(data.get("interval", 10))
    raise _device_error(error or "")


def wait_for_tokens(client_id: str, code: DeviceCode, *, sleep: Callable[[float], None] | None = None,
                    clock: Callable[[], float] | None = None, on_wait: Callable[[], None] | None = None) -> SignInTokens:
    """Poll at GitHub's interval (slow_down makes it longer) until the person approves, denies, or the code expires."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    deadline = clock() + code.expires_in
    interval = code.interval
    while True:
        if clock() >= deadline:
            raise SignInError("The sign-in code expired before it was approved. Start the sign-in again.")
        sleep(interval)
        if on_wait:
            on_wait()
        result = poll_once(client_id, code.device_code)
        if isinstance(result, SignInTokens):
            return result
        if result:
            interval = result


def refresh_tokens(client_id: str, tokens: SignInTokens) -> SignInTokens:
    """Refresh with the client ID and the refresh token only: the app has no client secret. A refusal means: sign in again."""
    if not tokens.refresh_token:
        raise SignInAgain("Your GitHub sign-in expired and cannot be renewed. Sign in again.")
    data = _post(TOKEN_URL, {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": tokens.refresh_token})
    if data.get("error") or not data.get("access_token"):
        raise SignInAgain("Your GitHub sign-in expired or was revoked. Sign in again (nothing was written or deleted).")
    return SignInTokens.from_response(data, login=tokens.login)


# --- what the GitHub client uses as its token -------------------------------------------------------------------------------
class SignInCredential:
    """The access token of a stored sign-in, refreshed before use. The new tokens replace the old ones in the vault first."""

    def __init__(self, secret_id: str, client_id: str | None = None) -> None:
        self.secret_id = secret_id
        self.client_id = client_id

    def _stored(self) -> SignInTokens:
        try:
            return SignInTokens.from_json(vault_mod.default_vault().get(self.secret_id))
        except vault_mod.VaultLocked:
            raise
        except (vault_mod.VaultError, ValueError, TypeError):
            raise SignInAgain("This pipeline's GitHub sign-in is not in the vault any more. Sign in again.") from None

    def token(self) -> str:
        """A valid access token. The vault is read every time, so a long-running process never keeps an expired or replaced token."""
        tokens = self._stored()
        if not tokens.needs_refresh():
            return tokens.access_token
        client_id = self.client_id or app_settings()["client_id"]
        if not client_id:
            raise SignInAgain("Your GitHub sign-in expired and this app does not know its GitHub App. Sign in again.")
        try:
            fresh = refresh_tokens(client_id, tokens)
        except SignInAgain:
            stored = self._stored()                      # another process may have refreshed first (the refresh token is single use)
            if stored.refresh_token != tokens.refresh_token and not stored.needs_refresh():
                return stored.access_token
            raise
        vault_mod.default_vault().replace(self.secret_id, fresh.to_json())      # the rotated tokens are stored before the new one is used
        return fresh.access_token


# --- installed repositories ------------------------------------------------------------------------------------------------
def installations(api: github_api.GitHubApi) -> list[dict]:
    return api.request("GET", "/user/installations", what="the GitHub App installations")["installations"]


def installed_repos(api: github_api.GitHubApi) -> list[str]:
    """Every repository the GitHub App is installed on for this person, as OWNER/REPO."""
    out: list[str] = []
    for inst in installations(api):
        data = api.request("GET", f"/user/installations/{inst['id']}/repositories", what="the installed repositories")
        out += [r["full_name"] for r in data["repositories"]]
    return sorted(set(out))


def installation_for(api: github_api.GitHubApi, repo: str) -> dict | None:
    """The installation of the GitHub App that covers ``repo`` for this person (None: the app is not installed on it)."""
    for inst in installations(api):
        data = api.request("GET", f"/user/installations/{inst['id']}/repositories", what="the installed repositories")
        if repo in {r["full_name"] for r in data["repositories"]}:
            return inst
    return None


def install_url(slug: str | None = None) -> str:
    slug = slug or app_settings()["slug"]
    return f"https://github.com/apps/{slug}/installations/new" if slug else "https://github.com/settings/installations"
