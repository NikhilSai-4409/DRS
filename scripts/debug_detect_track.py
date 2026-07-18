"""Detector-vs-tracker instrumentation for a single delivery clip.

Answers the only question that matters before touching tracker code: is the DETECTOR
failing to find the ball, or is the TRACKER rejecting a ball the detector found?

For every frame it runs the real BallDetector and the real SingleBallByteTracker in
lockstep and records, side by side:
  - every raw YOLO detection (centre, confidence)  -- unfiltered
  - which survive the pipeline's confidence_threshold and reach the tracker
  - what the tracker accepted / predicted / rejected, and the jump distance

Outputs: an annotated mp4 (mp4v), a per-frame CSV, sampled annotated JPEGs, and a
printed verdict. Usage:
    python scripts/debug_detect_track.py <clip.mp4> <out_dir> [--stride N] [--conf 0.25]
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np

from core.ball_association import SingleBallByteTracker
from core.ball_detector import BallDetector, DetectionResult


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("out_dir")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--conf", type=float, default=0.25, help="pipeline confidence_threshold")
    ap.add_argument("--save-every", type=int, default=12, help="save an annotated JPEG every N frames")
    args = ap.parse_args()

    out = Path(args.out_dir)
    (out / "frames").mkdir(parents=True, exist_ok=True)

    detector = BallDetector(model_path=None, export_results=False)
    cap = cv2.VideoCapture(args.clip)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    fps = src_fps / args.stride if args.stride > 1 else src_fps
    tracker = SingleBallByteTracker(fps=fps)

    writer = cv2.VideoWriter(str(out / "debug_video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), max(1.0, fps), (w, h))
    log_rows: list[dict] = []

    prev_trk: tuple[float, float] | None = None
    raw_centers: list[tuple[float, float] | None] = []
    trk_centers: list[tuple[float, float] | None] = []
    far_ball_rejected = 0  # frames where a detection sat >180px from the tracker

    frame_id = 0
    raw_index = -1
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        raw_index += 1
        if raw_index % args.stride != 0:
            continue
        ts = (raw_index / src_fps) * 1000.0

        raw = detector.detect(frame, frame_id, ts, 0, preprocess=False, imgsz=640).detections
        filtered = [d for d in raw if d.confidence >= args.conf]
        point = tracker.update(DetectionResult(frame_id, ts, 0, filtered, 0.0))

        raw_best = max(raw, key=lambda d: d.confidence) if raw else None
        raw_centers.append((raw_best.cx, raw_best.cy) if raw_best else None)
        trk = (point.x, point.y) if (point and point.real_detection) else None
        trk_centers.append(trk)

        # Is a real ball detection sitting far from where the tracker is stuck?
        jump = math.hypot(raw_best.cx - prev_trk[0], raw_best.cy - prev_trk[1]) if (raw_best and prev_trk) else 0.0
        if raw_best and prev_trk and jump > tracker.max_jump_px and raw_best.confidence < tracker.high_threshold:
            far_ball_rejected += 1
        if point and point.real_detection:
            prev_trk = (point.x, point.y)

        log_rows.append({
            "frame": frame_id, "n_raw": len(raw), "n_filtered": len(filtered),
            "raw_best_cx": raw_best.cx if raw_best else "", "raw_best_cy": raw_best.cy if raw_best else "",
            "raw_best_conf": round(raw_best.confidence, 3) if raw_best else "",
            "trk_x": round(point.x, 1) if point else "", "trk_y": round(point.y, 1) if point else "",
            "trk_state": ("real" if point and point.real_detection else "predicted" if point else "none"),
            "jump_from_trk": round(jump, 1), "rejected_jump": bool(point.rejected_jump) if point else False,
            "assoc": round(point.association_score, 3) if point else "",
        })

        annotated = _annotate(frame, raw, filtered, point, args.conf, frame_id, jump, tracker.max_jump_px)
        writer.write(annotated)
        if frame_id % args.save_every == 0:
            cv2.imwrite(str(out / "frames" / f"f{frame_id:04d}.jpg"),
                        cv2.resize(annotated, (w // 2, h // 2)))
        frame_id += 1

    cap.release()
    writer.release()
    with (out / "debug_log.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(log_rows[0].keys()))
        wr.writeheader()
        wr.writerows(log_rows)

    _verdict(log_rows, raw_centers, trk_centers, far_ball_rejected, w, tracker)


def _annotate(frame, raw, filtered, point, conf, fid, jump, max_jump):
    img = frame.copy()
    fset = {id(d) for d in filtered}
    for d in raw:
        reaches = id(d) in fset
        color = (0, 220, 255) if reaches else (60, 180, 255)  # cyan reaches tracker, orange filtered out
        cv2.rectangle(img, (d.x1, d.y1), (d.x2, d.y2), color, 2 if reaches else 1)
        cv2.putText(img, f"{d.confidence:.2f}", (d.x1, d.y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    if point:
        c = (int(point.x), int(point.y))
        col = (0, 255, 120) if point.real_detection else (0, 140, 255)
        cv2.circle(img, c, 12, col, 3)
        cv2.putText(img, "TRK" + ("" if point.real_detection else " (predict)"), (c[0] + 14, c[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
    txt = f"f{fid} raw={len(raw)} ->tracker={len(filtered)} (conf>={conf}) jump={jump:.0f}/{max_jump:.0f}"
    cv2.putText(img, txt, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    if point and point.rejected_jump:
        cv2.putText(img, "REJECTED far detection as jump", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    return img


def _motion(centers, ppm):
    pts = [c for c in centers if c]
    sp = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    if not sp:
        return 0.0, 0.0, 0.0
    sp.sort()
    return sp[len(sp) // 2], sp[int(len(sp) * 0.9)], sp[-1]


def _verdict(rows, raw_centers, trk_centers, far_ball_rejected, width, tracker):
    n = len(rows)
    with_raw = sum(1 for r in rows if r["n_raw"])
    with_filt = sum(1 for r in rows if r["n_filtered"])
    ppm = max(25.0, width / 20.12)
    raw_med, raw_p90, raw_max = _motion(raw_centers, ppm)
    trk_med, trk_p90, trk_max = _motion(trk_centers, ppm)
    to_kmh = lambda px_per_frame: (px_per_frame / ppm) * (tracker.fps) * 3.6
    print("\n================ DETECTOR vs TRACKER VERDICT ================")
    print(f"frames analysed:            {n}")
    print(f"frames with ANY raw YOLO:   {with_raw} ({100*with_raw/max(1,n):.0f}%)")
    print(f"frames reaching tracker:    {with_filt} ({100*with_filt/max(1,n):.0f}%)  (conf>=thresh)")
    print(f"raw best-detection motion:  median={raw_med:.1f}px  p90={raw_p90:.1f}px  max={raw_max:.1f}px"
          f"   (~{to_kmh(raw_p90):.0f} km/h at p90)")
    print(f"tracker accepted motion:    median={trk_med:.1f}px  p90={trk_p90:.1f}px  max={trk_max:.1f}px"
          f"   (~{to_kmh(trk_p90):.0f} km/h at p90)")
    print(f"frames w/ far det rejected:  {far_ball_rejected}  (detection >{tracker.max_jump_px:.0f}px from track, conf<{tracker.high_threshold})")
    print("------------------------------------------------------------")
    if raw_p90 < 15:
        print("LIKELY CASE 1 (DETECTOR): raw YOLO detections barely move -> the detector is")
        print("locking onto a near-static object; there is no fast ball in its output to track.")
    elif trk_p90 < 15 and raw_p90 >= 15:
        print("LIKELY CASE 2 (TRACKER): raw detections move fast but the tracker's accepted")
        print("path is static -> it seeded/locked on the wrong object and rejects the real ball.")
    else:
        print("LIKELY CASE 3 (BOTH): intermittent detection + jump rejection. Inspect frames.")
    print("============================================================")


if __name__ == "__main__":
    main()
