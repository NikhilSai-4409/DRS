"""Storage-separation contract (Slice 1 of the calibration redesign).

Camera intrinsics and the manual pitch profile used to share
``calibration_<id>.json`` and silently overwrite each other. They now live in
``intrinsics_<id>.json`` and ``pose_<id>.json``. These tests pin:

* intrinsics save/load round-trips through ``intrinsics_<id>.json`` with metadata,
* pose save/load round-trips through ``pose_<id>.json`` with metadata,
* a legacy ``calibration_<id>.json`` is still read (with a migration notice),
* the two outputs never collide for the same camera.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.calibration import (
    CameraCalibration,
    MultiCameraCalibrator,
    PitchCalibrator,
    intrinsics_path,
    load_intrinsics_data,
)
from core.calibration_paths import get_intrinsics_path, get_legacy_path, get_pose_path
from core.pitch_calibration import ManualPitchCalibrator, calibration_status_payload


SAMPLE_MARKERS = {
    "off_stump": {"x": 100.0, "y": 300.0},
    "middle_stump": {"x": 180.0, "y": 298.0},
    "leg_stump": {"x": 260.0, "y": 300.0},
    "bowling_crease": {"x": 180.0, "y": 360.0},
    "popping_crease": {"x": 180.0, "y": 420.0},
}

INTRINSICS_JSON = {
    "camera_matrix": [[1500.0, 0.0, 960.0], [0.0, 1500.0, 540.0], [0.0, 0.0, 1.0]],
    "distortion_coeffs": [[0.1, -0.05, 0.0, 0.0, 0.0]],
}


@pytest.fixture()
def cal_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both modules' CALIBRATION_DIR at a temp dir (intrinsics + pose share it)."""
    monkeypatch.setattr("core.calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")
    return tmp_path


def _sample_calibration(camera_id: int) -> CameraCalibration:
    return CameraCalibration(
        camera_id=camera_id,
        image_size=(1920, 1080),
        rms_error=0.4,
        camera_matrix=INTRINSICS_JSON["camera_matrix"],
        distortion_coeffs=INTRINSICS_JSON["distortion_coeffs"],
        rotation_vectors=[[0.0, 0.0, 0.0]],
        translation_vectors=[[0.0, 0.0, 10.0]],
    )


# --- intrinsics save/load -----------------------------------------------------

def test_intrinsics_save_writes_dedicated_file_with_metadata(cal_dir: Path) -> None:
    calibrator = MultiCameraCalibrator()
    path = calibrator.save_per_camera(_sample_calibration(3))

    assert path == cal_dir / "intrinsics_3.json"
    assert path.exists()
    assert not (cal_dir / "calibration_3.json").exists()  # never the shared name

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["type"] == "intrinsics"
    assert data["camera_id"] == 3
    assert data["schema_version"] == 1
    assert "created_at" in data


def test_intrinsics_load_round_trips(cal_dir: Path) -> None:
    calibrator = MultiCameraCalibrator()
    calibrator.save_per_camera(_sample_calibration(3))

    loaded = calibrator.load_per_camera(3)
    assert loaded.camera_id == 3
    assert loaded.camera_matrix[0][0] == 1500.0
    assert tuple(loaded.image_size) == (1920, 1080)  # JSON round-trips the tuple as a list


