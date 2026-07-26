"""Pitch pose (extrinsics) calibration — Slice 3A.

Synthetic ground truth: project the 9 pitch world points through a known camera pose,
then confirm the service's solvePnP recovers that pose (near-zero reprojection error).
No hardware, no review-pipeline coupling.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.calibration import PITCH_WORLD_POINTS
from core.calibration_paths import get_intrinsics_path
from core.pose_calibration import POSE_METHOD, PoseCalibrationService
from utils.helpers import save_json

K = np.array([[1100.0, 0, 960], [0, 1100.0, 540], [0, 0, 1]], dtype=np.float64)


def _look_at(cam_pos, target, up=(0, 0, 1)):
    """A realistic camera pose (rvec, tvec): positioned above/behind, looking at the pitch."""
    C = np.asarray(cam_pos, dtype=np.float64)
    z = np.asarray(target, dtype=np.float64) - C
    z /= np.linalg.norm(z)
    x = np.cross(np.asarray(up, dtype=np.float64), z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R_world_to_cam = np.column_stack([x, y, z]).T
    rvec = cv2.Rodrigues(R_world_to_cam)[0]
    tvec = (-R_world_to_cam @ C).reshape(3, 1)
    return rvec, tvec


# Camera 10 m behind the bowling crease, 12 m up, looking down the pitch.
RVEC, TVEC = _look_at([0.0, -10.0, 12.0], [0.0, 11.0, 0.3])


def _project(camera_matrix, rvec, tvec):
    return cv2.projectPoints(PITCH_WORLD_POINTS, rvec, tvec, camera_matrix, np.zeros(5))[0].reshape(-1, 2)


def _write_intrinsics(base: Path, camera_id: int, camera_matrix) -> None:
    save_json(
        {"type": "intrinsics", "camera_matrix": np.asarray(camera_matrix).tolist(),
         "distortion_coeffs": [[0.0, 0.0, 0.0, 0.0, 0.0]]},
        get_intrinsics_path(camera_id, base),
    )


@pytest.fixture()
def svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # intrinsics are read via core.calibration's CALIBRATION_DIR; pose written to base.
    monkeypatch.setattr("core.calibration.CALIBRATION_DIR", tmp_path)
    return PoseCalibrationService(base_dir=tmp_path), tmp_path


def test_pose_recovers_known_pose_with_real_intrinsics(svc):
    service, base = svc
    _write_intrinsics(base, 5, K)
    image_points = _project(K, RVEC, TVEC)

    result = service.compute_pose(5, image_points.tolist(), (1920, 1080))
    assert result["intrinsics_source"] == "charuco"     # used the real lens, not a guess
    assert result["reproj_error_px"] < 1.0              # pose reprojects the points cleanly
    assert result["quality"] == "good"

    pose = service.load_pose(5)
    assert pose is not None and pose["method"] == POSE_METHOD
    assert np.allclose(pose["tvec"], TVEC.reshape(-1), atol=0.5)   # recovered the translation
    assert (base / "pose_5.json").exists()


def test_pose_without_intrinsics_is_estimated_and_warns(svc):
    service, _ = svc
    image_points = _project(K, RVEC, TVEC)   # no intrinsics file written for cam 6

    result = service.compute_pose(6, image_points.tolist(), (1920, 1080))
    assert result["intrinsics_source"] == "estimated"
    assert result["warnings"] and "estimated" in result["warnings"][0].lower()


def test_compute_pose_requires_nine_points(svc):
    service, _ = svc
    with pytest.raises(ValueError, match="9 image points"):
        service.compute_pose(0, [[1.0, 1.0]] * 5, (1920, 1080))


def test_status_reflects_intrinsics_and_pose(svc):
    service, base = svc
    st = service.status(9)
    assert st["has_intrinsics"] is False and st["has_pose"] is False

    _write_intrinsics(base, 9, K)
    service.compute_pose(9, _project(K, RVEC, TVEC).tolist(), (1920, 1080))
    st = service.status(9)
    assert st["has_intrinsics"] is True and st["has_pose"] is True
    assert st["reproj_error_px"] is not None and st["reprojection_quality"] == "good"
    # Reprojection quality is NOT approval: `acceptable` is the plausibility gate,
    # and `in_use` says whether the review pipeline will actually project with it.
    assert "acceptable" in st and "in_use" in st
    assert st["in_use"] is (st["has_pose"] and bool(st["acceptable"]))


def test_load_pose_ignores_a_non_pose_profile(svc):
    # A legacy homography profile sharing pose_<id>.json must NOT read back as a pose.
    service, base = svc
    save_json({"type": "pose", "method": "manual_pitch_markers", "camera_id": 10, "markers": {}},
              base / "pose_10.json")
    assert service.load_pose(10) is None
    assert service.status(10)["has_pose"] is False


# --- acceptance gate ---------------------------------------------------------

def test_good_pose_passes_the_acceptance_gate(svc):
    service, base = svc
    _write_intrinsics(base, 5, K)
    result = service.compute_pose(5, _project(K, RVEC, TVEC).tolist(), (1920, 1080))
    assert result["acceptable"] is True
    assert all(result["acceptance"]["checks"].values())
    assert result["acceptance"]["reasons"] == []


def test_pitch_behind_camera_is_rejected():
    from core.pose_calibration import assess_pose
    # R = identity, tvec z negative → the whole pitch lands behind the camera.
    assessment = assess_pose([0, 0, 0], [0, 0, -5], K, np.zeros(5), (1920, 1080), reproj_px=0.2)
    assert assessment["checks"]["faces_pitch"] is False
    assert assessment["acceptable"] is False
    assert any("behind" in reason for reason in assessment["reasons"])


def test_high_reprojection_fails_the_gate():
    from core.pose_calibration import assess_pose
    assessment = assess_pose(RVEC, TVEC, K, np.zeros(5), (1920, 1080), reproj_px=5.0)
    assert assessment["checks"]["reproj_ok"] is False
    assert assessment["acceptable"] is False
