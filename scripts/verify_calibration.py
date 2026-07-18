"""Verify saved camera calibration profiles and their reprojection error.

Scans the calibration profile directory, reports the RMS reprojection error for
each camera/ground profile, and fails if any profile exceeds the acceptable
threshold. Supports the multi-camera, multi-ground production workflow built by
scripts/calibrate.py, scripts/calibration_wizard.py and scripts/run_calibration.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_PROFILE_DIR = PROJECT_ROOT / "config" / "calibration_profiles"
GOOD_PX = 3.0
ACCEPTABLE_PX = 6.0


def quality(rms_error_px: float) -> str:
    if rms_error_px < GOOD_PX:
        return "GOOD"
    if rms_error_px < ACCEPTABLE_PX:
        return "ACCEPTABLE"
    return "POOR"


def verify_profiles(profile_dir: Path | str, max_error_px: float = ACCEPTABLE_PX) -> int:
    profile_dir = Path(profile_dir)
    profiles = sorted(profile_dir.glob("*.json"))
    if not profiles:
        print(f"No calibration profiles found in {profile_dir}")
        print("Capture calibration first with scripts/calibrate.py or scripts/run_calibration.py.")
        return 1

    print(f"Calibration profiles in {profile_dir}:")
    worst = 0.0
    failures = 0
    for path in profiles:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  {path.name}: UNREADABLE")
            failures += 1
            continue
        rms = float(data.get("rms_error_px", data.get("rms_error", 999.0)))
        camera = data.get("camera_id", "?")
        ground = data.get("ground", "?")
        worst = max(worst, rms)
        flag = "" if rms <= max_error_px else "  <-- EXCEEDS THRESHOLD"
        print(f"  cam {camera} / {ground}: rms {rms:.2f}px [{quality(rms)}]{flag}")
        if rms > max_error_px:
            failures += 1

    print(f"Worst reprojection error: {worst:.2f}px (threshold {max_error_px:.1f}px)")
    print("Status: " + ("FAILED" if failures else "PASSED"))
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify camera calibration profiles and reprojection error")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--max-error-px", type=float, default=ACCEPTABLE_PX)
    args = parser.parse_args()
    raise SystemExit(verify_profiles(args.profile_dir, args.max_error_px))


if __name__ == "__main__":
    main()
