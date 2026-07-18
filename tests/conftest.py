import sys
from pathlib import Path

import pytest

# Add the project root to Python path so tests can import modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent))

from synthetic_drs_fixtures import (  # noqa: E402
    SyntheticBallDetector,
    ensure_synthetic_drs_videos,
    save_synthetic_calibration,
)


@pytest.fixture(scope="session", autouse=True)
def synthetic_drs_videos() -> None:
    ensure_synthetic_drs_videos()


@pytest.fixture()
def synthetic_e2e_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")
    readiness_path = save_synthetic_calibration(tmp_path)

    from core.readiness import ReadinessGate

    def readiness_factory(*args, **kwargs):
        kwargs["calibration_path"] = readiness_path
        return ReadinessGate(*args, **kwargs)

    monkeypatch.setattr("core.testing_pipeline.BallDetector", SyntheticBallDetector)
    monkeypatch.setattr("core.testing_pipeline.ReadinessGate", readiness_factory)
    return readiness_path


@pytest.fixture()
def calibration_markers() -> dict:
    """Standard calibration markers for testing."""
    return {
        "off_stump": {"x": 260.0, "y": 250.0},
        "middle_stump": {"x": 320.0, "y": 250.0},
        "leg_stump": {"x": 380.0, "y": 250.0},
        "bowling_crease": {"x": 320.0, "y": 250.0},
        "popping_crease": {"x": 320.0, "y": 330.0},
    }


@pytest.fixture()
def calibrated_pitch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calibration_markers: dict) -> Path:
    """Create a calibrated pitch profile in tmp_path."""
    monkeypatch.setattr("core.pitch_calibration.CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr("core.pitch_calibration.READINESS_PATH", tmp_path / "readiness.json")
    from core.pitch_calibration import ManualPitchCalibrator

    calibrator = ManualPitchCalibrator()
    calibrator.save_profile(0, calibration_markers, (640, 360))
    return tmp_path


@pytest.fixture()
def sample_trajectory_points():
    """Sample trajectory points for trajectory prediction tests."""
    from core.trajectory import TrajectoryPoint

    return [
        TrajectoryPoint(0.00, -5.0, 0.0, 1.8, 30.0, 0.0, -5.0),
        TrajectoryPoint(0.02, -4.4, 0.0, 1.6, 30.0, 0.0, -5.0),
        TrajectoryPoint(0.04, -3.8, 0.0, 1.3, 30.0, 0.0, -5.0),
        TrajectoryPoint(0.06, -3.2, 0.0, 1.0, 30.0, 0.0, -5.0),
        TrajectoryPoint(0.08, -2.6, 0.0, 0.6, 30.0, 0.0, -3.0),
    ]

