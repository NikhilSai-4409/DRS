"""Intrinsics API endpoints (Slice 2) — the thin layer over IntrinsicsCalibrationService.

The only feed-dependent route (/capture → backend.latest_frame) is exercised by
monkeypatching latest_frame to return generated ChArUco frames, so the whole HTTP
surface is verified without a camera.
"""

from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from core.api_server import create_app
from core.calibration import MultiCameraCalibrator


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("core.api_server.SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr("core.api_server.MATCHES_DIR", tmp_path / "matches")
    monkeypatch.setattr("core.calibration.CALIBRATION_DIR", tmp_path)


_BOARD = MultiCameraCalibrator().board.generateImage((1000, 700), marginSize=0, borderBits=1)
_QUADS = [
    [[340, 120], [940, 120], [940, 560], [340, 560]],
    [[80, 140], [560, 120], [560, 540], [80, 560]],
    [[720, 120], [1200, 140], [1200, 560], [720, 540]],
    [[480, 260], [800, 260], [800, 480], [480, 480]],
    [[340, 140], [960, 100], [900, 560], [400, 600]],
    [[300, 100], [900, 160], [960, 600], [260, 540]],
    [[200, 200], [780, 160], [820, 600], [240, 560]],
    [[520, 160], [1120, 200], [1080, 620], [560, 580]],
    [[360, 200], [880, 180], [900, 520], [380, 540]],
    [[420, 120], [1020, 160], [980, 600], [440, 560]],
]


def _view(i: int) -> np.ndarray:
    src = np.float32([[0, 0], [1000, 0], [1000, 700], [0, 700]])
    M = cv2.getPerspectiveTransform(src, np.float32(_QUADS[i % len(_QUADS)]))
    warped = cv2.warpPerspective(_BOARD, M, (1280, 720), borderValue=255)
    return cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)


def _app_with_frames(tmp_path, frames="board"):
    app = create_app([0], record=False)
    app.state.intrinsics_service.base = tmp_path
    counter = {"i": 0}
    if frames == "none":
        def _latest(_cid):
            raise KeyError(_cid)
    else:
        def _latest(_cid):
            v = _view(counter["i"]); counter["i"] += 1
            return SimpleNamespace(frame=v)
    app.state.backend.latest_frame = _latest
    return app


@pytest.mark.asyncio
async def test_capture_without_live_frame_returns_409(tmp_path):
    app = _app_with_frames(tmp_path, frames="none")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/calibration/intrinsics/0/capture")
    assert r.status_code == 409
    assert "no live frame" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_status_shape_is_stable_and_stateless(tmp_path):
    app = _app_with_frames(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        st = (await client.get("/api/calibration/intrinsics/0/status")).json()
    assert st["camera_id"] == 0
    assert st["captures"] == 0
    assert set(st["coverage"].keys()) == {"left", "center", "right", "near", "far", "tilted"}
    assert st["ready"] is False and st["calibrated"] is False


@pytest.mark.asyncio
async def test_capture_compute_inspect_clear_flow(tmp_path):
    app = _app_with_frames(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Not enough views yet → compute is a clean 422.
        assert (await client.post("/api/calibration/intrinsics/0/compute")).status_code == 422

        for _ in range(10):
            cap = await client.post("/api/calibration/intrinsics/0/capture")
            assert cap.status_code == 200 and cap.json()["accepted"] is True
        status = (await client.get("/api/calibration/intrinsics/0/status")).json()
        assert status["captures"] == 10 and status["ready"] is True

        compute = await client.post("/api/calibration/intrinsics/0/compute")
        assert compute.status_code == 200
        assert np.asarray(compute.json()["camera_matrix"]).shape == (3, 3)

        inspect = await client.get("/api/calibration/intrinsics/0")
        assert inspect.status_code == 200 and inspect.json()["type"] == "intrinsics"

        cleared = await client.post("/api/calibration/intrinsics/0/clear")
        assert cleared.json()["cleared"] is True
        assert (await client.get("/api/calibration/intrinsics/0/status")).json()["captures"] == 0


@pytest.mark.asyncio
async def test_inspect_missing_returns_404(tmp_path):
    app = _app_with_frames(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/calibration/intrinsics/7")
    assert r.status_code == 404
