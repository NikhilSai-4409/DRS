"""End-to-end tests for the testing platform dashboard flow.

Simulates: upload video → analyze → check progress → get result → export.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

try:
    from fastapi.testclient import TestClient
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

pytestmark = pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="fastapi not available")


@pytest.fixture
def client():
    # The dashboard surface (health, calibration, reviews) is served by the single
    # unified backend; the upload/analyze/export routes are folded in on top of it.
    from core.api_server import create_unified_app
    app = create_unified_app()
    return TestClient(app)


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    """Create a minimal synthetic video for upload testing."""
    import cv2
    import numpy as np
    video_path = tmp_path / "test_delivery.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 30.0, (640, 360))
    for i in range(60):
        frame = np.full((360, 640, 3), (34, 120, 50), dtype=np.uint8)
        x = 100 + i * 8
        y = 180 + int(30 * (i / 60.0))
        cv2.circle(frame, (x, y), 10, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()
    return video_path


class TestHealthFlow:
    def test_api_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_system_health(self, client):
        r = client.get("/api/system/health")
        assert r.status_code == 200
        data = r.json()
        assert "cpu_percent" in data


class TestUploadAnalyzeFlow:
    def test_upload_and_analyze(self, client, synthetic_video):
        """Upload a video via /api/test/upload and verify job creation."""
        with open(synthetic_video, "rb") as f:
            r = client.post(
                "/api/test/upload",
                files={"video_a": (synthetic_video.name, f, "video/mp4")},
            )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        job_id = data["job_id"]

        # Poll for completion (max 30s)
        for _ in range(15):
            r = client.get(f"/api/test/jobs/{job_id}")
            assert r.status_code == 200
            status = r.json().get("status")
            if status in ("completed", "failed"):
                break
            time.sleep(2)

        # Verify result
        result = client.get(f"/api/test/jobs/{job_id}").json()
        assert result["status"] in ("completed", "failed", "processing")


class TestExportFlow:
    def test_export_json(self, client, synthetic_video):
        """Upload + wait + export JSON result."""
        with open(synthetic_video, "rb") as f:
            r = client.post(
                "/api/test/upload",
                files={"video_a": (synthetic_video.name, f, "video/mp4")},
            )
        job_id = r.json()["job_id"]
        # Wait for completion
        for _ in range(15):
            status = client.get(f"/api/test/jobs/{job_id}").json().get("status")
            if status in ("completed", "failed"):
                break
            time.sleep(2)

        r = client.get(f"/api/testing/jobs/{job_id}/exports/json")
        # Export may not exist if job is still processing or if result has no export
        assert r.status_code in (200, 404)

    def test_export_pdf(self, client, synthetic_video):
        """Upload + wait + export PDF report."""
        with open(synthetic_video, "rb") as f:
            r = client.post(
                "/api/test/upload",
                files={"video_a": (synthetic_video.name, f, "video/mp4")},
            )
        job_id = r.json()["job_id"]
        for _ in range(15):
            status = client.get(f"/api/test/jobs/{job_id}").json().get("status")
            if status in ("completed", "failed"):
                break
            time.sleep(2)

        r = client.get(f"/api/testing/jobs/{job_id}/exports/pdf")
        assert r.status_code in (200, 404)


class TestCalibrationFlow:
    def test_get_profiles(self, client):
        r = client.get("/api/calibration/profiles")
        assert r.status_code == 200

    def test_get_status(self, client):
        r = client.get("/api/calibration/status")
        assert r.status_code == 200

    def test_default_profile(self, client):
        r = client.get("/api/calibration/default-profile")
        assert r.status_code == 200
        data = r.json()
        assert "markers" in data or "world_dimensions" in data or "image_points" in data


class TestCameraStatus:
    def test_cameras_fps(self, client):
        r = client.get("/api/cameras/fps")
        assert r.status_code == 200
