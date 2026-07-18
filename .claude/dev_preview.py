"""Dev preview launcher for the Cricket DRS Review Workstation.

Starts the unified FastAPI backend on 127.0.0.1:8765 (reusing one if already
listening) and serves the canonical Electron renderer (dashboard/electron/) as a
plain web app on 127.0.0.1:8793 — the renderer's window.drs bridge is optional, so
every page works in a normal browser against the live backend.

Used by .claude/launch.json ("drs-dashboard"). Synthetic camera fallback is enabled
so the dashboard has live feeds even with no cameras attached; a real webcam at
index 0 is still used when present.
"""

from __future__ import annotations

import http.server
import os
import socket
import socketserver
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # F:\DRS
WEB_ROOT = ROOT / "dashboard" / "electron"
BACKEND_PORT = 8765
STATIC_PORT = 8793


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, *args):  # keep the preview console quiet
        pass

    def end_headers(self):
        # ES modules cache hard; a dev preview must always serve current code.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        super().end_headers()

    def do_GET(self):
        if self.path in ("", "/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/renderer/index.html")
            self.end_headers()
            return
        super().do_GET()


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    backend = None
    if port_in_use(BACKEND_PORT):
        print(f"[dev-preview] backend already listening on {BACKEND_PORT} — reusing it")
    else:
        env = {**os.environ, "DRS_SYNTHETIC_CAMERAS": "1"}
        backend = subprocess.Popen(
            [sys.executable, str(ROOT / "drs_app.py"), "--api",
             "--cameras", "0,1", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
            cwd=str(ROOT), env=env,
        )
        print(f"[dev-preview] backend starting on 127.0.0.1:{BACKEND_PORT} (pid {backend.pid})")

    print(f"[dev-preview] dashboard at http://127.0.0.1:{STATIC_PORT}/renderer/index.html")
    try:
        with ThreadingServer(("127.0.0.1", STATIC_PORT), Handler) as httpd:
            httpd.serve_forever()
    finally:
        if backend is not None:
            backend.terminate()


if __name__ == "__main__":
    main()
