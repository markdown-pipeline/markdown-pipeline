"""A stand-in for the public GitHub releases pages, for checking the installer without publishing (MD-131).

    python3 scripts/local_releases_server.py DIR TAG        # serves  /releases/latest -> /releases/tag/TAG  and  /releases/download/TAG/<file from DIR>

Prints the base URL (http://127.0.0.1:PORT) on the first line, then serves until it is stopped. Standard library only; localhost only.
"""
import http.server
import sys
from pathlib import Path

directory, tag = Path(sys.argv[1]).resolve(), sys.argv[2]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET(body=False)

    def do_GET(self, body=True):
        if self.path == "/releases/latest":
            self.send_response(302)
            self.send_header("Location", f"/releases/tag/{tag}")
            self.end_headers()
        elif self.path == f"/releases/tag/{tag}":
            self.send_response(200)
            self.end_headers()
            body and self.wfile.write(b"release page")
        elif self.path.startswith(f"/releases/download/{tag}/") and (directory / self.path.rsplit("/", 1)[1]).is_file():
            data = (directory / self.path.rsplit("/", 1)[1]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            body and self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)
server.serve_forever()
