#!/usr/bin/env python3
"""Serve the collector's JSON at /host.json and nothing else.

Runs in its own container with no Docker socket and no host mounts.
STATS_FILE (default /shared/host.json) and STATS_PORT (default 8766) can be overridden.
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SOURCE = Path(os.environ.get("STATS_FILE", "/shared/host.json"))
PORT = int(os.environ.get("STATS_PORT", "8766"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path != "/host.json":
                raise FileNotFoundError
            body = SOURCE.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
