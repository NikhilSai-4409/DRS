"""Sanity-check the CANONICAL calibration on one real delivery.

Projects the tracked ball through a core/calibration.py profile and prints ground speed +
bounce location, so you can answer "does the canonical calibration produce physically
correct numbers?" BEFORE wiring it into the pipeline. It touches no producer and no UI.

Usage:
    python scripts/verify_calibrated_delivery.py \
        --results data/testing/outputs/<job>/results.json \
        --profile 0 [--ground main]

Create a profile first (interactive, once) with:  python scripts/run_calibration.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.calibration import PitchCalibrator, summarize_ground_trajectory


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify a calibrated delivery's physics")
    ap.add_argument("--results", required=True, help="results.json from a Testing analysis")
    ap.add_argument("--profile", required=True, help="camera id of the calibration profile")
    ap.add_argument("--ground", default=None, help="ground name (default: newest for the camera)")
    args = ap.parse_args()

    pc = PitchCalibrator()
    profile = pc.load_profile(args.profile, args.ground)
    if profile is None:
        print(f"No calibration profile for camera {args.profile}"
              f"{' / ' + args.ground if args.ground else ''} in {pc.profile_dir}/.")
        print("Create one first:  python scripts/run_calibration.py")
        return

    res = json.loads(Path(args.results).read_text(encoding="utf-8"))
    cam = (res.get("cameras") or [{}])[0]
    tracks = cam.get("tracking_points") or []
    real = [t for t in tracks if t.get("real_detection")]
    points = [(t["x"], t["y"]) for t in real]
    times = [t["timestamp_ms"] / 1000.0 for t in real]
    bounce_px = cam.get("bounce_point_px")

    summary = summarize_ground_trajectory(
        lambda x, y: pc.pixel_to_world(x, y, 0.0), points, times, bounce_px=bounce_px
    )

    width, height = (profile.get("image_size") or [0, 0])[:2]
    rms = profile.get("rms_error_px", profile.get("rms_error"))
    intrinsics = profile.get("intrinsics_source", "unknown")
    intr_label = {"charuco": "ChArUco ✓", "estimated": "Estimated ⚠"}.get(intrinsics, f"{intrinsics} ?")
    print("Calibration profile:")
    print(f"  Camera: {args.profile}   Ground: {args.ground or '(newest)'}")
    print(f"  Resolution: {width}x{height}")
    print(f"  RMS reprojection: {rms} px")
    print(f"  Intrinsics: {intr_label}")
    print("  Ground pose: solvePnP ✓")
    for warning in (profile.get("warnings") or []):
        print("\nWARNING")
        print("-------")
        print(warning)
    print("\nTrajectory:")
    print("  Producer: Calibrated (core/calibration.py)")
    print(f"  Ground speed: {summary['ground_speed_kmh']} km/h  (ground-shadow; height not recovered)")
    bounce = summary["bounce"]
    if bounce:
        print(f"  Bounce: {bounce['from_stumps_m']} m from stumps  (lateral {bounce['lateral_m']} m)")
    else:
        print("  Bounce: not detected")
    print(f"  Points projected: {summary['points_projected']}/{summary['points_total']}")

    # A couple of quick sanity flags the operator can eyeball.
    gs = summary["ground_speed_kmh"]
    if not (40.0 <= gs <= 170.0):
        print(f"  ⚠ ground speed {gs} km/h is outside a typical delivery range — check calibration/tracking")


if __name__ == "__main__":
    main()
