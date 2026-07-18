#!/usr/bin/env python
"""Extract delivery-flight frames for in-domain ball labelling (YOLO layout).

Deliberately does NOT blur-reject: the in-play ball is fast and motion-blurred, so the
smeared frames are exactly the positives we need. (scripts/extract_frames.py drops them
-- likely why past training sets had no in-flight ball examples.)

Writes full-resolution frames into  <out>/images/<clip>_f<idx>.jpg  with a parallel
empty <out>/labels/ dir, ready to open in Vision Studio's Annotation Studio. Label ONLY
the real in-play ball; unlabelled stationary-white distractors then become implicit hard
negatives automatically.

    # one clip, review the window first
    python scripts/extract_flight_frames.py --clip ".../Delivery001.mp4" --start 0.40 --end 0.70
    # pilot batch
    python scripts/extract_flight_frames.py --dir ".../1_MP4" --clips Delivery001,Delivery003,... --start 0.40 --end 0.70
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2

DEFAULT_OUT = Path("vision_studio_workspace/Extracted Frames/1_MP4")


def adaptive_window(n: int) -> tuple[int, int]:
    """Length-adaptive flight window. Shorter clips place the delivery later (fixed
    run-up time = bigger fraction of a short clip). Calibrated on two hand-checked
    clips: Delivery001 (327f, flight ~150-210) and Delivery014 (189f, flight ~124-155).
    center_frac = 1.0 - 0.00138*n, clamped; window = [center-42, center+30]."""
    center_frac = min(0.82, max(0.45, 1.0 - 0.00138 * n))
    c = int(center_frac * n)
    return max(0, c - 42), min(n - 1, c + 30)


def extract_one(clip: Path, images_dir: Path, start: float, end: float, stride: int,
                adaptive: bool = False) -> int:
    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        print(f"  WARNING cannot open {clip.name}")
        return 0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    f0, f1 = adaptive_window(n) if adaptive else (int(n * start), int(n * end))
    written = 0
    idx = -1
    while True:
        ok, frame = cap.read()
        if not ok or idx >= f1:
            break
        idx += 1
        if idx < f0 or (idx - f0) % stride != 0:
            continue
        name = f"{clip.stem}_f{idx:06d}.jpg"
        cv2.imwrite(str(images_dir / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        written += 1
    cap.release()
    print(f"  {clip.name}: frames {f0}-{f1} (of {n}) stride {stride} -> {written} images")
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", help="single clip path")
    ap.add_argument("--dir", help="directory of clips")
    ap.add_argument("--clips", help="comma-separated stems to include from --dir (default: all)")
    ap.add_argument("--start", type=float, default=0.40, help="window start as fraction of clip")
    ap.add_argument("--end", type=float, default=0.70, help="window end as fraction of clip")
    ap.add_argument("--stride", type=int, default=1, help="1 = every frame (needed at 30fps)")
    ap.add_argument("--adaptive", action="store_true",
                    help="length-adaptive window (delivery sits later in shorter clips)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out = Path(args.out)
    images_dir = out / "images"
    labels_dir = out / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    if args.clip:
        clips = [Path(args.clip)]
    else:
        allc = sorted(Path(args.dir).glob("*.mp4"))
        if args.clips:
            want = {s.strip() for s in args.clips.split(",")}
            allc = [c for c in allc if c.stem in want]
        clips = allc

    mode = "adaptive (length-scaled)" if args.adaptive else f"{args.start:.2f}-{args.end:.2f}"
    print(f"window {mode}  stride {args.stride}  -> {images_dir}")
    total = sum(extract_one(c, images_dir, args.start, args.end, args.stride, args.adaptive)
                for c in clips)
    print(f"\ntotal {total} images across {len(clips)} clip(s)")
    print(f"images: {images_dir}\nlabels (empty, for Annotation Studio): {labels_dir}")


if __name__ == "__main__":
    main()
