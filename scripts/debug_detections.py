#!/usr/bin/env python
"""Detector debug overlay for one delivery clip.

Writes a video with EVERY YOLO detection (box + class + confidence) drawn per frame,
plus the accumulated highest-confidence "ball" track, and prints a frame-by-frame log
of the selected ball detection. This is how you tell the failure mode apart —
detector miss vs. false-positive distractor vs. tracker picking the wrong box vs.
losing the ball after the bounce — in seconds, before investing in labeling/retraining.

    python scripts/debug_detections.py "path/to/Delivery009.mp4"
    python scripts/debug_detections.py <clip> --imgsz 1280 --conf 0.12 --out debug.mp4

Notes
-----
* imgsz matters: at a wide broadcast angle the ball is a few pixels; 640 downsamples
  it away. Try 1280.
* Reads the model from models/production/best.pt unless --model is given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="YOLO detection debug overlay for a delivery clip")
    ap.add_argument("clip")
    ap.add_argument("--model", default="models/production/best.pt")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.12)
    ap.add_argument("--out", default=None, help="output mp4 (default: <clip>_debug.mp4)")
    ap.add_argument("--max-frames", type=int, default=None)
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.model)
    names = model.names
    cap = cv2.VideoCapture(args.clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out_path = args.out or str(Path(args.clip).with_suffix("")) + "_debug.mp4"
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    colours = {0: (0, 255, 0), 1: (0, 180, 255), 2: (255, 120, 0), 3: (200, 0, 200)}

    track: list[tuple[int, int]] = []
    fid = 0
    detected = 0
    print(f"clip {W}x{H} fps={fps:.1f} model={args.model} imgsz={args.imgsz} conf={args.conf}\n")
    print("frame : selected ball  |  all detections")
    while True:
        ok, frame = cap.read()
        if not ok or (args.max_frames and fid >= args.max_frames):
            break
        res = model.predict(frame, imgsz=args.imgsz, conf=args.conf, verbose=False)[0]
        best = None
        alls = []
        for box in res.boxes:
            cls = int(box.cls)
            conf = float(box.conf)
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            alls.append(f"{names[cls]}:{conf:.2f}@({cx},{cy})")
            cv2.rectangle(frame, (x1, y1), (x2, y2), colours.get(cls, (255, 255, 255)), 2)
            cv2.putText(frame, f"{names[cls]} {conf:.2f}", (x1, y1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, colours.get(cls, (255, 255, 255)), 2)
            if cls == 0 and (best is None or conf > best[0]):
                best = (conf, cx, cy)
        if best:
            detected += 1
            track.append((best[1], best[2]))
        for i in range(1, len(track)):
            cv2.line(frame, track[i - 1], track[i], (0, 255, 255), 2)
        cv2.putText(frame, f"frame {fid}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        writer.write(frame)
        sel = f"ball {best[0]:.2f} @({best[1]},{best[2]})" if best else "(none)"
        print(f"{fid:5} : {sel:22} | {'  '.join(alls) if alls else '(none)'}")
        fid += 1
    cap.release()
    writer.release()
    print(f"\nball detected in {detected}/{fid} frames ({100 * detected / max(1, fid):.1f}%)")
    print(f"debug video: {out_path}")


if __name__ == "__main__":
    main()
