"""The local web UI server: a tiny stdlib HTTP server bound to 127.0.0.1, serving a static single-page
app (no external fonts, scripts or CDNs) and a JSON API over ``uiapi``.

Safety: loopback only; requests whose Host header is not loopback are refused (DNS rebinding); every
mutating request needs a per-session token (sent by the page in ``X-MDP-Token``) and must not carry a
foreign Origin (CSRF).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import uiactivity, uieditor, uiapi, uijira, uitargets, uiview, usage_hooks
from .brain import Brain
from .locking import BusyError
from .target import TargetError
from .vault import VaultError, readiness as vault_readiness
from .service import NotFound, ValidationFailed

STATIC_DIR = Path(__file__).parent / "ui_static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".svg": "image/svg+xml", ".md": "text/plain; charset=utf-8"}
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra) -> None:
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


class UIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, brain: Brain, port: int, opener=None) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.brain = brain
        self.token = secrets.token_urlsafe(24)
        self.opener = opener
        self.running: set[str] = set()
        self.running_lock = threading.Lock()
        self.opened = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


def make_server(brain: Brain, port: int = 0, opener=None) -> UIServer:
    return UIServer(brain, port, opener)


class Handler(BaseHTTPRequestHandler):
    server: UIServer
    server_version = "mdpipeline-ui"

    def log_message(self, fmt, *args) -> None:  # keep the terminal quiet
        pass

    # ---- plumbing
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0] if not (self.headers.get("Host") or "").startswith("[") else "[::1]"
        return host in LOOPBACK_HOSTS

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            raise ApiError(400, "invalid JSON body") from None
        return data if isinstance(data, dict) else {}

    def _guard_mutation(self) -> None:
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ApiError(403, "cross-origin requests are not allowed")
        if not secrets.compare_digest(self.headers.get("X-MDP-Token") or "", self.server.token):
            raise ApiError(403, "missing or invalid session token")

    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_ok():
                raise ApiError(403, "forbidden host")
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if method != "GET":
                self._guard_mutation()
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            result = self.route(method, path, query)
            self._json(200, result)
        except ApiError as exc:
            self._json(exc.status, {"error": exc.message, **exc.extra})
        except ValidationFailed as exc:
            self._json(422, {"error": str(exc), "errors": [vars(i) for i in exc.result.errors],
                             "warnings": [vars(i) for i in exc.result.warnings]})
        except NotFound as exc:
            self._json(404, {"error": f"not found: {exc}"})
        except BusyError as exc:
            self._json(409, {"error": str(exc), "result": "busy"})
        except TargetError as exc:                # e.g. sign-in not set up, GitHub unreachable: plain words, never a secret
            self._json(409, {"error": str(exc), "category": exc.category, "needs_passphrase": bool(getattr(exc, "needs_passphrase", False))})
        except VaultError as exc:                 # a locked vault (passphrase needed) or a refused removal: plain words, never a secret
            self._json(409, {"error": str(exc), "needs_passphrase": bool(getattr(exc, "needs_passphrase", False))})
        except Exception as exc:  # never leak a stack trace to the browser
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        file = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in file.parents or not file.is_file():
            raise ApiError(404, "not found")
        body = file.read_bytes()
        if name == "index.html":
            body = body.replace(b"__MDP_TOKEN__", self.server.token.encode())
            if not self.server.opened:                    # MD-116: once per server run, not per reload
                self.server.opened = True
                usage_hooks.app_opened()
        self._send(200, body, CONTENT_TYPES.get(file.suffix, "application/octet-stream"))

    # ---- routes
    def route(self, method: str, path: str, query: dict):
        brain = self.server.brain
        if method == "GET" and path == "/api/state":
            return uiapi.state_view(brain, running=tuple(self.server.running))
        if method == "GET" and path == "/api/version":
            return uiapi.version_view(brain)
        if method == "GET" and path == "/api/usage":
            return uiapi.usage_view()
        if method == "POST" and path == "/api/usage/consent":
            try:
                return uiapi.set_usage_consent(self._body().get("choice", ""))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        if method == "POST" and path == "/api/usage/event":
            return uiapi.usage_event(self._body())
        if method == "POST" and path == "/api/usage/reset-id":
            return uiapi.reset_usage_install_id()
        if method == "POST" and path == "/api/background/ack":
            from .scheduler import acknowledge_announcement

            acknowledge_announcement()
            return uiapi.background_view()
        if method == "POST" and path == "/api/background/linger-hide":
            from .scheduler import hide_linger_notice

            hide_linger_notice()
            from . import usage_hooks

            usage_hooks.linger_notice_hidden("home")
            return uiapi.background_view()
        if method == "POST" and path == "/api/background":
            try:
                return uiapi.set_background_view(self._body().get("enabled"))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        m = re.fullmatch(r"/api/pipelines/([^/]+)/schedule", path)
        if method == "POST" and m:
            self._known(m.group(1))
            try:
                return uiapi.set_schedule_view(brain, m.group(1), self._body().get("interval"))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        if method == "POST" and path == "/api/sync-all":
            return self._tracked(None, lambda: uiapi.run_sync_all(brain))
        if method == "POST" and path == "/api/state/restore":
            from .recovery import RestoreError

            body = self._body()
            try:
                return uiapi.restore_state(brain, bool(body.get("apply")), body.get("generation"))
            except RestoreError as exc:
                raise ApiError(409, str(exc)) from exc
        if method == "GET" and path == "/api/prompt":
            from . import feedback

            return {"prompt": feedback.next_prompt(brain)}
        if method == "POST" and path == "/api/feedback/first-run-ended":
            from . import feedback

            feedback.note_first_run_ended()
            return {"ok": True}
        if method == "POST" and path in ("/api/prompt/answer", "/api/prompt/dismiss", "/api/source-request"):
            from . import feedback, usage

            body = self._body()
            try:
                if path == "/api/prompt/dismiss":
                    feedback.dismiss(str(body.get("question_id", "")))
                    return {"ok": True}
                if path == "/api/prompt/answer":
                    return feedback.answer(str(body.get("question_id", "")), str(body.get("answer", "")), str(body.get("comment", "")), usage.get_transport())
                return feedback.request_source(str(body.get("source_id", "")), str(body.get("other_source", "")), str(body.get("email", "")), usage.get_transport())
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        if method == "GET" and path == "/api/agents":
            from . import agents

            return agents.view(brain, (query.get("pipeline") or [""])[0])
        if method == "POST" and path in ("/api/agents/connect", "/api/agents/disconnect"):
            from . import agents, usage
            from .config import load_config
            from .destinations import directory_root

            body = self._body()
            agent = str(body.get("agent", ""))
            if agent not in agents.AGENTS:
                raise ApiError(400, "unknown agent")
            if path.endswith("disconnect"):
                return agents.disconnect(brain, agent)
            pipeline = load_config(brain).get(str(body.get("pipeline", "")))
            if pipeline is None:
                raise ApiError(404, "unknown pipeline")
            how = body.get("agents_md") if body.get("agents_md") in ("keep", "replace") else None
            res = agents.connect(brain, agent, str(directory_root(brain, pipeline.destination)), how)
            if res.get("ok"):
                usage.emit("agent_connected", agent=agent, method="auto")
            return res
        if method == "POST" and path == "/api/first-run/review":
            from . import firstrun

            return firstrun.review(brain, self._body())
        if method == "GET" and path == "/api/first-run/activity":
            from . import progress

            return {"activity": progress.current()}
        if path == "/api/first-run/progress":
            from . import firstrun

            if method == "GET":
                return {"progress": firstrun.read_progress(), "defaults": {"jql": firstrun.DEFAULT_JQL, "token_page": firstrun.JIRA_TOKEN_PAGE},
                        "vault": vault_readiness()}
            if method == "POST":
                firstrun.save_progress(self._body())
                return {"progress": firstrun.read_progress()}
        if method == "POST" and path == "/api/first-run/open-folder":
            from . import firstrun

            try:
                return firstrun.open_folder(brain, str(self._body().get("pipeline", "")), self.server.opener)
            except KeyError as exc:
                raise ApiError(404, "unknown pipeline") from exc
        if method == "POST" and path == "/api/first-run/dest-check":
            from . import firstrun

            return firstrun.dest_check(self._body())
        if method == "POST" and path == "/api/first-run/jira-test":
            from . import firstrun

            return firstrun.jira_test(self._body())
        if method == "POST" and path == "/api/first-run/write":
            from . import firstrun

            body = self._body()
            return self._tracked(None, lambda: firstrun.write(brain, body, sync_fn=lambda n: uiapi.run_sync(brain, n)))
        if method == "POST" and path in ("/api/sample/later", "/api/sample/remove"):
            from . import sample

            try:
                if path.endswith("later"):
                    return self._tracked(sample.NAME, lambda: sample.later(brain, lambda n: uiapi.run_sync(brain, n)))
                return self._tracked(sample.NAME, lambda: sample.remove(brain))
            except KeyError as exc:
                raise ApiError(404, "the sample is not set up") from exc
        if method == "POST" and path == "/api/folder-browse":
            from . import folderbrowser

            body = self._body()
            return folderbrowser.list_dir(brain, str(body.get("path", "")), show_hidden=bool(body.get("show_hidden")), start=int(body.get("start") or 0))
        if method == "POST" and path == "/api/folder-browse/new":
            from . import folderbrowser

            body = self._body()
            try:
                return folderbrowser.new_folder(str(body.get("parent", "")), str(body.get("name", "")))
            except folderbrowser.NewFolderError as exc:
                raise ApiError(exc.status, exc.message) from exc
        if method == "POST" and path == "/api/jira/projects":
            from . import jira_picker

            return jira_picker.list_projects(self._body())
        if method == "POST" and path == "/api/jira/build-jql":
            from . import jira_picker

            try:
                return jira_picker.pick(self._body())
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        if method == "POST" and path == "/api/jira-test":
            return uijira.test_connection(self._body())
        if method == "POST" and path == "/api/github/branches":
            return uitargets.list_branches(brain, self._body())
        if method == "POST" and path == "/api/target/test":
            return uitargets.test_target(brain, self._body())
        if method == "POST" and path == "/api/github-app/reset":
            return uitargets.reset_github_app()
        if method == "POST" and path == "/api/github-app":
            return uitargets.save_github_app(self._body())
        if method == "POST" and path == "/api/signin/start":
            return uitargets.signin_start(self._body())
        if method == "POST" and path == "/api/signin/poll":
            return uitargets.signin_poll(self._body())
        if method == "POST" and path == "/api/signin/repos":
            return uitargets.signin_repos(self._body())
        m = re.fullmatch(r"/api/pipelines/([^/]+)/sign-in", path)
        if m and method == "POST":
            return uitargets.sign_in_again(brain, m.group(1), self._body())
        if m and method == "DELETE":
            return uitargets.sign_out(brain, m.group(1))
        if method == "GET" and path == "/api/vault":
            return uitargets.vault_view(brain)
        if method == "POST" and path == "/api/vault/unlock":
            return uitargets.unlock_vault(self._body())
        if method == "POST" and path == "/api/vault/import":
            return uitargets.import_secrets(brain)
        if method == "POST" and path == "/api/vault/remove":
            return uitargets.remove_vault_secret(brain, self._body())
        m = re.fullmatch(r"/api/pipelines/([^/]+)/token", path)
        if m and method == "POST":
            return uitargets.replace_pipeline_token(brain, m.group(1), self._body())
        if m and method == "DELETE":
            return uitargets.remove_pipeline_token(brain, m.group(1))
        if method == "GET" and path == "/api/activity":
            return uiactivity.activity_view(brain, (query.get("pipeline") or [None])[0])
        if method == "POST" and path == "/api/open":
            try:
                return uiapi.open_file(brain, str(self._body().get("path", "")), self.server.opener)
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        m = re.fullmatch(r"/api/pipelines/([^/]+)/remove", path)
        if m and method == "POST":
            from . import uiremove

            self._known(m.group(1))
            try:
                return self._tracked(m.group(1), lambda: uiremove.remove(brain, m.group(1), self._body()))
            except uiremove.ConfirmationRequired as exc:
                raise ApiError(409, str(exc), count=exc.count) from exc
        m = re.fullmatch(r"/api/pipelines/([^/]+)/(source-path|remove-source)", path)
        if method == "POST" and m:
            body = self._body()
            self._known(m.group(1))
            try:
                if m.group(2) == "source-path":
                    status, payload = self._tracked(m.group(1), lambda: uieditor.set_source_path(brain, m.group(1), str(body.get("label", "")), str(body.get("path", ""))))
                    if status != 200:
                        raise ApiError(status, payload.pop("message", "blocked"), **payload)
                    return payload
                return self._tracked(m.group(1), lambda: uieditor.remove_source_keep_files(brain, m.group(1), str(body.get("label", ""))))
            except uieditor.SourceChangeRefused as exc:
                raise ApiError(exc.status, str(exc)) from exc
        m = re.fullmatch(r"/api/pipelines/([^/]+)/(sync|pause|resume|approve|cancel|force)", path)
        if method == "POST" and m:
            name, action = m.groups()
            self._known(name)
            if action == "sync":
                return self._tracked(name, lambda: uiapi.run_sync(brain, name))
            if action == "approve":
                return self._tracked(name, lambda: uiapi.run_approve(brain, name))
            if action == "cancel":
                try:
                    return uiapi.run_cancel(brain, name)
                except ValueError as exc:
                    raise ApiError(409, str(exc)) from exc
            if action == "force":
                paths = [str(p) for p in (self._body().get("paths") or [])]
                if not paths:
                    raise ApiError(400, "force needs the path(s) of the conflicting file(s)")
                return self._tracked(name, lambda: uiapi.run_force(brain, name, paths))
            return uiapi.set_paused_view(brain, name, action == "pause")
        if method == "POST" and path == "/api/folder-check":
            return uieditor.folder_check(str(self._body().get("path", "")))
        if method == "POST" and path == "/api/preview":
            return uieditor.preview(brain, self._body())
        if method == "POST" and path == "/api/pipelines":
            body = self._body()
            return self._tracked(None, lambda: uieditor.create(brain, body, sync_fn=lambda n: uiapi.run_sync(brain, n)))
        m = re.fullmatch(r"/api/pipelines/([^/]+)/view", path)
        if m and method == "GET":
            return uiview.pipeline_detail(brain, m.group(1))
        m = re.fullmatch(r"/api/pipelines/([^/]+)/remove-preview", path)
        if m and method == "GET":
            from . import uiremove

            self._known(m.group(1))
            return uiremove.preview(brain, m.group(1))
        m = re.fullmatch(r"/api/pipelines/([^/]+)/plan", path)
        if m and method == "GET":
            return uiview.plan_view(brain, m.group(1))
        m = re.fullmatch(r"/api/pipelines/([^/]+)", path)
        if m and method == "GET":
            return uieditor.pipeline_view(brain, m.group(1))
        if m and method == "PUT":
            body = self._body()
            status, payload = self._tracked(m.group(1), lambda: uieditor.update(brain, m.group(1), body))
            if status != 200:
                raise ApiError(status, payload.pop("message", "blocked"), **payload)
            return payload
        raise ApiError(404, "unknown endpoint")

    def _known(self, name: str) -> None:
        from .config import load_config

        if load_config(self.server.brain).get(name) is None:
            raise ApiError(404, f"no pipeline named '{name}'")

    def _tracked(self, name, fn):
        with self.server.running_lock:
            self.server.running.add(name or "*")
        try:
            return fn()
        finally:
            with self.server.running_lock:
                self.server.running.discard(name or "*")
