"""A fake GitHub for tests (MD-59): an in-memory repo behind the same ``transport(method, url, headers, body)`` call
the real client uses, so no test ever touches the network.

It implements the Git Data API subset the GitHub target needs (user, repo, branch, ref, commit, tree, blob, history)
with the semantics that matter: a ref update that is not a fast-forward is refused (422), protected branches refuse
direct updates (403), a read-only token cannot write (403), trees can be reported truncated. Tests script races
(``before``/``commit_as_other``), failures (``fail``), rate limits (``rate_limit``) and read what happened
(``requests``, ``forced_updates``, ``is_linear_history``).

Object ids: blobs use the real git blob sha1; commit/tree ids are derived from their content (not real git ids).
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from typing import Callable
from urllib.parse import parse_qs, urlsplit

API_HOST = "api.github.com"
LOGIN_HOST = "github.com"


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    def __init__(self, repo: str = "acme/brain", branch: str = "main", token: str = "tok", user: str = "tester",
                 files: dict[str, bytes] | None = None) -> None:
        self.repo, self.default_branch, self.token, self.user = repo, branch, token, user
        self.permission = "write"
        self.max_tree_entries = 100_000
        self.forced_updates = 0
        self.requests: list[tuple[str, str]] = []          # (method, path) of every request received
        self.log: list[dict] = []                          # every request with its parsed body
        self._lock = threading.RLock()
        self._blobs: dict[str, bytes] = {}
        self._trees: dict[str, dict[str, tuple[str, str]]] = {}      # tree id -> {path: (mode, blob sha)}
        self._commits: dict[str, dict] = {}
        self._refs: dict[str, str] = {}
        self._protected: set[str] = set()
        self._before: list[tuple[str, Callable[[], None], list[int]]] = []
        self._failures: list[dict] = []
        self._rate_limited = 0
        self._counter = 0
        # GitHub App sign-in (MD-76): off until ``enable_app``
        self.app_client_id: str | None = None
        self.app_slug = "md-pipeline"
        self.device_flow_enabled = True
        self.refresh_requires_secret = False
        self.access_ttl = 28800
        self.rate_remaining = 5000
        self.device_interval = 5                            # seconds GitHub asks the client to wait between polls
        self.sign_in_log: list[dict] = []                  # every sign-in endpoint request (path + form), for network-rule tests
        self._device: dict | None = None
        self._slow_down = 0
        self._access: dict[str, bool] = {}                 # live user-to-server access tokens: token -> valid
        self._refresh: dict[str, bool] = {}                # refresh tokens: token -> still usable
        self._installed: set[str] = set()
        root = self._make_commit({p: ("100644", self._store_blob(d)) for p, d in (files or {}).items()}, [], "initial")
        self._refs[branch] = root

    # -- scripting API ---------------------------------------------------------------
    def head(self, branch: str | None = None) -> str:
        return self._refs[branch or self.default_branch]

    def read(self, path: str, ref: str | None = None) -> bytes | None:
        entry = self._tree_of(self._commits[ref or self.head()]["tree"]).get(path)
        return self._blobs[entry[1]] if entry else None

    def files(self, ref: str | None = None) -> dict[str, bytes]:
        return {p: self._blobs[e[1]] for p, e in self._tree_of(self._commits[ref or self.head()]["tree"]).items()}

    def protect(self, branch: str) -> None:
        self._protected.add(branch)

    def add_branch(self, name: str, from_branch: str | None = None) -> None:
        """A second branch at the same commit (for the branch picker)."""
        self._refs[name] = self._refs[from_branch or self.default_branch]

    def set_permission(self, permission: str) -> None:
        self.permission = permission

    def commit_as_other(self, files: dict[str, bytes | None], message: str = "someone else", branch: str | None = None) -> str:
        """Another person or agent commits on the branch (None deletes a path). Returns the new commit sha."""
        with self._lock:
            branch = branch or self.default_branch
            tree = dict(self._tree_of(self._commits[self._refs[branch]]["tree"]))
            for path, data in files.items():
                if data is None:
                    tree.pop(path, None)
                else:
                    tree[path] = ("100644", self._store_blob(data))
            sha = self._make_commit(tree, [self._refs[branch]], message)
            self._refs[branch] = sha
            return sha

    def before(self, method: str, action: Callable[[], None], times: int = 1, path_contains: str = "") -> None:
        """Run ``action`` just before the next ``times`` requests whose method matches (and path contains the text)."""
        self._before.append((f"{method}|{path_contains}", action, [times]))

    def fail(self, method: str, path_contains: str, status: int = 500, times: int = 1, message: str = "scripted failure") -> None:
        self._failures.append({"method": method, "path": path_contains, "status": status, "times": times, "message": message})

    def rate_limit(self, times: int = 1) -> None:
        self._rate_limited = times

    def history(self, branch: str | None = None) -> list[str]:
        """Commit shas from the first commit to the head, following first parents."""
        out, sha = [], self.head(branch)
        while sha:
            out.append(sha)
            parents = self._commits[sha]["parents"]
            sha = parents[0] if parents else ""
        return out[::-1]

    def is_linear_history(self, branch: str | None = None) -> bool:
        """True when every commit in the head's history has at most one parent (no merges, no rewrites)."""
        return all(len(self._commits[s]["parents"]) <= 1 for s in self.history(branch))

    def commit_message(self, sha: str) -> str:
        return self._commits[sha]["message"]

    def commit_files_changed(self, sha: str) -> dict[str, bytes | None]:
        """The paths a commit changed relative to its first parent (None = deleted)."""
        parents = self._commits[sha]["parents"]
        before = self._tree_of(self._commits[parents[0]]["tree"]) if parents else {}
        after = self._tree_of(self._commits[sha]["tree"])
        out: dict[str, bytes | None] = {p: self._blobs[e[1]] for p, e in after.items() if before.get(p) != e}
        out.update({p: None for p in before if p not in after})
        return out

    # -- GitHub App sign-in scripting (MD-76) -----------------------------------------
    def enable_app(self, client_id: str = "Iv1.fake", slug: str = "md-pipeline", access_ttl: int = 28800) -> None:
        """Register a GitHub App on this fake: its client id answers the device flow."""
        self.app_client_id, self.app_slug, self.access_ttl = client_id, slug, access_ttl

    def _pending_device(self, timeout: float) -> dict:
        """The device code the app asked for. A browser test clicks 'Sign in' and calls this at once, before the server has made that request
        (MD-169: this race made a test fail now and then), so wait a little for it."""
        import time

        end = time.monotonic() + timeout
        while self._device is None and time.monotonic() < end:
            time.sleep(0.02)
        assert self._device is not None, f"no device code was requested within {timeout}s: the app never started the sign-in"
        return self._device

    def authorize_device(self, timeout: float = 10.0) -> None:
        """The person types the user code on github.com/login/device and approves."""
        self._pending_device(timeout)["state"] = "approved"

    def deny_device(self, timeout: float = 10.0) -> None:
        self._pending_device(timeout)["state"] = "denied"

    def expire_device_code(self, timeout: float = 10.0) -> None:
        self._pending_device(timeout)["state"] = "expired"

    def slow_down(self, times: int = 1) -> None:
        self._slow_down = times

    def expire_access_tokens(self) -> None:
        """Every access token stops working (as after the 8 hours); refresh tokens still work."""
        self._access = dict.fromkeys(self._access, False)

    def revoke_tokens(self) -> None:
        """The person revoked the app, or uninstalled it: access and refresh tokens are all dead."""
        self._access = dict.fromkeys(self._access, False)
        self._refresh = dict.fromkeys(self._refresh, False)

    def install_app(self, repos: list[str]) -> None:
        self._installed |= set(repos)

    # -- internals -------------------------------------------------------------------
    def _store_blob(self, data: bytes) -> str:
        sha = blob_sha(data)
        self._blobs[sha] = data
        return sha

    def _tree_of(self, tree_id: str) -> dict[str, tuple[str, str]]:
        return self._trees[tree_id]

    def _store_tree(self, entries: dict[str, tuple[str, str]]) -> str:
        sha = hashlib.sha1(json.dumps(sorted(entries.items())).encode()).hexdigest()
        self._trees[sha] = dict(entries)
        return sha

    def _make_commit(self, entries: dict[str, tuple[str, str]], parents: list[str], message: str) -> str:
        self._counter += 1
        tree = self._store_tree(entries)
        sha = hashlib.sha1(f"{tree}|{parents}|{message}|{self._counter}".encode()).hexdigest()
        self._commits[sha] = {"tree": tree, "parents": parents, "message": message}
        return sha

    def _commit_json(self, sha: str) -> dict:
        c = self._commits[sha]
        return {"sha": sha, "tree": {"sha": c["tree"]}, "parents": [{"sha": p} for p in c["parents"]], "message": c["message"],
                "html_url": f"https://github.com/{self.repo}/commit/{sha}"}

    @staticmethod
    def _reply(status: int, body, headers: dict | None = None):
        return status, headers or {}, json.dumps(body).encode()

    # -- the transport ---------------------------------------------------------------
    def __call__(self, method: str, url: str, headers: dict, body: bytes | None):
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname == LOGIN_HOST:
            with self._lock:
                return self._login(method, parts.path, {k: v[0] for k, v in parse_qs((body or b"").decode()).items()})
        if parts.scheme != "https" or parts.hostname != API_HOST:
            raise AssertionError(f"the fake only serves https://{API_HOST} and https://{LOGIN_HOST}/login, got {url}")
        with self._lock:
            status, resp_headers, data = self._handle(method, parts.path, parse_qs(parts.query), headers, json.loads(body) if body else None)
            self.rate_remaining = max(0, self.rate_remaining - 1)
            resp_headers = {"x-ratelimit-limit": "5000", "x-ratelimit-remaining": str(self.rate_remaining), "x-ratelimit-reset": "1999999999", **resp_headers}
            return status, resp_headers, data

    def _handle(self, method: str, path: str, query: dict, headers: dict, body):
        self.requests.append((method, path))
        self.log.append({"method": method, "path": path, "query": query, "body": body})
        for key, action, left in list(self._before):
            m, contains = key.split("|", 1)
            if m == method and contains in path and left[0] > 0:
                left[0] -= 1
                action()
        if self._rate_limited > 0:
            self._rate_limited -= 1
            return self._reply(403, {"message": "API rate limit exceeded"}, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1999999999"})
        for f in self._failures:
            if f["method"] == method and f["path"] in path and f["times"] > 0:
                f["times"] -= 1
                return self._reply(f["status"], {"message": f["message"]})
        auth = {k.lower(): v for k, v in headers.items()}.get("authorization", "")
        bearer = auth.split(" ", 1)[1] if " " in auth else ""
        user_token = bearer in self._access
        if bearer != self.token and not (user_token and self._access[bearer]):
            return self._reply(401, {"message": "Bad credentials"})
        if path == "/user":
            return self._reply(200, {"login": self.user})
        if path.startswith("/user/installations"):
            return self._installations(path, user_token)
        if path == "/rate_limit":
            return self._reply(200, {"resources": {"core": {"limit": 5000, "remaining": self.rate_remaining, "reset": 1999999999}}})
        prefix = f"/repos/{self.repo}"
        if not (path == prefix or path.startswith(prefix + "/")) or (user_token and self.repo not in self._installed):
            return self._reply(404, {"message": "Not Found"})
        rest = path[len(prefix):]
        write = self.permission == "write"
        if rest == "" and method == "GET":
            return self._reply(200, {"full_name": self.repo, "default_branch": self.default_branch,
                                     "permissions": {"push": write, "pull": True}})
        if rest == "/branches" and method == "GET":
            return self._reply(200, [{"name": n, "protected": n in self._protected, "commit": {"sha": s}} for n, s in sorted(self._refs.items())])
        if rest.startswith("/branches/") and method == "GET":
            name = rest[len("/branches/"):]
            if name not in self._refs:
                return self._reply(404, {"message": "Branch not found"})
            return self._reply(200, {"name": name, "protected": name in self._protected, "commit": {"sha": self._refs[name]}})
        if method == "POST" and rest == "/git/refs":
            if not write:
                return self._reply(403, {"message": "Resource not accessible by personal access token"})
            name = (body or {}).get("ref", "").removeprefix("refs/heads/")
            if not name or name in self._refs:
                return self._reply(422, {"message": "Reference already exists"})
            if (body or {}).get("sha") not in self._commits:
                return self._reply(422, {"message": "Object does not exist"})
            self._refs[name] = body["sha"]
            return self._reply(201, {"ref": f"refs/heads/{name}", "object": {"sha": body["sha"], "type": "commit"}})
        if method == "DELETE" and rest.startswith("/git/refs/heads/"):
            name = rest[len("/git/refs/heads/"):]
            if not write or name not in self._refs:
                return self._reply(403 if not write else 404, {"message": "Not Found"})
            if name == self.default_branch:
                return self._reply(422, {"message": "Cannot delete the default branch"})
            del self._refs[name]
            return 204, {}, b""
        for ref_prefix in ("/git/ref/heads/", "/git/refs/heads/"):
            if rest.startswith(ref_prefix):
                name = rest[len(ref_prefix):]
                if method == "GET":
                    if name not in self._refs:
                        return self._reply(404, {"message": "Not Found"})
                    return self._reply(200, {"ref": f"refs/heads/{name}", "object": {"sha": self._refs[name], "type": "commit"}})
                if method == "PATCH":
                    return self._update_ref(name, body, write)
        if rest.startswith("/git/commits/") and method == "GET":
            sha = rest[len("/git/commits/"):]
            return self._reply(200, self._commit_json(sha)) if sha in self._commits else self._reply(404, {"message": "Not Found"})
        if rest.startswith("/git/trees/") and method == "GET":
            return self._get_tree(rest[len("/git/trees/"):], query)
        if rest.startswith("/git/blobs/") and method == "GET":
            sha = rest[len("/git/blobs/"):]
            if sha not in self._blobs:
                return self._reply(404, {"message": "Not Found"})
            data = self._blobs[sha]
            return self._reply(200, {"sha": sha, "size": len(data), "encoding": "base64", "content": base64.b64encode(data).decode()})
        if rest == "/commits" and method == "GET":
            return self._list_commits(query)
        if method == "POST" and rest in ("/git/blobs", "/git/trees", "/git/commits"):
            if not write:
                return self._reply(403, {"message": "Resource not accessible by personal access token"})
            return {"/git/blobs": self._post_blob, "/git/trees": self._post_tree, "/git/commits": self._post_commit}[rest](body)
        return self._reply(404, {"message": "Not Found"})

    def _installations(self, path: str, user_token: bool):
        if not user_token:
            return self._reply(403, {"message": "You must authenticate with an access token authorized to a GitHub App in order to list installations"})
        write = self.permission == "write"
        installation = {"id": 4242, "app_slug": self.app_slug, "account": {"login": self.repo.split("/")[0]},
                        "permissions": {"contents": "write" if write else "read", "metadata": "read"}}
        if path == "/user/installations":
            installs = [installation] if self._installed else []
            return self._reply(200, {"total_count": len(installs), "installations": installs})
        if path == "/user/installations/4242/repositories" and self._installed:
            repos = [{"full_name": r, "default_branch": self.default_branch, "permissions": {"push": write, "pull": True}}
                     for r in sorted(self._installed)]
            return self._reply(200, {"total_count": len(repos), "repositories": repos})
        return self._reply(404, {"message": "Not Found"})

    def _login(self, method: str, path: str, form: dict):
        """github.com/login/device/code and /login/oauth/access_token (device flow and refresh), always answered as JSON."""
        self.sign_in_log.append({"method": method, "path": path, "form": dict(form)})
        if method != "POST" or path not in ("/login/device/code", "/login/oauth/access_token") or self.app_client_id is None:
            return self._reply(404, {"message": "Not Found"})
        if form.get("client_id") != self.app_client_id:
            return self._reply(200, {"error": "incorrect_client_credentials"})
        if path == "/login/device/code":
            if not self.device_flow_enabled:
                return self._reply(200, {"error": "device_flow_disabled"})
            self._counter += 1
            self._device = {"device_code": f"dc{self._counter}", "user_code": "WDJB-MJHT", "state": "pending"}
            return self._reply(200, {"device_code": self._device["device_code"], "user_code": "WDJB-MJHT",
                                     "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": self.device_interval})
        if form.get("grant_type") == "refresh_token":
            return self._refresh_grant(form)
        device = self._device
        if not device or form.get("device_code") != device["device_code"]:
            return self._reply(200, {"error": "incorrect_device_code"})
        if self._slow_down > 0:
            self._slow_down -= 1
            return self._reply(200, {"error": "slow_down", "interval": self.device_interval + 5})
        if device["state"] in ("denied", "expired"):
            return self._reply(200, {"error": "access_denied" if device["state"] == "denied" else "expired_token"})
        if device["state"] == "pending":
            return self._reply(200, {"error": "authorization_pending"})
        self._device = None
        return self._reply(200, self._issue_tokens())

    def _refresh_grant(self, form: dict):
        if self.refresh_requires_secret and not form.get("client_secret"):
            return self._reply(200, {"error": "incorrect_client_credentials"})
        if not self._refresh.get(form.get("refresh_token", ""), False):
            return self._reply(200, {"error": "bad_refresh_token"})
        self._refresh[form["refresh_token"]] = False                      # a refresh token works once
        return self._reply(200, self._issue_tokens())

    def _issue_tokens(self) -> dict:
        self._counter += 1
        access, refresh = f"ghu_access{self._counter}", f"ghr_refresh{self._counter}"
        self._access[access], self._refresh[refresh] = True, True
        return {"access_token": access, "expires_in": self.access_ttl, "refresh_token": refresh,
                "refresh_token_expires_in": 15897600, "token_type": "bearer", "scope": ""}

    def _update_ref(self, name: str, body: dict, write: bool):
        if not write:
            return self._reply(403, {"message": "Resource not accessible by personal access token"})
        if name not in self._refs:
            return self._reply(404, {"message": "Reference does not exist"})
        if name in self._protected:
            return self._reply(403, {"message": "Protected branch update failed: changes must be made through a pull request"})
        sha, force = body["sha"], bool(body.get("force", False))
        if sha not in self._commits:
            return self._reply(422, {"message": "Object does not exist"})
        if not force and self._refs[name] not in self._ancestors(sha):
            return self._reply(422, {"message": "Update is not a fast forward"})
        if force:
            self.forced_updates += 1
        self._refs[name] = sha
        return self._reply(200, {"ref": f"refs/heads/{name}", "object": {"sha": sha, "type": "commit"}})

    def _ancestors(self, sha: str) -> set[str]:
        seen, stack = set(), [sha]
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            stack.extend(self._commits[s]["parents"])
        return seen

    def _get_tree(self, tree_id: str, query: dict):
        if tree_id not in self._trees:
            return self._reply(404, {"message": "Not Found"})
        entries = sorted(self._trees[tree_id].items())
        truncated = len(entries) > self.max_tree_entries
        entries = entries[: self.max_tree_entries]
        return self._reply(200, {"sha": tree_id, "truncated": truncated, "tree": [
            {"path": p, "mode": mode, "type": "blob", "sha": sha, "size": len(self._blobs[sha])} for p, (mode, sha) in entries]})

    def _list_commits(self, query: dict):
        branch = (query.get("sha") or [self.default_branch])[0]
        path = (query.get("path") or [""])[0]
        limit = int((query.get("per_page") or ["30"])[0])
        out = []
        for sha in reversed(self.history(branch)):
            parents = self._commits[sha]["parents"]
            before = self._tree_of(self._commits[parents[0]]["tree"]).get(path) if parents else None
            after = self._tree_of(self._commits[sha]["tree"]).get(path)
            if not path or before != after:
                out.append({"sha": sha, "commit": {"message": self._commits[sha]["message"]}, "html_url": f"https://github.com/{self.repo}/commit/{sha}"})
        return self._reply(200, out[:limit])

    def _post_blob(self, body: dict):
        data = base64.b64decode(body["content"]) if body.get("encoding") == "base64" else body["content"].encode()
        return self._reply(201, {"sha": self._store_blob(data)})

    def _post_tree(self, body: dict):
        base = dict(self._trees[body["base_tree"]]) if body.get("base_tree") else {}
        for e in body["tree"]:
            if e.get("sha") is None:
                base.pop(e["path"], None)
            else:
                if e["sha"] not in self._blobs:
                    return self._reply(422, {"message": "GitRPC::BadObjectState"})
                base[e["path"]] = (e.get("mode", "100644"), e["sha"])
        return self._reply(201, {"sha": self._store_tree(base)})

    def _post_commit(self, body: dict):
        if body["tree"] not in self._trees or any(p not in self._commits for p in body["parents"]):
            return self._reply(422, {"message": "Object does not exist"})
        self._counter += 1
        sha = hashlib.sha1(f"{body['tree']}|{body['parents']}|{body['message']}|{self._counter}".encode()).hexdigest()
        self._commits[sha] = {"tree": body["tree"], "parents": list(body["parents"]), "message": body["message"]}
        return self._reply(201, self._commit_json(sha))
