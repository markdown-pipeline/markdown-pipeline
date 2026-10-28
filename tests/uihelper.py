"""Start the real UI server on an ephemeral port and talk to it over HTTP."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

from mdpipeline.ui_server import make_server


class Client:
    def __init__(self, server) -> None:
        self.server = server
        self.base = server.url

    def request(self, method: str, path: str, body=None, token: bool | str = True, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-MDP-Token", self.server.token if token is True else token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw.decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw.decode()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body if body is not None else {}, **kw)


@contextmanager
def running_ui(brain, **kw):
    server = make_server(brain, port=0, **kw)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
