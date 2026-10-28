"""The Jira Cloud REST client (MD-46, MD-47, MD-50, MD-52): the second (and only other) module that talks to the network.

Rules (MD-47): READ-ONLY (only a fixed allow-list of GET endpoints plus the one POST that counts matches), HTTPS only, only the
configured Atlassian site host, credentials only in the ``Authorization`` header (never in a URL, message or log), no response body
echoed (except Jira's own JQL error text). Failures raise ``SourceError`` with a category; a rate limit (429) honours ``Retry-After``
(capped, bounded retries). The transport is injectable (``transport(method, url, headers, body) -> (status, headers, bytes)``) so tests
run against ``tests/fake_jira.py``.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlencode, urlsplit

from . import progress
from .sources import SourceError

MAX_RETRIES = 3
MAX_WAIT_SECONDS = 60
TIMEOUT_SECONDS = 30
PAGE_SIZE = 100
Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict, bytes]"]
_override: Transport | None = None
_sleep_override: Callable[[float], None] | None = None

# the whole read-only surface: (method, path regex)
ALLOWED = (
    ("GET", re.compile(r"^/rest/api/3/myself$")),
    ("GET", re.compile(r"^/rest/api/3/project/search$")),               # the projects this account can read (MD-141, the picker)
    ("GET", re.compile(r"^/rest/api/3/search/jql$")),
    ("POST", re.compile(r"^/rest/api/3/search/approximate-count$")),     # read-only despite POST: how many issues a JQL matches
    ("GET", re.compile(r"^/rest/api/3/issue/[A-Za-z0-9-]+$")),
    ("GET", re.compile(r"^/rest/api/3/issue/[A-Za-z0-9-]+/comment$")),
)


def set_transport(transport: Transport | None, sleep: Callable[[float], None] | None = None) -> None:
    """Route every Jira client created afterwards through ``transport`` (tests); None restores the real network."""
    global _override, _sleep_override
    _override, _sleep_override = transport, sleep


def site_host(site: str) -> str:
    parts = urlsplit(site)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host.endswith(".atlassian.net") or host == ".atlassian.net" or parts.path not in ("", "/") or parts.port:
        raise ValueError("the Jira site must be an https://<name>.atlassian.net URL")
    return host


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, *, allowed_host: str):
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


class JiraApi:
    def __init__(self, site: str, email: "str | Callable[[], str]", token: "str | Callable[[], str]", *, transport: Transport | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.host = site_host(site)
        self.site = f"https://{self.host}"
        self._email_source, self._token_source = email, token
        self._credentials: str | None = None
        self._identity_confirmed = False
        host = self.host
        self._transport = transport or _override or (lambda m, u, h, b: urllib_transport(m, u, h, b, allowed_host=host))
        self._sleep = sleep or _sleep_override or time.sleep

    def _auth(self) -> str:
        if self._credentials is None:
            email = self._email_source() if callable(self._email_source) else self._email_source
            token = self._token_source() if callable(self._token_source) else self._token_source
            self._credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
        return self._credentials

    # -- plumbing --------------------------------------------------------------------
    def request(self, method: str, path: str, query: dict | None = None, body: dict | None = None):
        if not any(m == method and rx.match(path) for m, rx in ALLOWED):
            raise ValueError(f"the Jira connector is read-only: {method} {path} is not allowed")
        url = f"{self.site}{path}" + (("?" + urlencode(query)) if query else "")
        headers = {"Authorization": f"Basic {self._auth()}", "Accept": "application/json", "User-Agent": "mdpipeline"}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, resp_headers, raw = self._transport(method, url, headers, data)
            except ValueError:
                raise
            except SourceError:
                raise
            except Exception as exc:               # network failure: never include request details (the headers hold the credentials)
                raise SourceError(f"Could not reach {self.host} ({type(exc).__name__}). Check your network and try again.", "network") from None
            if status == 429:
                if attempt == MAX_RETRIES:
                    raise SourceError("Jira rate limit reached and it did not clear after several waits. Nothing was changed; try again later.", "rate-limited")
                lower = {k.lower(): v for k, v in (resp_headers or {}).items()}
                try:
                    wait = min(max(float(lower.get("retry-after", 1)), 0), MAX_WAIT_SECONDS)
                except ValueError:
                    wait = 1
                self._sleep(int(wait) if wait == int(wait) else wait)
                continue
            return self._finish(status, raw, path)

    def _finish(self, status: int, raw: bytes, path: str):
        payload = None
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                if 200 <= status < 300:
                    raise SourceError("Jira answered with something that is not JSON, so nothing was read.", "malformed") from None
        if 200 <= status < 300:
            if payload is None and raw:
                raise SourceError("Jira answered with an empty or invalid body.", "malformed")
            return payload
        if status == 401:
            raise SourceError("Jira rejected the email or API token (wrong, expired or revoked). Create a new API token and update the variable or keychain entry.", "auth")
        if status == 403:
            raise SourceError("Jira says this account is not allowed to read that. Check the account's permission for the project.", "permission")
        if status == 404:
            if path.startswith("/rest/api/3/issue/"):
                raise SourceError("The issue no longer exists or is no longer visible to this account.", "item-gone")
            raise SourceError(f"The Jira site {self.host} was not found (or the API is not available there). Check the site URL.", "site-not-found")
        if status == 400:
            messages = (payload or {}).get("errorMessages") if isinstance(payload, dict) else None
            raise SourceError("Jira refused the query: " + (" ".join(str(m) for m in messages) if messages else "invalid request"), "bad-query")
        if status >= 500:
            raise SourceError(f"Jira is not available right now (HTTP {status}). Nothing was changed; try again later.", "network")
        raise SourceError(f"Jira refused the request (HTTP {status}).", "error")

    # -- reads -----------------------------------------------------------------------
    def myself(self) -> dict:
        me = self.request("GET", "/rest/api/3/myself")
        self._identity_confirmed = True
        return me

    def _confirm_identity(self) -> None:
        """MD-174: Jira Cloud serves a REJECTED token on search, the project list and the count as an anonymous request (200, empty);
        only ``/myself`` answers 401. So before any of those answers is trusted, the credentials are confirmed once (a failure raises
        ``auth`` and is not remembered): a revoked token can never look like "Jira has nothing", which would plan to delete every file."""
        if not self._identity_confirmed:
            self.myself()

    def projects(self, *, start_at: int = 0, max_results: int = PAGE_SIZE, query: str = "") -> dict:
        """One page of the projects the account can read, by name: ``{"values": [{"key", "name"}...], "isLast": bool, ...}``."""
        self._confirm_identity()
        params = {"startAt": start_at, "maxResults": max_results, "orderBy": "name", **({"query": query} if query else {})}
        page = self.request("GET", "/rest/api/3/project/search", params)
        if not isinstance(page, dict) or not isinstance(page.get("values"), list):
            raise SourceError("Jira answered a project list with an unexpected shape.", "malformed")
        return page

    def count(self, jql: str) -> int:
        self._confirm_identity()
        return int(self.request("POST", "/rest/api/3/search/approximate-count", body={"jql": jql})["count"])

    def list_issues(self, jql: str, fields: str, page_size: int = PAGE_SIZE) -> list[dict]:
        """Every issue the JQL matches, across all pages. Any failing page raises: a partial list is never returned."""
        self._confirm_identity()
        issues, token = [], None
        while True:
            query = {"jql": jql, "fields": fields, "maxResults": page_size, **({"nextPageToken": token} if token else {})}
            page = self.request("GET", "/rest/api/3/search/jql", query)
            if not isinstance(page, dict) or not isinstance(page.get("issues"), list):
                raise SourceError("Jira answered a search with an unexpected shape.", "malformed")
            issues += page["issues"]
            progress.report("listing", len(issues))
            if page.get("isLast", True) or not page.get("nextPageToken"):
                return issues
            token = page["nextPageToken"]

    def issue(self, issue_id: str) -> dict:
        return self.request("GET", f"/rest/api/3/issue/{issue_id}", {"fields": "*all"})

    def comments(self, issue_id: str, page_size: int = PAGE_SIZE) -> list[dict]:
        out, start = [], 0
        while True:
            page = self.request("GET", f"/rest/api/3/issue/{issue_id}/comment", {"startAt": start, "maxResults": page_size})
            out += page["comments"]
            start += len(page["comments"])
            if not page["comments"] or start >= int(page.get("total", 0)):
                return out
