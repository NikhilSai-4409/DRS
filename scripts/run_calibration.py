"""Interactive single-camera pitch calibration wizard.

Uses the existing core.calibration.PitchCalibrator (which the test suite
already covers). Supports either a live camera frame or a frozen image
file as the calibration source.

Usage:
    python scripts/run_calibration.py --camera 0 --profile home
    python scripts/run_calibration.py --image frame.png --camera 0 --profile main
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make repo root importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from core.calibration import PitchCalibrator


def _grab_from_camera(camera_index: int) -> "cv2.Mat | None":
    print(f"Opening camera {camera_index}...")
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"Cannot open camera {camera_index}")
        return None
    print("Camera open. Press SPACE to capture the calibration frame. ESC to cancel.")
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        cv2.putText(
            frame,
            "SPACE = capture   ESC = cancel",
            (20, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
        )
        cv2.imshow("DRS calibration capture", frame)
        key = cv2.waitKey(30) & 0xFF
        if key == 32:  # SPACE
            cap.release()
            cv2.destroyAllWindows()
            return frame
        if key == 27:  # ESC
            cap.release()
            cv2.destroyAllWindows()
            return None


def main() -> int:
    parser = argparse.ArgumentParser(description="DRS pitch calibration wizard")
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default: 0)")
    parser.add_argument("--image", type=str, default=None, help="Use an image file instead of a live camera")
    parser.add_argument("--profile", type=str, required=True, help="Profile name, e.g. 'home_ground'")
    parser.add_argument("--ground", type=str, default="default", help="Ground/venue name")
    args = parser.parse_args()

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"Cannot read image: {args.image}")
            return 1
        print(f"Using image: {args.image}")
    else:
        frame = _grab_from_camera(args.camera)
        if frame is None:
            print("Cancelled.")
            return 0

    calibrator = PitchCalibrator()
    try:
        profile = calibrator.calibrate_interactive(
            camera_frame=frame,
            camera_id=args.camera,
            profile_name=args.profile,
            ground_name=args.ground,
        )
    except KeyboardInterrupt:
        print("Calibration cancelled.")
        return 0
    except Exception as exc:
        print(f"Calibration failed: {exc}")
        return 1

    rms = float(profile.get("rms_error_px", 0.0))
    quality = "GOOD" if rms < 3 else "ACCEPTABLE" if rms < 6 else "POOR"
    print(f"Calibration saved. RMS={rms:.2f}px ({quality})")
    print(
        "Profile: config/calibration_profiles/"
        f"{args.camera}_{args.ground}.json"
    )
    print("Ready for live DRS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
