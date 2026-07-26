"""Intrinsics capture/compute service (Slice 2 backend).

Exercises the full feed-independent loop — detect → store → coverage → compute →
save intrinsics_<id>.json — against real ChArUco board views synthesised by
perspective-warping the canonical board. No camera feed involved.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.calibration import MultiCameraCalibrator
from core.intrinsics_calibration import (
    MIN_VIEWS_TO_COMPUTE,
    IntrinsicsCalibrationService,
)


@pytest.fixture(scope="module")
def board_img() -> np.ndarray:
    """One clean, frontal render of the canonical DRS ChArUco board (grayscale)."""
    return MultiCameraCalibrator().board.generateImage((1000, 700), marginSize=0, borderBits=1)


def _view(board: np.ndarray, W: int, H: int, quad: list[list[float]]) -> np.ndarray:
    """Place the board into a WxH white frame, warped to `quad` (BGR frame out)."""
    bh, bw = board.shape[:2]
    src = np.float32([[0, 0], [bw, 0], [bw, bh], [0, bh]])
    M = cv2.getPerspectiveTransform(src, np.float32(quad))
    warped = cv2.warpPerspective(board, M, (W, H), borderValue=255)
    return cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)


def _varied_views(board: np.ndarray, n: int, W: int = 1280, H: int = 720) -> list[np.ndarray]:
    """A spread of positions / sizes / tilts so calibration has real variation."""
    quads = [
        [[340, 120], [940, 120], [940, 560], [340, 560]],   # centered, large (near)
        [[80, 140], [560, 120], [560, 540], [80, 560]],     # left
        [[720, 120], [1200, 140], [1200, 560], [720, 540]], # right
        [[480, 260], [800, 260], [800, 480], [480, 480]],   # small centered (far)
        [[340, 140], [960, 100], [900, 560], [400, 600]],   # tilted / perspective
        [[300, 100], [900, 160], [960, 600], [260, 540]],   # tilted other way
        [[200, 200], [780, 160], [820, 600], [240, 560]],   # left-tilt
        [[520, 160], [1120, 200], [1080, 620], [560, 580]], # right-tilt
        [[360, 200], [880, 180], [900, 520], [380, 540]],   # mild
        [[420, 120], [1020, 160], [980, 600], [440, 560]],  # mixed
    ]
    return [_view(board, W, H, quads[i % len(quads)]) for i in range(n)]


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IntrinsicsCalibrationService:
    # intrinsics_<id>.json is written by MultiCameraCalibrator via core.calibration's dir.
    monkeypatch.setattr("core.calibration.CALIBRATION_DIR", tmp_path)
    return IntrinsicsCalibrationService(base_dir=tmp_path)


def test_add_capture_rejects_a_blank_frame(service: IntrinsicsCalibrationService) -> None:
    blank = np.full((720, 1280, 3), 255, np.uint8)
    result = service.add_capture(0, blank)
    assert result["accepted"] is False
    assert "board" in result["reason"].lower()
    assert service.status(0)["captures"] == 0


def test_add_capture_accepts_a_board_and_scores_coverage(service, board_img) -> None:
    left = _view(board_img, 1280, 720, [[60, 150], [520, 130], [520, 540], [60, 560]])
    result = service.add_capture(0, left)
    assert result["accepted"] is True
    assert result["corners"] >= 6
    assert "left" in result["buckets"]
    assert service.status(0)["captures"] == 1


def test_status_unions_coverage_and_gates_compute(service, board_img) -> None:
    st = service.status(0)
    assert st["captures"] == 0 and st["ready"] is False and st["calibrated"] is False

    for view in _varied_views(board_img, 4):
        service.add_capture(0, view)
    st = service.status(0)
    assert st["captures"] == 4
    assert st["ready"] is False               # still under MIN_VIEWS_TO_COMPUTE
    assert any(st["coverage"].values())       # some buckets covered
    with pytest.raises(ValueError, match="at least"):
        service.compute(0)


def test_compute_produces_and_saves_intrinsics(service, board_img, tmp_path) -> None:
    for view in _varied_views(board_img, max(MIN_VIEWS_TO_COMPUTE, 10)):
        service.add_capture(0, view)
    assert service.status(0)["ready"] is True

    result = service.compute(0)
    assert result["camera_id"] == 0
    assert np.asarray(result["camera_matrix"]).shape == (3, 3)
    assert result["rms_error"] >= 0.0
    assert result["views_used"] >= MIN_VIEWS_TO_COMPUTE

    # Persisted to intrinsics_<id>.json (the Slice 1 storage layout) and loadable.
    assert (tmp_path / "intrinsics_0.json").exists()
    saved = service.load_saved(0)
    assert saved is not None and saved["type"] == "intrinsics"
    assert service.status(0)["calibrated"] is True


def test_clear_captures_removes_the_working_set(service, board_img) -> None:
    for view in _varied_views(board_img, 3):
        service.add_capture(0, view)
    assert service.status(0)["captures"] == 3
    assert service.clear_captures(0) is True
    assert service.status(0)["captures"] == 0
    assert service.clear_captures(0) is False  # already gone
