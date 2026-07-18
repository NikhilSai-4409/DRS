#!/usr/bin/env python
"""Does the detector EVER see the real, moving delivery ball?

The top-1 audit proved the SELECTED box is a static distractor on 28/28 clips. But it
only kept the highest-confidence ball per frame, so it cannot answer the question that
decides the whole fix strategy:

  * If a MOVING ball track exists somewhere in the detections (even low-confidence,
    even as a secondary box) -> the detector CAN see it; the bug is SELECTION, and a
    motion gate in association could fix it WITHOUT retraining.
  * If NO moving track exists -> the detector is blind to the in-play ball; in-domain
    retraining for RECALL is mandatory before anything else.

This logs ALL ball detections per frame, finds the persistent STATIC hotspots (the
distractors), strips them out, and searches the remaining "mobile" detections for a
short consistent moving streak (a delivery flight).

    python scripts/detect_moving_ball.py "E:/II innigs/CAM A/Vision Studio/Deliveries/1_MP4"
    python scripts/detect_moving_ball.py <dir> --stride 2 --conf 0.08 --limit 6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

HOTSPOT_RADIUS_FRAC = 0.025   # detections within this of a persistent point = same object
HOTSPOT_MIN_FRAMES = 0.20     # present in >=20% of frames -> it's a static distractor
LINK_JUMP_FRAC = 0.14         # max per-step move to link mobile detections (allows a fast ball)
MIN_STEP_FRAC = 0.006         # a linked step must move at least this (else it's static)
MIN_STREAK = 4                # frames in a row to count as a real moving track
MIN_NET_DISP_FRAC = 0.10      # a real flight travels at least this across the streak


def _ball_ids(names):
    ids = {i for i, n in names.items() if "ball" in str(n).lower()}
    return ids or {0}


def analyse(model, ball_ids, clip: Path, imgsz, conf, stride, max_frames):
    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        return {"file": clip.name, "error": "open failed"}
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    diag = (W * W + H * H) ** 0.5 or 1.0
    per_frame: list[list[tuple]] = []   # per processed frame: list of (conf, cx, cy)
    fid = -1; nproc = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        fid += 1
        if fid % stride != 0:
            continue
        if max_frames and nproc >= max_frames:
            break
        nproc += 1
        res = model.predict(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
        dets = []
        for b in res.boxes:
            if int(b.cls) in ball_ids:
                x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                dets.append((float(b.conf), (x1 + x2) // 2, (y1 + y2) // 2))
        per_frame.append(dets)
    cap.release()

    all_pts = [(fi, c, x, y) for fi, ds in enumerate(per_frame) for (c, x, y) in ds]
    if not all_pts:
        return {"file": clip.name, "processed": nproc, "detections": 0,
                "moving_ball_found": False, "verdict": "NO DETECTIONS"}

    # 1) find persistent static hotspots (distractors)
    hotspots: list[dict] = []
    for _, _, x, y in all_pts:
        for h in hotspots:
            if np.hypot(x - h["x"], y - h["y"]) < HOTSPOT_RADIUS_FRAC * diag:
                h["n"] += 1
                h["x"] = 0.9 * h["x"] + 0.1 * x
                h["y"] = 0.9 * h["y"] + 0.1 * y
                break
        else:
            hotspots.append({"x": float(x), "y": float(y), "n": 1})
    static = [h for h in hotspots if h["n"] >= HOTSPOT_MIN_FRAMES * nproc]

    def is_static(x, y):
        return any(np.hypot(x - h["x"], y - h["y"]) < HOTSPOT_RADIUS_FRAC * diag for h in static)

    # 2) strip static detections -> mobile candidates per frame
    mobile = [[(c, x, y) for (c, x, y) in ds if not is_static(x, y)] for ds in per_frame]
    n_mobile = sum(len(m) for m in mobile)

    # 3) greedily link mobile detections across consecutive frames into moving tracks
    best = {"len": 0, "net": 0.0, "start": None, "conf": 0.0}
    for f0 in range(len(mobile)):
        for (c0, x0, y0) in mobile[f0]:
            path = [(f0, x0, y0)]; cx, cy, cc = x0, y0, c0
            f = f0 + 1
            while f < len(mobile):
                cand = None
                for (c, x, y) in mobile[f]:
                    d = np.hypot(x - cx, y - cy)
                    if MIN_STEP_FRAC * diag <= d <= LINK_JUMP_FRAC * diag:
                        if cand is None or d < cand[0]:
                            cand = (d, x, y, c)
                if cand is None:
                    break
                _, x, y, c = cand
                path.append((f, x, y)); cx, cy, cc = x, y, c
                f += 1
            if len(path) >= 2:
                net = np.hypot(path[-1][1] - path[0][1], path[-1][2] - path[0][2]) / diag
                if len(path) > best["len"] or (len(path) == best["len"] and net > best["net"]):
                    best = {"len": len(path), "net": round(float(net), 3),
                            "start": path[0][0], "conf": round(cc, 2)}

    found = best["len"] >= MIN_STREAK and best["net"] >= MIN_NET_DISP_FRAC
    return {
        "file": clip.name, "processed": nproc, "detections": len(all_pts),
        "static_hotspots": [{"pos_frac": [round(h["x"] / W, 3), round(h["y"] / H, 3)],
                             "frames": int(h["n"])} for h in
                            sorted(static, key=lambda h: -h["n"])],
        "mobile_detections": n_mobile,
        "best_moving_streak_len": best["len"],
        "best_moving_streak_netdisp": best["net"],
        "moving_ball_found": bool(found),
        "verdict": "REAL BALL DETECTED (moving track exists)" if found
                   else "NO MOVING BALL TRACK (detector blind to in-play ball)",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips_dir")
    ap.add_argument("--model", default="models/production/best.pt")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.08)  # lower: catch faint real-ball boxes
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--out", default="analysis/detector_audit/ball_recall.json")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(args.model)
    ball_ids = _ball_ids(model.names)
    print(f"model={args.model} conf={args.conf} stride={args.stride} (lower conf to catch faint real ball)\n")

    clips = sorted(Path(args.clips_dir).glob("*.mp4"))
    if args.limit:
        clips = clips[: args.limit]

    rows = []
    print(f"{'clip':16}{'dets':>6}{'mobile':>7}{'streak':>7}{'netDisp':>8}  verdict")
    print("-" * 78)
    for clip in clips:
        r = analyse(model, ball_ids, clip, args.imgsz, args.conf, args.stride, args.max_frames)
        rows.append(r)
        if "error" in r:
            print(f"{clip.name:16} ERROR {r['error']}"); continue
        print(f"{r['file']:16}{r['detections']:6}{r['mobile_detections']:7}"
              f"{r['best_moving_streak_len']:7}{r['best_moving_streak_netdisp']:8.2f}  {r['verdict']}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, indent=2))
    found = [r for r in rows if r.get("moving_ball_found")]
    print("\n" + "=" * 78)
    print(f"SUMMARY: real moving-ball track found in {len(found)}/{len([r for r in rows if 'error' not in r])} clips")
    print(f"  clips WITH a moving ball : {[r['file'] for r in found]}")
    print("  -> if MANY: bug is SELECTION (add motion gate, maybe no retrain).")
    print("  -> if FEW/NONE: detector is blind to the in-play ball; retrain for RECALL first.")
    print(f"  wrote {args.out}")
    print("=" * 78)


if __name__ == "__main__":
    main()
