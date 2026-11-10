"""Web dashboard: a single page plus a small JSON API, served from a background thread."""
import base64
import hmac
import json
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as config_module
from .bom import BomClient, state_of

log = logging.getLogger(__name__)
PAGE = Path(__file__).resolve().parent / "web" / "index.html"
ACTIONS = {"next", "pause", "resume", "jump", "refresh_weather", "init", "test", "reboot", "reset_settings"}


def make_handler(dashboard, settings, password):
    class Handler(BaseHTTPRequestHandler):
        server_version = "smalltv"

        def log_message(self, fmt, *args):
            log.debug("web %s", fmt % args)

        # ---- helpers ----

        def _authorised(self):
            if not password:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                    if hmac.compare_digest(given, password):
                        return True
                except ValueError:
                    pass
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SmallTV"')
            self.end_headers()
            return False

        def _send(self, code, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self):
            # Requiring JSON blocks cross-site form posts from other pages.
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("expected application/json")
            length = int(self.headers.get("Content-Length", 0))
            if length > 65536:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def _state(self, config=None):
            # config: freshly saved settings the loop has not applied yet
            return {"status": dashboard.snapshot(), "settings": config_module.editable(config or dashboard.config),
                    "screens_available": list(config_module.SCREENS), "themes": list(config_module.THEMES),
                    "hosts": [h.name for h in dashboard.config.hosts]}

        # ---- routes ----

        def do_GET(self):
            if not self._authorised():
                return
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                self._send(200, self._state())
            elif url.path == "/api/preview.png":
                png = dashboard.preview_png
                self._send(200, png, "image/png") if png else self._send(404, {"error": "nothing shown yet"})
            elif url.path == "/api/locations":
                query = urllib.parse.parse_qs(url.query).get("q", [""])[0].strip()
                if len(query) < 2:
                    return self._send(200, [])
                try:
                    found = BomClient(config_module.Weather(location=query), "UTC").search(query, limit=8)
                    self._send(200, [{"name": p.get("name", ""), "state": state_of(p), "id": p.get("id", ""),
                                      "type": p.get("type", "")} for p in found if p.get("type") == "place"])
                except Exception as error:
                    self._send(502, {"error": f"BOM search failed: {error}"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorised():
                return
            try:
                body = self._json_body()
                if self.path == "/api/settings":
                    new_config = settings.update(body)
                    dashboard.request(config=new_config)
                    return self._send(200, self._state(new_config))
                if self.path == "/api/action":
                    action = body.get("action")
                    if action not in ACTIONS:
                        raise ValueError(f"unknown action {action!r}")
                    if action == "jump":
                        dashboard.request(jump=str(body.get("screen", "")))
                    elif action == "next":
                        dashboard.request(next=True)
                    elif action in ("pause", "resume"):
                        dashboard.request(pause=action == "pause")
                        dashboard.paused = action == "pause"
                    elif action == "refresh_weather":
                        dashboard.request(refresh_weather=True)
                    elif action == "reset_settings":
                        new_config = settings.reset()
                        dashboard.request(config=new_config)
                        return self._send(200, self._state(new_config))
                    else:
                        dashboard.request(device=action)
                    return self._send(200, self._state())
                self._send(404, {"error": "not found"})
            except (ValueError, json.JSONDecodeError) as error:
                self._send(400, {"error": str(error)})

    return Handler


def start(dashboard, settings, web_config):
    server = ThreadingHTTPServer((web_config.bind, web_config.port),
                                 make_handler(dashboard, settings, web_config.password))
    thread = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    thread.start()
    log.info("dashboard on http://%s:%s%s", web_config.bind, web_config.port,
             " (password protected)" if web_config.password else "")
    return server
