"""Guided camera calibration CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from core.calibration import MultiCameraCalibrator


def parse_camera_ids(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate DRS cameras from ChArUco board images")
    parser.add_argument("--cameras", required=True, help="Comma-separated camera IDs, e.g. 0,1,2")
    parser.add_argument("--images-dir", default="data/calibration/images", help="Folder containing cam_<id>/*.jpg")
    args = parser.parse_args()

    calibrator = MultiCameraCalibrator()
    root = Path(args.images_dir)
    for camera_id in parse_camera_ids(args.cameras):
        image_paths = sorted((root / f"cam_{camera_id}").glob("*.jpg"))
        if not image_paths:
            print(f"camera {camera_id}: no ChArUco images found under {root / f'cam_{camera_id}'}")
            continue
        calibration = calibrator.calibrate_camera(camera_id, image_paths)
        path = calibrator.save_per_camera(calibration)
        print(f"camera {camera_id}: rms={calibration.rms_error:.3f}px saved={path}")


if __name__ == "__main__":
    main()
