#!/usr/bin/env python
"""Batch detector + trajectory audit across a directory of delivery clips.

Runs the production ball detector over every clip and quantifies BOTH:

  A. detector behaviour  -- detection rate, confidence, longest continuous track,
                            fragmentation, false-track pressure, and whether the
                            selected box MOVES like a ball or sits STATIC like a
                            distractor (clustered across clips by pixel region).

  B. trajectory physics  -- cheap, ML-free sanity checks on the longest continuous
                            track: speed (px/s), direction reversals, vertical
                            reversals (bounce proxy), identity-switch jumps, and a
                            quadratic-fit smoothness score -> VALID / FALSE TRACK.

Detection rate only says "something was classified as a ball." The physics checks are
what separate "a real delivery arc" from "a confident box hopping between distractors"
without any labelling. km/h is deliberately NOT reported: it needs the pitch homography
(pixel->metre), an unvalidated stage -- speed is in px/s until that is trusted.

    python scripts/audit_detector_batch.py "E:/II innigs/CAM A/Vision Studio/Deliveries/1_MP4"
    python scripts/audit_detector_batch.py <dir> --stride 2 --imgsz 1280 --conf 0.12 --limit 3

Reads models/production/best.pt unless --model is given, and logs exactly which
checkpoint (+ its best.json sidecar) it loaded, so a stale-checkpoint bug shows up in
the header rather than after hours of debugging.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# A step whose centre moves less than this fraction of the frame diagonal counts as
# "static" (candidate distractor, not a flying ball).
STATIC_STEP_FRAC = 0.004
# A jump larger than this fraction of the diagonal between consecutive in-track frames
# is treated as an identity switch (the selected box teleported to another object).
SWITCH_JUMP_FRAC = 0.08
# Per-clip static clusters within this fraction of the diagonal are the same object.
CLUSTER_TOL_FRAC = 0.03


def _ball_class_ids(names: dict) -> set[int]:
    ids = {i for i, n in names.items() if "ball" in str(n).lower()}
    return ids or {0}  # single-class custom models label class 0 = ball


def _fragments(track: list[tuple], stride: int) -> list[list[tuple]]:
    """Split the selected-ball track into gap-separated continuous runs."""
    runs: list[list[tuple]] = []
    cur: list[tuple] = []
    for i, pt in enumerate(track):
        if not cur or (pt[0] - cur[-1][0]) <= stride:
            cur.append(pt)
        else:
            runs.append(cur)
            cur = [pt]
    if cur:
        runs.append(cur)
    return runs


def _physics(frag: list[tuple], fps: float, stride: int, W: int, H: int, diag: float) -> dict:
    """ML-free sanity checks on one continuous track fragment."""
    if len(frag) < 4:
        return {"status": "TOO SHORT", "frames": len(frag)}
    fids = [p[0] for p in frag]
    xs = np.array([p[2] for p in frag], dtype=float)
    ys = np.array([p[3] for p in frag], dtype=float)
    dt = stride / fps  # seconds between consecutive in-track samples

    vx = np.diff(xs)
    vy = np.diff(ys)
    step = np.hypot(vx, vy)
    speeds_pps = step / dt                      # pixels per second
    moving = step > STATIC_STEP_FRAC * diag
    moving_ratio = float(moving.mean())

    # direction reversals: angle between consecutive velocity vectors > 120 deg
    reversals = 0
    for i in range(1, len(vx)):
        a = np.array([vx[i - 1], vy[i - 1]])
        b = np.array([vx[i], vy[i]])
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na < 1 or nb < 1:
            continue
        cos = float(np.clip(a.dot(b) / (na * nb), -1, 1))
        if math.degrees(math.acos(cos)) > 120:
            reversals += 1

    # vertical reversals ~ bounce proxy (down->up transitions in image y)
    bounce = int(((vy[:-1] > 1) & (vy[1:] < -1)).sum())
    # identity-switch jumps within a "continuous" run
    switches = int((step > SWITCH_JUMP_FRAC * diag).sum())

    # smoothness: residual of quadratic fit x(t), y(t) vs frame index, normalised
    t = np.array(fids, dtype=float)
    try:
        rx = ys * 0  # placeholder to keep names; recompute below
        px = np.polyfit(t, xs, 2)
        py = np.polyfit(t, ys, 2)
        res = np.hypot(np.polyval(px, t) - xs, np.polyval(py, t) - ys)
        rms = float(res.mean())
    except Exception:
        rms = diag
    smoothness = max(0.0, min(1.0, 1 - rms / (0.03 * diag)))

    moving_speeds = speeds_pps[moving]
    phys = {
        "frames": len(frag),
        "max_speed_pps": round(float(speeds_pps.max()), 1),
        "mean_speed_pps": round(float(moving_speeds.mean()) if moving_speeds.size else 0.0, 1),
        "moving_ratio": round(moving_ratio, 2),
        "direction_reversals": reversals,
        "bounce_proxy": bounce,
        "switch_jumps": switches,
        "smoothness": round(smoothness, 2),
    }
    # verdict: a real delivery is a smooth, mostly-moving arc with few reversals/switches
    if moving_ratio < 0.25:
        phys["status"] = "FALSE TRACK (static/distractor)"
    elif reversals > 4 or switches > 2 or smoothness < 0.5:
        phys["status"] = "LIKELY FALSE TRACK (erratic)"
    elif moving_ratio > 0.5 and reversals <= 3 and smoothness >= 0.6:
        phys["status"] = "PLAUSIBLE"
    else:
        phys["status"] = "AMBIGUOUS"
    return phys


def analyse_clip(model, names, ball_ids, clip: Path, imgsz: int, conf: float,
                 stride: int, max_frames: int | None) -> dict:
    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        return {"file": clip.name, "error": "could not open"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    diag = (W * W + H * H) ** 0.5 or 1.0

    track: list[tuple[int, float, int, int]] = []
    frames_processed = 0
    frames_with_det = 0
    extra_ball_boxes = 0
    t0 = time.time()
    fid = -1
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        fid += 1
        if fid % stride != 0:
            continue
        if max_frames and frames_processed >= max_frames:
            break
        frames_processed += 1
        res = model.predict(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
        best = None
        ball_here = 0
        for box in res.boxes:
            if int(box.cls) not in ball_ids:
                continue
            ball_here += 1
            c = float(box.conf)
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            if best is None or c > best[0]:
                best = (c, cx, cy)
        if best:
            frames_with_det += 1
            extra_ball_boxes += ball_here - 1
            track.append((fid, best[0], best[1], best[2]))
    cap.release()
    elapsed = time.time() - t0

    row: dict = {
        "file": clip.name, "w": W, "h": H, "fps": round(fps, 1),
        "frames_processed": frames_processed,
        "frames_with_det": frames_with_det,
        "detection_rate": round(frames_with_det / max(1, frames_processed), 3),
        "avg_extra_ball_boxes": round(extra_ball_boxes / max(1, frames_with_det), 2),
        "sec": round(elapsed, 1),
        "track": track,  # raw points so downstream analysis never re-runs inference
    }
    if not track:
        row.update({"detected": False, "verdict": "NO DETECTIONS",
                    "confidence_mean": None, "physics": {"status": "NO TRACK"}})
        return row

    confs = [t[1] for t in track]
    xs = [t[2] for t in track]
    ys = [t[3] for t in track]
    runs = _fragments(track, stride)
    longest_frag = max(runs, key=len)
    static_steps = tot_steps = 0
    for frag in runs:
        for i in range(1, len(frag)):
            s = ((frag[i][2] - frag[i - 1][2]) ** 2 + (frag[i][3] - frag[i - 1][3]) ** 2) ** 0.5
            tot_steps += 1
            if s < STATIC_STEP_FRAC * diag:
                static_steps += 1
    static_ratio = static_steps / max(1, tot_steps)
    med_cx, med_cy = int(st.median(xs)), int(st.median(ys))
    phys = _physics(longest_frag, fps, stride, W, H, diag)

    # detector-level verdict (is it a ball or a distractor)
    if row["detection_rate"] < 0.1:
        verdict = "DETECTOR MISS"
    elif static_ratio > 0.7 and phys.get("status", "").startswith(("FALSE", "LIKELY")):
        verdict = "STATIC -> DISTRACTOR"
    elif phys.get("status") == "PLAUSIBLE":
        verdict = "MOVES like a ball"
    else:
        verdict = "AMBIGUOUS"

    row.update({
        "detected": True,
        "confidence_mean": round(st.mean(confs), 3),
        "confidence_median": round(st.median(confs), 3),
        "longest_continuous": len(longest_frag),
        "fragments": len(runs),
        "x_range_frac": round((max(xs) - min(xs)) / W, 3),
        "y_range_frac": round((max(ys) - min(ys)) / H, 3),
        "static_ratio": round(static_ratio, 3),
        "median_pos_frac": [round(med_cx / W, 3), round(med_cy / H, 3)],
        "physics": phys,
        "verdict": verdict,
    })
    return row


def find_recurring_distractor(rows: list[dict]) -> dict:
    pts = [(r["file"], r["median_pos_frac"]) for r in rows
           if r.get("verdict", "").startswith("STATIC") and r.get("median_pos_frac")]
    clusters: list[dict] = []
    for name, (fx, fy) in pts:
        for cl in clusters:
            if math.hypot(fx - cl["sum_x"] / cl["n"], fy - cl["sum_y"] / cl["n"]) < CLUSTER_TOL_FRAC:
                cl["n"] += 1; cl["sum_x"] += fx; cl["sum_y"] += fy; cl["files"].append(name)
                break
        else:
            clusters.append({"n": 1, "sum_x": fx, "sum_y": fy, "files": [name]})
    clusters.sort(key=lambda c: c["n"], reverse=True)
    return {"static_clips": len(pts), "clusters": [
        {"center_frac": [round(c["sum_x"] / c["n"], 3), round(c["sum_y"] / c["n"], 3)],
         "count": c["n"], "files": c["files"]} for c in clusters]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch YOLO detector + trajectory audit")
    ap.add_argument("clips_dir")
    ap.add_argument("--model", default="models/production/best.pt")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.12)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--out", default="analysis/detector_audit")
    args = ap.parse_args()

    from ultralytics import YOLO

    model_path = Path(args.model)
    meta = {}
    if model_path.with_suffix(".json").exists():
        meta = json.loads(model_path.with_suffix(".json").read_text())
    print("=" * 76)
    print(f"MODEL   : {model_path}  ({model_path.stat().st_size/1e6:.1f} MB)")
    if meta:
        print(f"  name={meta.get('model_name')} dataset={meta.get('dataset')} "
              f"imgsz={meta.get('image_size')} mAP50={meta.get('mAP50')}")
    print(f"INFER   : imgsz={args.imgsz} conf={args.conf} stride={args.stride}")
    print("=" * 76)

    model = YOLO(str(model_path))
    names = model.names
    ball_ids = _ball_class_ids(names)
    print(f"classes {names} -> ball ids {sorted(ball_ids)}\n")

    clips = sorted(Path(args.clips_dir).glob("*.mp4"))
    if args.limit:
        clips = clips[: args.limit]

    rows = []
    hdr = (f"{'clip':16}{'det?':>5}{'det%':>6}{'conf':>6}{'track':>6}{'switch':>7}"
           f"{'rev':>4}{'smooth':>7}  {'PHYSICS':22} DETECTOR")
    print(hdr); print("-" * len(hdr))
    for clip in clips:
        r = analyse_clip(model, names, ball_ids, clip, args.imgsz, args.conf,
                         args.stride, args.max_frames)
        rows.append(r)
        if "error" in r:
            print(f"{clip.name:16} ERROR {r['error']}"); continue
        p = r.get("physics", {})
        det = "Y" if r.get("detected") else "n"
        print(f"{r['file']:16}{det:>5}{r['detection_rate']*100:5.0f}%"
              f"{(r.get('confidence_mean') or 0):6.2f}{r.get('longest_continuous',0):6}"
              f"{p.get('switch_jumps',0):7}{p.get('direction_reversals',0):4}"
              f"{p.get('smoothness',0):7.2f}  {p.get('status','-'):22} {r.get('verdict','-')}")

    recurring = find_recurring_distractor(rows)
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit.json").write_text(json.dumps(
        {"model": str(model_path), "model_meta": meta,
         "params": {"imgsz": args.imgsz, "conf": args.conf, "stride": args.stride},
         "clips_dir": args.clips_dir, "clips": rows, "recurring_distractor": recurring},
        indent=2))

    ok = [r for r in rows if "error" not in r]
    detected = [r for r in ok if r.get("detected")]
    plausible = [r for r in ok if r.get("physics", {}).get("status") == "PLAUSIBLE"]
    false_tracks = [r for r in ok if "FALSE" in r.get("physics", {}).get("status", "")]
    missed = [r for r in ok if not r.get("detected") or r["detection_rate"] < 0.1]
    print("\n" + "=" * 76)
    print("SUMMARY")
    print(f"  deliveries              : {len(ok)}")
    print(f"  ball detected (any box) : {len(detected)}/{len(ok)}")
    print(f"  physics PLAUSIBLE       : {len(plausible)}/{len(ok)}   {[r['file'] for r in plausible]}")
    print(f"  physics FALSE TRACK     : {len(false_tracks)}/{len(ok)}")
    print(f"  detector missed         : {len(missed)}/{len(ok)}   {[r['file'] for r in missed]}")
    if ok:
        print(f"  avg detection rate      : {st.mean(r['detection_rate'] for r in ok)*100:.0f}%")
        print(f"  avg confidence          : {st.mean((r.get('confidence_mean') or 0) for r in detected):.2f}")
        print(f"  longest continuous track: {max(r.get('longest_continuous',0) for r in ok)} frames")
    print(f"\n  recurring distractor clusters:")
    for c in recurring["clusters"]:
        print(f"    at {c['center_frac']}  x{c['count']}  {c['files'][:6]}{'...' if len(c['files'])>6 else ''}")
    print(f"\n  wrote {out_dir/'audit.json'}")
    print("=" * 76)


if __name__ == "__main__":
    main()
