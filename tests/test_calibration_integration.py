"""Tests for calibration integration into pipelines."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pytest

from core.pitch_calibration import ManualPitchCalibrator


FRAME_SIZE = (640, 360)

CALIBRATION_MARKERS = {
    "off_stump": {"x": 260.0, "y": 250.0},
    "middle_stump": {"x": 320.0, "y": 250.0},
    "leg_stump": {"x": 380.0, "y": 250.0},
    "bowling_crease": {"x": 320.0, "y": 250.0},
    "popping_crease": {"x": 320.0, "y": 330.0},
}


def test_homography_computation() -> None:
    """Homography should be computable from standard markers."""
    calibrator = ManualPitchCalibrator()
    homography, error = calibrator.compute_homography(CALIBRATION_MARKERS)
    assert homography is not None
    assert error >= 0.0
    assert isinstance(homography, (list, np.ndarray))


def test_pixel_to_pitch_transform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pixel_to_pitch_mm should convert pixel coords to world mm."""
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")

    calibrator = ManualPitchCalibrator()
    # Must save a profile first so pixel_to_pitch_mm can load it
    calibrator.save_profile(0, CALIBRATION_MARKERS, FRAME_SIZE)

    # Middle stump should map to recognizable coordinates
    result = calibrator.pixel_to_pitch_mm(0, 320.0, 250.0)
    assert result is not None
    x_mm, y_mm = result
    assert isinstance(x_mm, float)
    assert isinstance(y_mm, float)


def test_save_and_load_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calibration profile should round-trip through save/load."""
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")

    calibrator = ManualPitchCalibrator()
    calibrator.save_profile(1, CALIBRATION_MARKERS, FRAME_SIZE)

    # Verify file was created
    profile_files = list(tmp_path.glob("*.json"))
    assert len(profile_files) > 0

    # Load and verify
    loaded = ManualPitchCalibrator()
    success = loaded.load_profile(1)
    assert success


def test_calibration_status_payload() -> None:
    """calibration_status_payload should return well-formed dict."""
    from core.pitch_calibration import calibration_status_payload

    status = calibration_status_payload()
    assert isinstance(status, dict)
    assert "calibrated" in status
    assert "camera_count" in status


def test_multiple_camera_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Should support calibration profiles for multiple cameras."""
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")

    calibrator = ManualPitchCalibrator()
    # Save profiles for 3 cameras
    for cam_id in [0, 1, 2]:
        calibrator.save_profile(cam_id, CALIBRATION_MARKERS, FRAME_SIZE)

    # All should be loadable
    for cam_id in [0, 1, 2]:
        loaded = ManualPitchCalibrator()
        assert loaded.load_profile(cam_id)


def test_homography_error_reasonable() -> None:
    """Homography error should be within acceptable bounds for standard markers."""
    calibrator = ManualPitchCalibrator()
    _homography, error = calibrator.compute_homography(CALIBRATION_MARKERS)
    # Error should be finite and reasonable
    assert error < 100.0  # cm