def test_load_per_camera_missing_raises(cal_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        MultiCameraCalibrator().load_per_camera(42)


# --- pose save/load -----------------------------------------------------------

def test_pose_save_writes_dedicated_file_with_metadata(cal_dir: Path) -> None:
    calibrator = ManualPitchCalibrator()
    profile = calibrator.save_profile(1, SAMPLE_MARKERS, (1280, 720))

    assert (cal_dir / "pose_1.json").exists()
    assert not (cal_dir / "calibration_1.json").exists()
    assert profile.type == "pose"

    data = json.loads((cal_dir / "pose_1.json").read_text(encoding="utf-8"))
    assert data["type"] == "pose"
    assert data["camera_id"] == 1
    assert data["method"] == "manual_pitch_markers"


def test_pose_load_round_trips(cal_dir: Path) -> None:
    calibrator = ManualPitchCalibrator()
    calibrator.save_profile(1, SAMPLE_MARKERS, (1280, 720))
    loaded = calibrator.load_profile(1)
    assert loaded is not None
    assert loaded.markers == SAMPLE_MARKERS
    assert loaded.type == "pose"


def test_pose_records_intrinsics_source(cal_dir: Path) -> None:
    MultiCameraCalibrator().save_per_camera(_sample_calibration(5))
    profile = ManualPitchCalibrator().save_profile(5, SAMPLE_MARKERS, (1280, 720))
    assert profile.intrinsics_source == "intrinsics_5.json"


# --- legacy backward compatibility -------------------------------------------

def test_legacy_intrinsics_file_still_read_with_notice(cal_dir: Path, capsys) -> None:
    legacy = cal_dir / "calibration_7.json"
    legacy.write_text(json.dumps({"camera_matrix": INTRINSICS_JSON["camera_matrix"],
                                  "distortion_coeffs": INTRINSICS_JSON["distortion_coeffs"]}),
                      encoding="utf-8")

    data = load_intrinsics_data(7)
    assert data is not None and data["camera_matrix"][0][0] == 1500.0
    assert "migration" in capsys.readouterr().out.lower()

    # PitchCalibrator picks the legacy intrinsics up as real ('charuco'), not estimated.
    _matrix, _dist, source, warnings = PitchCalibrator()._load_intrinsics(7, (1920, 1080))
    assert source == "charuco"
    assert warnings == []


def test_legacy_pose_file_still_read_with_notice(cal_dir: Path, capsys) -> None:
    calibrator = ManualPitchCalibrator()
    homography, error_cm = calibrator.compute_homography(SAMPLE_MARKERS)
    legacy = cal_dir / "calibration_8.json"
    legacy.write_text(json.dumps({
        "camera_id": 8,
        "method": "manual_pitch_markers",
        "markers": SAMPLE_MARKERS,
        "image_size": [1280, 720],
        "homography": homography,
        "homography_error_cm": error_cm,
    }), encoding="utf-8")

    loaded = calibrator.load_profile(8)
    assert loaded is not None
    assert loaded.camera_id == 8
    assert "migration" in capsys.readouterr().out.lower()

    # It also appears in the status payload / listing.
    assert 8 in calibration_status_payload()["camera_ids"]


def test_pose_prefers_new_file_over_legacy(cal_dir: Path) -> None:
    calibrator = ManualPitchCalibrator()
    # A stale legacy file with different markers must be ignored once pose_ exists.
    (cal_dir / "calibration_9.json").write_text(json.dumps({
        "camera_id": 9, "method": "manual_pitch_markers",
        "markers": {"off_stump": {"x": 1.0, "y": 1.0}}, "image_size": [10, 10],
    }), encoding="utf-8")
    calibrator.save_profile(9, SAMPLE_MARKERS, (1280, 720))

    loaded = calibrator.load_profile(9)
    assert loaded is not None
    assert loaded.markers == SAMPLE_MARKERS  # new file wins


# --- the core guarantee: no collision ----------------------------------------

def test_intrinsics_and_pose_coexist_without_overwrite(cal_dir: Path) -> None:
    MultiCameraCalibrator().save_per_camera(_sample_calibration(0))
    ManualPitchCalibrator().save_profile(0, SAMPLE_MARKERS, (1280, 720))

    assert (cal_dir / "intrinsics_0.json").exists()
    assert (cal_dir / "pose_0.json").exists()

    # Intrinsics survive the pose save intact (the old shared-file bug).
    intr = MultiCameraCalibrator().load_per_camera(0)
    assert intr.camera_matrix[0][0] == 1500.0
    pose = ManualPitchCalibrator().load_profile(0)
    assert pose is not None and pose.markers == SAMPLE_MARKERS

    # Deleting the pose leaves the intrinsics alone.
    assert ManualPitchCalibrator().delete_profile(0) is True
    assert not (cal_dir / "pose_0.json").exists()
    assert (cal_dir / "intrinsics_0.json").exists()


# --- path helper API ----------------------------------------------------------

def test_path_helpers_are_the_single_source_of_names(tmp_path: Path) -> None:
    assert get_intrinsics_path(2, tmp_path) == tmp_path / "intrinsics_2.json"
    assert get_pose_path(2, tmp_path) == tmp_path / "pose_2.json"
    assert get_legacy_path(2, tmp_path) == tmp_path / "calibration_2.json"


# --- migration matrix: the four states, made permanent -----------------------
# Each of intrinsics and pose is verified against all four initial states so a
# regression in the legacy fallback can never slip through silently.

def _write_legacy_intrinsics(cal_dir: Path, camera_id: int) -> None:
    # A real legacy calibration_<id>.json was the old save_per_camera output: a full
    # asdict(CameraCalibration), so load_per_camera can reconstruct it.
    from dataclasses import asdict
    (cal_dir / f"calibration_{camera_id}.json").write_text(
        json.dumps(asdict(_sample_calibration(camera_id))), encoding="utf-8",
    )


def _write_legacy_pose(cal_dir: Path, camera_id: int, markers: dict) -> None:
    calibrator = ManualPitchCalibrator()
    homography, error_cm = calibrator.compute_homography(markers)
    (cal_dir / f"calibration_{camera_id}.json").write_text(
        json.dumps({"camera_id": camera_id, "method": "manual_pitch_markers",
                    "markers": markers, "image_size": [1280, 720],
                    "homography": homography, "homography_error_cm": error_cm}),
        encoding="utf-8",
    )


# intrinsics ------------------------------------------------------------------

def test_intrinsics_state_only_legacy(cal_dir: Path, capsys) -> None:
    """Only calibration_<id>.json: loads, one migration notice, next save creates the new file."""
    _write_legacy_intrinsics(cal_dir, 11)
    assert MultiCameraCalibrator().load_per_camera(11).camera_matrix[0][0] == 1500.0
    assert "migration" in capsys.readouterr().out.lower()
    # Next save writes the dedicated file; the legacy one is left untouched (non-destructive).
    MultiCameraCalibrator().save_per_camera(_sample_calibration(11))
    assert (cal_dir / "intrinsics_11.json").exists()


def test_intrinsics_state_only_new(cal_dir: Path) -> None:
    MultiCameraCalibrator().save_per_camera(_sample_calibration(12))
    assert (cal_dir / "intrinsics_12.json").exists()
    assert not (cal_dir / "calibration_12.json").exists()
    assert MultiCameraCalibrator().load_per_camera(12).camera_matrix[0][0] == 1500.0


def test_intrinsics_state_both_new_wins(cal_dir: Path) -> None:
    # Stale legacy with a different focal must be ignored once the new file exists.
    (cal_dir / "calibration_13.json").write_text(
        json.dumps({"camera_matrix": [[999.0, 0, 1], [0, 999.0, 1], [0, 0, 1]],
                    "distortion_coeffs": [[0, 0, 0, 0, 0]]}), encoding="utf-8")
    MultiCameraCalibrator().save_per_camera(_sample_calibration(13))
    assert load_intrinsics_data(13)["camera_matrix"][0][0] == 1500.0  # new, not 999


def test_intrinsics_state_neither(cal_dir: Path) -> None:
    assert load_intrinsics_data(14) is None
    # Pose solve behaves exactly as today: falls back to estimated intrinsics, loudly.
    _matrix, _dist, source, warnings = PitchCalibrator()._load_intrinsics(14, (1920, 1080))
    assert source == "estimated"
    assert warnings and "estimated" in warnings[0].lower()


# pose ------------------------------------------------------------------------

def test_pose_state_only_legacy(cal_dir: Path, capsys) -> None:
    _write_legacy_pose(cal_dir, 21, SAMPLE_MARKERS)
    loaded = ManualPitchCalibrator().load_profile(21)
    assert loaded is not None and loaded.markers == SAMPLE_MARKERS
    assert "migration" in capsys.readouterr().out.lower()
    # Next save migrates to pose_<id>.json.
    ManualPitchCalibrator().save_profile(21, SAMPLE_MARKERS, (1280, 720))
    assert (cal_dir / "pose_21.json").exists()


def test_pose_state_only_new(cal_dir: Path) -> None:
    ManualPitchCalibrator().save_profile(22, SAMPLE_MARKERS, (1280, 720))
    assert (cal_dir / "pose_22.json").exists()
    assert not (cal_dir / "calibration_22.json").exists()
    assert ManualPitchCalibrator().load_profile(22) is not None


def test_pose_state_both_new_wins(cal_dir: Path) -> None:
    (cal_dir / "calibration_23.json").write_text(json.dumps({
        "camera_id": 23, "method": "manual_pitch_markers",
        "markers": {"off_stump": {"x": 1.0, "y": 1.0}}, "image_size": [10, 10]}),
        encoding="utf-8")
    ManualPitchCalibrator().save_profile(23, SAMPLE_MARKERS, (1280, 720))
    assert ManualPitchCalibrator().load_profile(23).markers == SAMPLE_MARKERS  # new wins


def test_pose_state_neither(cal_dir: Path) -> None:
    assert ManualPitchCalibrator().load_profile(24) is None  # fresh, exactly as today
