"""The GitHub REST client for the GitHub target (MD-61, MD-62, MD-65): the only module that talks to the network.

Rules (MD-65): HTTPS only, only the configured API host (default ``api.github.com``), the token goes in the
``Authorization`` header and nowhere else (never in a URL, a message or a log). Failures are mapped to ``TargetError``
categories; no response body is echoed back (it could contain anything). The transport is injectable
(``transport(method, url, headers, body) -> (status, headers, bytes)``) so tests run against ``tests/fake_github.py``.
"""
from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import quote, urlencode, urlsplit

from .target import (ConcurrentChange, TargetAuthError, TargetError, TargetProtected, TargetRateLimited,
                     TargetUnavailable)

DEFAULT_API_URL = "https://api.github.com"
TIMEOUT_SECONDS = 30
Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict, bytes]"]
_override: Transport | None = None      # tests install a fake with set_transport()


def set_transport(transport: Transport | None) -> None:
    """Route every GitHub client created afterwards through ``transport`` (tests); None restores the real network."""
    global _override
    _override = transport


def _allowed_host(api_url: str) -> str:
    parts = urlsplit(api_url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("the GitHub API URL must be https")
    return parts.hostname


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, *, allowed_host: str = "api.github.com"):
    """The real network call. Refuses anything that is not https to the allowed host before connecting."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != allowed_host:
        raise ValueError(f"refusing to call {parts.scheme}://{parts.hostname}: only https://{allowed_host} is allowed")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:     # noqa: S310 (scheme and host checked above)
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


class _Rejected(Exception):
    """Internal: GitHub answered 401 to this token (turned into the user-facing error, or a renewal and one retry, by ``request``)."""


class GitHubApi:
    def __init__(self, token: "str | Callable[[], str]", *, api_url: str = DEFAULT_API_URL, allowed_hosts: tuple[str, ...] = ("api.github.com",),
                 transport: Transport | None = None, renew: "Callable[[str], str] | None" = None,
                 still_rejected: "Callable[[], Exception] | None" = None) -> None:
        """``allowed_hosts`` is the whole network rule: the API URL must be https and its host must be listed
        (the configuration lists a GitHub Enterprise host explicitly; nothing else is ever called).

        ``renew`` (MD-187, a sign-in credential only): called with the token GitHub rejected (401), it returns a renewed one. The request is then
        retried once; if GitHub rejects the renewed token too, ``still_rejected()`` supplies the error. Without ``renew`` a 401 is final."""
        self._host = _allowed_host(api_url)
        if self._host not in allowed_hosts:
            raise ValueError(f"{self._host} is not a configured GitHub host")
        self.api_url = api_url.rstrip("/")
        self._token_source = token
        self._token_value: str | None = None
        self._renew, self._still_rejected = renew, still_rejected
        self._renew_lock = threading.Lock()
        host = self._host
        self._transport = transport or _override or (lambda m, u, h, b: urllib_transport(m, u, h, b, allowed_host=host))

    # -- plumbing --------------------------------------------------------------------
    def forget_token(self) -> None:
        """Resolve the token again on the next request (a long-running process must not keep a token across syncs: it may have
        expired, been refreshed or replaced)."""
        self._token_value = None

    def _token(self) -> str:
        """The token, resolved on first use (a missing credential is reported where it is needed) and kept in memory only."""
        if self._token_value is None:
            self._token_value = self._token_source() if callable(self._token_source) else self._token_source
        return self._token_value

    def _renewed_token(self, rejected: str) -> str:
        """The token to retry with after GitHub rejected ``rejected``. Threads that were rejected together renew once: the later ones find the new token."""
        with self._renew_lock:
            if self._token_value is not None and self._token_value != rejected:
                return self._token_value
            self._token_value = self._renew(rejected)
            return self._token_value

    def request(self, method: str, path: str, body: dict | None = None, query: dict | None = None, *, what: str = ""):
        token = self._token()
        try:
            return self._request_with(token, method, path, body, query, what)
        except _Rejected:
            if self._renew is None:
                raise self._error(401, {}, None, what, method, path) from None
        token = self._renewed_token(token)                  # a 401 on a sign-in: renew once, then try this request again before any error
        try:
            return self._request_with(token, method, path, body, query, what)
        except _Rejected:
            raise (self._still_rejected() if self._still_rejected else self._error(401, {}, None, what, method, path)) from None

    def _request_with(self, token: str, method: str, path: str, body: dict | None, query: dict | None, what: str):
        url = self.api_url + path + (("?" + urlencode(query)) if query else "")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != self._host:
            raise ValueError("refusing to call a host that is not the configured GitHub API host")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "mdpipeline"}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            status, resp_headers, raw = self._transport(method, url, headers, data)
        except ValueError:
            raise
        except Exception as exc:                       # network failure: never include request details (headers hold the token)
            raise TargetUnavailable(f"Could not reach GitHub ({type(exc).__name__}). Check your network and try again.") from None
        payload = None
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
        if 200 <= status < 300:
            return payload
        if status == 401:
            raise _Rejected()
        raise self._error(status, resp_headers, payload, what, method, path)

    @staticmethod
    def _error(status: int, headers: dict, payload, what: str, method: str, path: str) -> TargetError:
        message = (payload or {}).get("message", "") if isinstance(payload, dict) else ""
        lower = {k.lower(): v for k, v in (headers or {}).items()}
        label = what or "the GitHub request"
        if status == 401:
            return TargetAuthError("GitHub rejected the token (expired, revoked or wrong). Create a new fine-grained token and update it.")
        if status == 429 or (status == 403 and (lower.get("x-ratelimit-remaining") == "0" or "rate limit" in message.lower())):
            return TargetRateLimited("GitHub rate limit reached. Nothing was written; try again later.")
        if status == 403 and method == "PATCH" and "/git/refs/" in path and "protect" in message.lower():
            return TargetProtected("The branch is protected against direct pushes, so the pipeline cannot commit to it. "
                                   "Use a dedicated branch such as 'md-sync'.")
        if status == 403:
            return TargetAuthError(f"The token is not allowed to do this ({label}). It needs Contents read and write on this repository.")
        if status == 404:
            return TargetError(f"Not found: {label}. Check the repository name, the branch and that the token can see the repository.")
        if status == 422 and method == "PATCH" and "/git/refs/" in path and "fast forward" in message.lower():
            return ConcurrentChange("The branch moved while syncing.")
        if status >= 500:
            return TargetUnavailable(f"GitHub is not available right now (HTTP {status}). Nothing was written; try again later.")
        return TargetError(f"GitHub refused {label} (HTTP {status}).")

    # -- reads -----------------------------------------------------------------------
    def user(self) -> str:
        return self.request("GET", "/user", what="the token check")["login"]

    def repo(self, full_name: str) -> dict:
        return self.request("GET", f"/repos/{full_name}", what=f"repository {full_name}")

    def branches(self, full_name: str, limit: int = 100) -> list[dict]:
        """The first ``limit`` branches (name, protected): read-only, for the branch picker."""
        return self.request("GET", f"/repos/{full_name}/branches", query={"per_page": str(limit)}, what=f"the branches of {full_name}")

    def branch(self, full_name: str, branch: str) -> dict:
        return self.request("GET", f"/repos/{full_name}/branches/{quote(branch, safe='')}", what=f"branch {branch} of {full_name}")

    def head(self, full_name: str, branch: str) -> str:
        return self.request("GET", f"/repos/{full_name}/git/ref/heads/{quote(branch, safe='/')}", what=f"branch {branch} of {full_name}")["object"]["sha"]

    def commit(self, full_name: str, sha: str) -> dict:
        return self.request("GET", f"/repos/{full_name}/git/commits/{sha}", what=f"commit {sha[:7]}")

    def tree(self, full_name: str, sha: str) -> list[dict]:
        data = self.request("GET", f"/repos/{full_name}/git/trees/{sha}", query={"recursive": "1"}, what="the repository file list")
        if data.get("truncated"):
            raise TargetError("The repository is too large to list through the GitHub API in one call, so the pipeline cannot "
                              "tell what is in it. Nothing was written.")
        return data["tree"]

    def blob(self, full_name: str, sha: str) -> bytes:
        data = self.request("GET", f"/repos/{full_name}/git/blobs/{sha}", what="a repository file")
        return base64.b64decode(data["content"]) if data.get("encoding") == "base64" else data["content"].encode()

    def history(self, full_name: str, branch: str, path: str, limit: int = 10) -> list[dict]:
        return self.request("GET", f"/repos/{full_name}/commits", query={"sha": branch, "path": path, "per_page": limit},
                            what=f"the history of {path}")

    # -- writes (only the target's commit uses these) --------------------------------
    def create_blob(self, full_name: str, data: bytes) -> str:
        return self.request("POST", f"/repos/{full_name}/git/blobs",
                            {"content": base64.b64encode(data).decode(), "encoding": "base64"}, what="a new file")["sha"]

    def create_tree(self, full_name: str, base_tree: str, entries: list[dict]) -> str:
        return self.request("POST", f"/repos/{full_name}/git/trees", {"base_tree": base_tree, "tree": entries}, what="the new file list")["sha"]

    def create_commit(self, full_name: str, message: str, tree: str, parents: list[str]) -> dict:
        return self.request("POST", f"/repos/{full_name}/git/commits", {"message": message, "tree": tree, "parents": parents}, what="the commit")

    def update_ref(self, full_name: str, branch: str, sha: str) -> None:
        """Fast-forward the branch. ``force`` is always False: the client has no way to rewrite history."""
        self.request("PATCH", f"/repos/{full_name}/git/refs/heads/{quote(branch, safe='/')}", {"sha": sha, "force": False},
                     what=f"the update of branch {branch}")
