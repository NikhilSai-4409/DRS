"""Pitch pose (extrinsics) calibration — Slice 3A.

Given a camera's stored intrinsics and the operator's 9 pitch reference points,
solve the camera's full 3D pose (rvec/tvec) with ``cv2.solvePnP`` and score it by
reprojection error. This is the piece that finally consumes the Slice 2 intrinsics.

Scope (3A): GENERATE + VALIDATE + SAVE a pose. It does NOT touch the review pipeline
(that is 3B: feeding PoseProjection). The 9-point non-coplanar target (crease points on
the ground + striker stump tops at 0.711 m) is what makes a metric pose recoverable;
a coplanar ground set could only yield a homography.

Storage note: the pose is written to ``pose_<id>.json`` per the agreed naming. That
file currently also holds the legacy homography profile (ManualPitchCalibrator); the
two are method-tagged and this service reads only its own ``pitch_pose_solvepnp``
records. Reconciling who owns pose_<id>.json for live writes is a 3B/UI-redesign
decision — this service is validated in isolation and is not yet wired to live writes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from config.settings import CALIBRATION_DIR
from core.calibration import PITCH_WORLD_POINTS, PitchCalibrator, load_intrinsics_data
from core.calibration_paths import get_pose_path
from utils.helpers import load_json, save_json
from utils.logger import get_logger

log = get_logger(__name__)

POSE_METHOD = "pitch_pose_solvepnp"
N_POINTS = 9


def _quality(reproj_px: float) -> str:
    if reproj_px < 3.0:
        return "good"
    if reproj_px < 6.0:
        return "acceptable"
    return "poor"


# Acceptance gate: solvePnP can converge on a mathematically valid but physically
# implausible pose. These checks catch that before a pose is trusted / saved live.
MAX_ACCEPT_REPROJ_PX = 1.5
MIN_CAMERA_HEIGHT_M = 0.3
MAX_CAMERA_HEIGHT_M = 40.0
MAX_ROLL_DEG = 45.0


def assess_pose(rvec, tvec, camera_matrix, dist_coeffs, image_size, reproj_px: float) -> dict:
    """Plausibility gate for a solved pose — the UI should refuse Save if not acceptable."""
    width, height = int(image_size[0]), int(image_size[1])
    rvec = np.asarray(rvec, dtype=np.float64).reshape(3, 1)
    tvec = np.asarray(tvec, dtype=np.float64).reshape(3, 1)
    R, _ = cv2.Rodrigues(rvec)

    camera_center = (-R.T @ tvec).reshape(-1)          # camera position in world metres
    camera_height_m = float(camera_center[2])          # world Z is up
    cam_frame = (R @ PITCH_WORLD_POINTS.T + tvec).T     # pitch points in camera frame
    faces_pitch = bool(np.all(cam_frame[:, 2] > 0))     # all in front of the camera
    roll_deg = float(np.degrees(np.arcsin(np.clip(R[0, 2], -1.0, 1.0))))

    projected = cv2.projectPoints(
        PITCH_WORLD_POINTS, rvec, tvec, np.asarray(camera_matrix, np.float64), np.asarray(dist_coeffs, np.float64)
    )[0].reshape(-1, 2)
    mx, my = 0.25 * width, 0.25 * height
    points_in_frame = bool(np.all(
        (projected[:, 0] > -mx) & (projected[:, 0] < width + mx)
        & (projected[:, 1] > -my) & (projected[:, 1] < height + my)
    ))

    checks = {
        "reproj_ok": bool(reproj_px < MAX_ACCEPT_REPROJ_PX),
        "points_in_frame": points_in_frame,
        "faces_pitch": faces_pitch,
        "height_plausible": bool(MIN_CAMERA_HEIGHT_M <= camera_height_m <= MAX_CAMERA_HEIGHT_M),
        "roll_ok": bool(abs(roll_deg) < MAX_ROLL_DEG),
    }
    reasons = []
    if not checks["reproj_ok"]:
        reasons.append(f"reprojection error {reproj_px:.2f} px exceeds {MAX_ACCEPT_REPROJ_PX} px")
    if not checks["points_in_frame"]:
        reasons.append("the projected pitch falls well outside the image")
    if not checks["faces_pitch"]:
        reasons.append("some pitch points sit behind the camera")
    if not checks["height_plausible"]:
        reasons.append(f"camera height {camera_height_m:.1f} m is outside the plausible range")
    if not checks["roll_ok"]:
        reasons.append(f"camera roll {roll_deg:.0f}° is implausible")
    return {
        "acceptable": all(checks.values()),
        "checks": checks,
        "reasons": reasons,
        "camera_height_m": round(camera_height_m, 2),
        "roll_deg": round(roll_deg, 1),
    }


class _PosePointProjector:
    """Projects a single pose-frame point to pixels via the solved extrinsics."""

    __slots__ = ("rvec", "tvec", "camera_matrix", "dist_coeffs")

    def __init__(self, rvec, tvec, camera_matrix, dist_coeffs) -> None:
        self.rvec = rvec
        self.tvec = tvec
        self.camera_matrix = camera_matrix
        self.dist_coeffs = dist_coeffs

    def world_to_pixel(self, x_m: float, y_m: float, z_m: float) -> tuple[float, float]:
        point = np.array([[[float(x_m), float(y_m), float(z_m)]]], dtype=np.float64)
        projected, _ = cv2.projectPoints(
            point, self.rvec, self.tvec, self.camera_matrix, self.dist_coeffs)
        return float(projected[0, 0, 0]), float(projected[0, 0, 1])


class PoseCalibrationService:
    """Solve, score, and persist a camera's pitch pose from intrinsics + 9 points."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = base_dir or CALIBRATION_DIR
        # Reuse the proven intrinsics loader (real ChArUco intrinsics preferred, estimated
        # pinhole as a loud fallback) so the pose honestly reports what it used.
        self._intrinsics = PitchCalibrator()

    def _pose_path(self, camera_id: int) -> Path:
        return get_pose_path(camera_id, self.base)

    def compute_pose(self, camera_id: int, image_points, image_size) -> dict:
        pts = np.asarray(image_points, dtype=np.float32)
        if pts.shape != (N_POINTS, 2):
            raise ValueError(f"Expected {N_POINTS} image points, got shape {tuple(pts.shape)}")
        image_size = (int(image_size[0]), int(image_size[1]))

        camera_matrix, dist_coeffs, intrinsics_source, warnings = self._intrinsics._load_intrinsics(camera_id, image_size)
        ok, rvec, tvec = cv2.solvePnP(
            PITCH_WORLD_POINTS, pts, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
        )
        if not ok:
            raise ValueError("cv2.solvePnP failed for the supplied pitch points")

        projected = cv2.projectPoints(PITCH_WORLD_POINTS, rvec, tvec, camera_matrix, dist_coeffs)[0].reshape(-1, 2)
        reproj = float(np.sqrt(np.mean(np.sum((projected - pts) ** 2, axis=1))))
        acceptance = assess_pose(rvec, tvec, camera_matrix, dist_coeffs, image_size, reproj)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        profile = {
            "type": "pose",
            "schema_version": 1,
            "camera_id": int(camera_id),
            "method": POSE_METHOD,
            "image_size": list(image_size),
            "reproj_error_px": round(reproj, 3),
            "acceptance": acceptance,                  # plausibility gate result, persisted
            "intrinsics_source": intrinsics_source,   # "charuco" | "estimated"
            "warnings": warnings,                      # persisted so the pose self-declares
            "rvec": rvec.reshape(-1).astype(float).tolist(),
            "tvec": tvec.reshape(-1).astype(float).tolist(),
            "camera_matrix": np.asarray(camera_matrix, dtype=float).tolist(),
            "dist_coeffs": np.asarray(dist_coeffs, dtype=float).reshape(-1).tolist(),
            "image_points": pts.astype(float).tolist(),
            "world_points": PITCH_WORLD_POINTS.astype(float).tolist(),
            "created_at": now,
            "updated_at": now,
        }
        save_json(profile, self._pose_path(camera_id))
        return {
            "camera_id": int(camera_id),
            "reproj_error_px": round(reproj, 3),
            "quality": _quality(reproj),
            "acceptable": acceptance["acceptable"],
            "acceptance": acceptance,
            "intrinsics_source": intrinsics_source,
            "warnings": warnings,
        }

    def projector(self, camera_id: int):
        """A world→pixel projector for the review pipeline, or None.

        Returns an object exposing ``world_to_pixel(x_m, y_m, z_m)`` in the POSE
        world frame (X across, Y from the bowling crease toward the striker, Z up)
        — :class:`core.projection.PoseProjection` converts pitch coordinates into
        that frame.

        A pose whose plausibility gate failed is NOT returned: an implausible
        solve would silently become the production geometry, which is worse than
        falling back to a projection we already know to distrust.
        """
        pose = self.load_pose(camera_id)
        if pose is None:
            return None
        if not (pose.get("acceptance") or {}).get("acceptable", True):
            log.warning("Camera {} has a pose calibration but it failed its "
                        "plausibility gate — not using it.", camera_id)
            return None
        try:
            return _PosePointProjector(
                rvec=np.asarray(pose["rvec"], dtype=np.float64).reshape(3, 1),
                tvec=np.asarray(pose["tvec"], dtype=np.float64).reshape(3, 1),
                camera_matrix=np.asarray(pose["camera_matrix"], dtype=np.float64),
                dist_coeffs=np.asarray(pose["dist_coeffs"], dtype=np.float64).reshape(-1, 1),
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("Camera {} pose profile is unusable: {}", camera_id, exc)
            return None

    def clear(self, camera_id: int) -> bool:
        """Remove a camera's pose so it falls back to the ground homography."""
        path = self._pose_path(camera_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def load_pose(self, camera_id: int) -> dict | None:
        path = self._pose_path(camera_id)
        if not path.exists():
            return None
        try:
            data = load_json(path)
        except Exception:
            return None
        return data if data.get("method") == POSE_METHOD else None

    def status(self, camera_id: int) -> dict:
        pose = self.load_pose(camera_id)
        reproj = pose.get("reproj_error_px") if pose else None
        acceptance = (pose or {}).get("acceptance") or {}
        # `quality` grades REPROJECTION only; `acceptable` is the physical
        # plausibility gate. They disagree often — a pose solved with estimated
        # intrinsics can reproject tidily and still place the camera underground —
        # so both are surfaced, plus whether the pose is actually being USED.
        # Reporting `quality: "acceptable"` alone read as approval for a pose the
        # pipeline had refused.
        return {
            "camera_id": int(camera_id),
            "has_intrinsics": load_intrinsics_data(camera_id) is not None,
            "has_pose": pose is not None,
            "reproj_error_px": reproj,
            "intrinsics_source": pose.get("intrinsics_source") if pose else None,
            "reprojection_quality": _quality(reproj) if reproj is not None else None,
            "acceptable": acceptance.get("acceptable") if pose else None,
            "rejection_reasons": acceptance.get("reasons") or [],
            "in_use": pose is not None and bool(acceptance.get("acceptable", False)),
            "warnings": (pose or {}).get("warnings") or [],
        }
