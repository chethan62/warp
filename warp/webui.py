"""Local HTTP API and UI host.

One page, one big toggle — the 1.1.1.1 shape. The UI is plain HTML/CSS/JS served
from disk, so there is no build step and no node_modules.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import __version__, cloudflare, paths, window
from .tunnel import Tunnel


def make_handler(tunnel: Tunnel):
    class Handler(BaseHTTPRequestHandler):
        server_version = "warp"

        def log_message(self, *args):  # keep the console clean
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                try:
                    html = paths.ui_path().read_bytes()
                except OSError as exc:
                    return self._send(500, f"UI missing: {exc}".encode(), "text/plain")
                return self._send(200, html, "text/html; charset=utf-8")
            if route == "/api/status":
                return self._json({**tunnel.status(), "version": __version__})
            return self._json({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            route = urlparse(self.path).path
            if route == "/api/connect":
                return self._json(tunnel.start())
            if route == "/api/disconnect":
                return self._json(tunnel.stop())
            if route == "/api/reset":
                tunnel.stop()
                cloudflare.reset()
                return self._json({"ok": True, "note": "account cleared; next connect re-registers"})
            if route == "/api/system-proxy/on":
                return self._json(tunnel.route_system(True))
            if route == "/api/system-proxy/off":
                return self._json(tunnel.route_system(False))
            return self._json({"error": "not found"}, 404)

    return Handler


def serve(port: int = 8787, host: str = "127.0.0.1", open_browser: bool = True,
          prefer_tab: bool = False) -> None:
    tunnel = Tunnel()
    httpd = ThreadingHTTPServer((host, port), make_handler(tunnel))
    url = f"http://{host}:{port}/"
    print(f"warp ready — {url}")
    if open_browser:
        # its own window by default; a tab only if asked for or nothing else works
        opened = window.open_ui(url, profile_dir=paths.config_dir() / "browser",
                                prefer_tab=prefer_tab)
        if opened == "none":
            print("no browser found — open the URL above by hand")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        httpd.server_close()
