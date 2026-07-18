#!/usr/bin/env python
"""Locate each delivery's flight window by the impact motion spike.

A fixed fraction-of-clip window does not generalise: clips vary from ~190 to ~330
frames and the delivery sits at different relative positions. But one cue is reliable
across all of them -- the batsman is still until the ball arrives, then swings. So the
peak frame-to-frame motion inside the batsman/stumps region marks impact. We window
from impact-BEFORE (release + flight) to impact+AFTER (follow-through / keeper take).

The bowler's run-up is excluded because the ROI is the upper (far) third only.

    python scripts/find_flight_window.py --dir ".../1_MP4"            # print windows for all
    python scripts/find_flight_window.py --clip ".../Delivery014.mp4" --debug
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

# batsman/stumps ROI as fractions of frame (far end, upper-centre) -- excludes run-up
ROI = (0.30, 0.08, 0.58, 0.40)   # x0, y0, x1, y1
BEFORE, AFTER = 30, 12           # frames to keep before/after the impact peak


def find_impact(clip: Path):
    cap = cv2.VideoCapture(str(clip))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    x0, y0, x1, y1 = int(ROI[0]*W), int(ROI[1]*H), int(ROI[2]*W), int(ROI[3]*H)
    prev = None; motion = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(f[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (5, 5), 0)
        motion.append(0.0 if prev is None else float(np.abs(g.astype(int) - prev).mean()))
        prev = g
    cap.release()
    m = np.array(motion)
    if len(m) < 5:
        return None
    k = np.ones(5) / 5                       # smooth
    ms = np.convolve(m, k, mode="same")
    impact = int(ms.argmax())
    f0 = max(0, impact - BEFORE)
    f1 = min(n - 1, impact + AFTER)
    return {"frames": n, "impact": impact, "impact_frac": round(impact / n, 3),
            "window": [f0, f1], "window_frac": [round(f0/n, 3), round(f1/n, 3)],
            "peak_motion": round(float(ms.max()), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip"); ap.add_argument("--dir")
    args = ap.parse_args()
    clips = [Path(args.clip)] if args.clip else sorted(Path(args.dir).glob("*.mp4"))
    print(f"{'clip':16}{'frames':>7}{'impact':>7}{'i_frac':>7}  window (frames / frac)")
    print("-" * 70)
    for c in clips:
        r = find_impact(c)
        if not r:
            print(f"{c.name:16} (unreadable)"); continue
        print(f"{c.name:16}{r['frames']:7}{r['impact']:7}{r['impact_frac']:7.2f}  "
              f"{r['window']}  {r['window_frac']}")


if __name__ == "__main__":
    main()
