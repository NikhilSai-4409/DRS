"""Replay 1 — 'with players' (Testing page: Observed Trajectory section).

The REAL footage (batsman, bat, wickets visible) with the broadcast overlay drawn on it:
the smoothed tracked path draws in progressively (image space — no calibration needed),
pitching marker at the real bounce, impact ring, decision cards, verdict banner.
Renderer-only: every value comes from the reconstruction payload (core/replay_reconstruction).
Pure cv2 + PIL — no new dependencies.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.replay_hud import draw_hud

FPS_OUT = 25
FRAME_STEP = 2          # subsample source frames -> broadcast pace
HOLD_FRAMES = 45        # ~1.8s hold after impact


def generate_replay_with_players(
    video_path: str | Path,
    reconstruction: dict[str, Any],
    out_path: str | Path,
) -> Path | None:
    track = {int(round(f)): (x, y) for f, x, y in reconstruction["image_fit"]}
    frames_with_ball = sorted(track.keys())
    if len(frames_with_ball) < 8:
        return None
    f_start, f_impact = frames_with_ball[0], frames_with_ball[-1]
    impact_px = reconstruction["impact_px"]
    cards = reconstruction["cards"]
    bounce_px = reconstruction.get("bounce_px")
    bounce_frame = reconstruction.get("bounce_frame")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    W, H = int(cap.get(3)), int(cap.get(4))
    out_path = Path(out_path)
    # H.264 writer (avc1) — mp4v does NOT decode in Chromium: the Testing page <video>
    # renders it as a black player (root-caused 2026-07-17)
    from core.testing_pipeline import _open_video_writer

    vw = _open_video_writer(out_path, FPS_OUT, (W, H))
    seq = list(range(f_start, f_impact + 1, FRAME_STEP)) + [f_impact] * HOLD_FRAMES
    travel_n = max(1, (f_impact - f_start) // FRAME_STEP)
    tag = "REPLAY 1/2 with players | REAL footage + tracked overlay | " + Path(video_path).name

    def path_overlay(img: np.ndarray, upto_frame: int) -> np.ndarray:
        lay = np.zeros_like(img)
        seen = [track[f] for f in frames_with_ball if f <= upto_frame]
        if len(seen) >= 2:
            arr = np.array(seen, np.int32)
            for i in range(1, len(arr)):
                cv2.line(lay, tuple(arr[i - 1]), tuple(arr[i]), (58, 38, 208), 8, cv2.LINE_AA)
            halo = cv2.GaussianBlur(lay, (0, 0), 5) * 0.8
            lay = np.clip(halo + lay.astype(float), 0, 255).astype(np.uint8)
            cx_, cy_ = map(int, seen[-1])
            cv2.circle(lay, (cx_, cy_), 8, (120, 120, 255), -1, cv2.LINE_AA)
            cv2.circle(lay, (cx_, cy_), 11, (255, 255, 255), 2, cv2.LINE_AA)
        return np.clip(img.astype(float) + lay.astype(float) * 0.5, 0, 255).astype(np.uint8)

    for k, f in enumerate(seq):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        if not ok:
            break
        img = path_overlay(img, f)
        prog = min(1.0, k / travel_n)
        after = max(0, k - travel_n)
        if bounce_px is not None and bounce_frame is not None and f >= bounce_frame:
            bx, by = map(int, bounce_px)
            cv2.ellipse(img, (bx, by), (16, 7), 0, 0, 360, (30, 20, 10), -1, cv2.LINE_AA)
            cv2.ellipse(img, (bx, by), (16, 7), 0, 0, 360, (255, 255, 255), 2, cv2.LINE_AA)
        if prog >= 1.0:
            rad = 18 + int(6 * np.sin(after * 0.5))
            cv2.circle(img, tuple(map(int, impact_px)), rad, (80, 80, 255), 3, cv2.LINE_AA)
            cv2.circle(img, tuple(map(int, impact_px)), 4, (255, 255, 255), -1, cv2.LINE_AA)
        img = draw_hud(img, k * (1000 // FPS_OUT), cards, tag,
                       banner_at=(travel_n + 10) * (1000 // FPS_OUT), scale=1.0)
        vw.write(img)
    vw.release()
    cap.release()
    return out_path if out_path.exists() else None
