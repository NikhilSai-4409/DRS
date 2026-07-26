"""ChArUco intrinsics capture/compute service — the backend for the Intrinsics tab.

This is the operator-facing intrinsics workflow layer on top of
:class:`core.calibration.MultiCameraCalibrator`. It is deliberately independent of
the live camera feed: callers hand it frames (grabbed from the feed by the API, or
uploaded), and it detects the board, scores coverage, stores accepted views, then
runs the real ``calibrateCameraCharuco`` on them and saves ``intrinsics_<id>.json``.

Coverage is scored in image space (board position, size, and skew) so it needs no
prior intrinsics — the chicken-and-egg that a pose-based score would hit. The goal
is only to guide the operator to vary the board; richer spread → lower RMS.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import cv2
import numpy as np

from config.settings import CALIBRATION_DIR, CHARUCO_SQUARES_X, CHARUCO_SQUARES_Y
from core.calibration import MultiCameraCalibrator, load_intrinsics_data
from core.calibration_paths import get_intrinsics_captures_dir

# The operator should collect at least this many good views before computing; a
# richer set (up to ~30) lowers distortion error but adds diminishing returns.
MIN_VIEWS_TO_COMPUTE = 8
RECOMMENDED_MAX_VIEWS = 30
# A board pose is only useful when enough of its interior corners are seen.
MIN_CORNERS = 6

COVERAGE_BUCKETS = ("left", "center", "right", "near", "far", "tilted")
_MANIFEST = "captures.json"


class IntrinsicsCalibrationService:
    """Manage the capture → coverage → compute → save loop for one camera at a time."""

    def __init__(self, base_dir: Path | None = None, calibrator: MultiCameraCalibrator | None = None) -> None:
        self.base = base_dir or CALIBRATION_DIR
        self.calibrator = calibrator or MultiCameraCalibrator()

    # ---- paths / manifest ----
    def _dir(self, camera_id: int) -> Path:
        return get_intrinsics_captures_dir(camera_id, self.base)

    def _load_manifest(self, camera_id: int) -> list[dict]:
        path = self._dir(camera_id) / _MANIFEST
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _save_manifest(self, camera_id: int, entries: list[dict]) -> None:
        d = self._dir(camera_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / _MANIFEST).write_text(json.dumps(entries, indent=2), encoding="utf-8")

    # ---- capture ----
    def add_capture(self, camera_id: int, frame_bgr: np.ndarray) -> dict:
        """Detect the board in a frame; store it and report coverage if usable."""
        if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
            return {"accepted": False, "reason": "empty frame"}
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        charuco_corners, charuco_ids, _, _ = self.calibrator.detector.detectBoard(gray)
        if charuco_ids is None or charuco_corners is None or len(charuco_ids) < MIN_CORNERS:
            return {"accepted": False, "reason": "board not clearly visible — move it fully into frame"}

        buckets = self._coverage_buckets(charuco_corners, gray.shape)
        entries = self._load_manifest(camera_id)
        filename = f"view_{len(entries):03d}.jpg"
        cv2.imwrite(str(self._dir(camera_id) / filename), frame_bgr)
        entries.append({"file": filename, "corners": int(len(charuco_ids)), "buckets": buckets})
        self._save_manifest(camera_id, entries)

        status = self.status(camera_id)
        return {"accepted": True, "corners": int(len(charuco_ids)), "buckets": buckets,
                "captures": status["captures"], "coverage": status["coverage"], "ready": status["ready"]}

    def _coverage_buckets(self, corners: np.ndarray, shape: tuple[int, int]) -> list[str]:
        h, w = shape[:2]
        pts = corners.reshape(-1, 2)
        cx = float(pts[:, 0].mean()) / w
        bbox_w = float(pts[:, 0].max() - pts[:, 0].min())
        bbox_h = float(pts[:, 1].max() - pts[:, 1].min())
        size_frac = (bbox_w * bbox_h) / (w * h)
        buckets = ["left" if cx < 0.4 else "right" if cx > 0.6 else "center"]
        if size_frac > 0.12:
            buckets.append("near")
        elif size_frac < 0.05:
            buckets.append("far")
        # Skew: observed bbox aspect vs the board's true aspect ⇒ the board is angled.
        true_aspect = CHARUCO_SQUARES_X / CHARUCO_SQUARES_Y
        obs_aspect = bbox_w / max(bbox_h, 1.0)
        if abs(obs_aspect - true_aspect) / true_aspect > 0.30:
            buckets.append("tilted")
        return buckets

    # ---- status ----
    def status(self, camera_id: int) -> dict:
        entries = self._load_manifest(camera_id)
        coverage = {b: False for b in COVERAGE_BUCKETS}
        for entry in entries:
            for b in entry.get("buckets", []):
                coverage[b] = True
        captures = len(entries)
        return {
            "camera_id": camera_id,
            "captures": captures,
            "min_views": MIN_VIEWS_TO_COMPUTE,
            "recommended_max": RECOMMENDED_MAX_VIEWS,
            "coverage": coverage,
            "ready": captures >= MIN_VIEWS_TO_COMPUTE,
            "calibrated": load_intrinsics_data(camera_id) is not None,
        }

    # ---- compute ----
    def compute(self, camera_id: int) -> dict:
        entries = self._load_manifest(camera_id)
        if len(entries) < MIN_VIEWS_TO_COMPUTE:
            raise ValueError(f"Need at least {MIN_VIEWS_TO_COMPUTE} views to compute, have {len(entries)}")
        image_paths = [self._dir(camera_id) / e["file"] for e in entries]
        calibration = self.calibrator.calibrate_camera(camera_id, image_paths)
        self.calibrator.save_per_camera(calibration)  # → intrinsics_<id>.json (Slice 1)
        return {
            "camera_id": camera_id,
            "rms_error": round(float(calibration.rms_error), 4),
            "image_size": list(calibration.image_size),
            "camera_matrix": calibration.camera_matrix,
            "distortion_coeffs": calibration.distortion_coeffs,
            "views_used": len(image_paths),
        }

    # ---- inspect / clear ----
    def load_saved(self, camera_id: int) -> dict | None:
        return load_intrinsics_data(camera_id)

    def clear_captures(self, camera_id: int) -> bool:
        d = self._dir(camera_id)
        if not d.exists():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True
