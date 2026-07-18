"""Replay 2 — 'DRS REVIEW' clean broadcast replay (Testing page: DRS REVIEW section).

Renders the reconstructed delivery in the Three.js broadcast scene (dashboard/replay_assets)
via headless Chrome + SwiftShader, then assembles the mp4 with the broadcast post chain and
crisp HUD (cards drawn AFTER post, like TV). Renderer-only: consumes the reconstruction
payload verbatim.

Headless Chrome is an optional dependency: when unavailable this returns None and the job
simply ships without the DRS-review video (Replay 1 + results are unaffected).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.replay_hud import draw_hud

ASSETS = Path(__file__).resolve().parent.parent / "dashboard" / "replay_assets"
CHROME_CANDIDATES = [
    os.environ.get("DRS_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
MIME = {".html": "text/html", ".js": "application/javascript", ".json": "application/json"}
FPS, N_FRAMES, BANNER_AT_MS = 25, 132, 3500

# broadcast post chain (bloom -> softness -> real JPEG artifacts -> grain); grade is identity
_P = dict(bloom_thr=215, bloom_w1=0.25, bloom_s1=4, bloom_w2=0.12, bloom_s2=14,
          soften=0.7, jpeg_q=72, grain=1.6)


def _chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def _post(img: np.ndarray) -> np.ndarray:
    out = img.astype(np.float32)
    lum = out.mean(2)
    bright = np.clip((lum - _P["bloom_thr"]) / (255 - _P["bloom_thr"]), 0, 1)[..., None]
    src = out * bright
    out = np.clip(out + cv2.GaussianBlur(src, (0, 0), _P["bloom_s1"]) * _P["bloom_w1"]
                  + cv2.GaussianBlur(src, (0, 0), _P["bloom_s2"]) * _P["bloom_w2"], 0, 255)
    out = cv2.GaussianBlur(out, (0, 0), _P["soften"])
    ok, enc = cv2.imencode(".jpg", out.astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, _P["jpeg_q"]])
    out = cv2.imdecode(enc, cv2.IMREAD_COLOR).astype(np.float32)
    rng = np.random.RandomState(7)
    return np.clip(out + rng.normal(0, _P["grain"], out.shape[:2])[..., None], 0, 255).astype(np.uint8)


def generate_drs_review_replay(reconstruction: dict[str, Any], out_path: str | Path) -> Path | None:
    chrome = _chrome()
    if chrome is None or not (ASSETS / "replay_review.html").exists():
        return None
    with tempfile.TemporaryDirectory(prefix="drs_replay_") as td:
        tdir = Path(td)
        for name in ("replay_review.html", "broadcast_scene.js", "three.module.min.js", "three.core.min.js"):
            shutil.copy(ASSETS / name, tdir / name)
        shutil.copytree(ASSETS / "broadcast_style_v1", tdir / "broadcast_style_v1")
        (tdir / "delivery3d.json").write_text(json.dumps(reconstruction))
        frames_dir = tdir / "anim_frames"
        frames_dir.mkdir()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: N802
                pass

            def do_GET(self):  # noqa: N802
                p = tdir / self.path.split("?")[0].lstrip("/")
                if p.is_file():
                    self.send_response(200)
                    self.send_header("Content-Type", MIME.get(p.suffix, "application/octet-stream"))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(p.read_bytes())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):  # noqa: N802
                m = re.search(r"/frame\?i=(\d+)", self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if m:
                    (frames_dir / ("frame_%03d.png" % int(m.group(1)))).write_bytes(body)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            subprocess.run(
                [chrome, "--headless=new", "--no-sandbox", "--enable-unsafe-swiftshader",
                 "--hide-scrollbars", "--window-size=1280,720", "--virtual-time-budget=320000",
                 "--dump-dom", f"http://127.0.0.1:{port}/replay_review.html#nohud"],
                capture_output=True, timeout=420,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        finally:
            srv.shutdown()
        frames = sorted(frames_dir.glob("frame_*.png"))
        if len(frames) < N_FRAMES - 2:
            return None
        out_path = Path(out_path)
        # H.264 writer (avc1) — mp4v does NOT decode in Chromium (black <video> player)
        from core.testing_pipeline import _open_video_writer

        vw = _open_video_writer(out_path, FPS, (1280, 720))
        cards, tag = reconstruction["cards"], reconstruction["meta"]["tag"]
        for i, f in enumerate(frames):
            img = _post(cv2.imread(str(f)))
            img = draw_hud(img, i * (1000 // FPS), cards, tag, banner_at=BANNER_AT_MS)
            vw.write(img)
        vw.release()
        return out_path if out_path.exists() else None
