"""Tests for manual pitch calibration profiles."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.pitch_calibration import (
    MARKER_KEYS,
    ManualPitchCalibrator,
    calibration_status_payload,
    default_icc_profile,
    refresh_readiness_from_profiles,
)


@pytest.fixture()
def sample_markers() -> dict[str, dict[str, float]]:
    return {
        "off_stump": {"x": 100.0, "y": 300.0},
        "middle_stump": {"x": 180.0, "y": 298.0},
        "leg_stump": {"x": 260.0, "y": 300.0},
        "bowling_crease": {"x": 180.0, "y": 360.0},
        "popping_crease": {"x": 180.0, "y": 420.0},
    }


def test_default_icc_profile_contains_required_markers() -> None:
    profile = default_icc_profile()
    assert profile["world_dimensions"]["pitch_length_m"] == pytest.approx(20.12)
    assert profile["world_dimensions"]["pitch_width_m"] == pytest.approx(3.05)
    assert profile["required_markers"] == list(MARKER_KEYS)


def test_compute_homography_from_markers(sample_markers: dict[str, dict[str, float]]) -> None:
    calibrator = ManualPitchCalibrator()
    homography, error_cm = calibrator.compute_homography(sample_markers)
    assert len(homography) == 3
    assert error_cm >= 0.0
    world_x, world_y = calibrator.pixel_to_world(180.0, 298.0, homography)
    assert world_x == pytest.approx(0.1143, abs=0.02)
    assert world_y == pytest.approx(0.0, abs=0.02)


def test_save_and_load_profile_per_camera(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_markers: dict) -> None:
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")
    calibrator = ManualPitchCalibrator()
    saved = calibrator.save_profile(1, sample_markers, (1280, 720))
    assert saved.camera_id == 1
    assert saved.homography is not None
    loaded = calibrator.load_profile(1)
    assert loaded is not None
    assert loaded.markers == sample_markers
    assert (tmp_path / "pose_1.json").exists()
    assert not (tmp_path / "calibration_1.json").exists()  # no longer collides with intrinsics


def test_refresh_readiness_from_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_markers: dict) -> None:
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    readiness_path = tmp_path / "readiness.json"
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", readiness_path)
    ManualPitchCalibrator().save_profile(2, sample_markers, (960, 540))
    refresh_readiness_from_profiles()
    data = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert data["homography_error_cm"] is not None
    assert data["source"] == "manual_pitch_markers"
    assert "2" in data["per_camera"]


def test_calibration_status_payload_counts_manual_profiles_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_markers: dict,
) -> None:
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")
    (tmp_path / "readiness.json").write_text("{}", encoding="utf-8")
    ManualPitchCalibrator().save_profile(1, sample_markers, (1280, 720))
    status = calibration_status_payload()
    assert status["calibrated"] is True
    assert status["camera_count"] == 1
    assert status["camera_ids"] == [1]


def test_missing_marker_raises(sample_markers: dict[str, dict[str, float]]) -> None:
    incomplete = dict(sample_markers)
    incomplete.pop("leg_stump")
    calibrator = ManualPitchCalibrator()
    with pytest.raises(ValueError, match="Missing markers"):
        calibrator.compute_homography(incomplete)


def test_pitch_mm_pixel_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_markers: dict) -> None:
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    calibrator = ManualPitchCalibrator()
    calibrator.save_profile(1, sample_markers, (1280, 720))
    pitch_mm = calibrator.pixel_to_pitch_mm(1, 180.0, 298.0)
    assert pitch_mm is not None
    px, py = calibrator.pitch_mm_to_pixel(1, pitch_mm[0], pitch_mm[1])
    assert px == pytest.approx(180.0, abs=2.0)
    assert py == pytest.approx(298.0, abs=2.0)
