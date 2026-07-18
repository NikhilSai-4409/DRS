import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from core.ball_detector import BALL_CLASS_IDS, BallDetector
from core import testing_api
from core.testing_api import create_testing_app


def _write_synthetic_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (320, 180))
    for frame_id in range(10):
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        cv2.circle(frame, (40 + frame_id * 18, 80), 6, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


async def _upload_completed_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(testing_api.pipeline.detector, "model", None)
    app = create_testing_app()
    video_path = tmp_path / "delivery.mp4"
    _write_synthetic_video(video_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with video_path.open("rb") as handle:
            response = await client.post(
                "/api/test/upload",
                files={"video_a": ("delivery.mp4", handle, "video/mp4")},
                data={"options_json": json.dumps({"max_frames": 10})},
            )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        deadline = asyncio.get_running_loop().time() + 60
        while asyncio.get_running_loop().time() < deadline:
            job_response = await client.get(f"/api/test/jobs/{job_id}")
            assert job_response.status_code == 200
            job = job_response.json()
            if job["status"] in {"completed", "review_inconclusive", "failed"}:
                return job
            await asyncio.sleep(2)
    pytest.fail("Timed out waiting for upload job completion")


@pytest.mark.asyncio
async def test_upload_job_completion(tmp_path, monkeypatch):
    job = await _upload_completed_job(tmp_path, monkeypatch)
    assert job["status"] in {"completed", "review_inconclusive"}


@pytest.mark.asyncio
async def test_export_files_exist(tmp_path, monkeypatch):
    job = await _upload_completed_job(tmp_path, monkeypatch)
    output_dir = Path(job["output_dir"])
    # These four are written for every completed job. The old bbox-normalized
    # animation.mp4 was replaced by the broadcast replay package (replay_review.mp4),
    # which is only rendered when a ball trajectory is reconstructed — this job runs
    # with the detector disabled, so no trajectory and no replay render is expected.
    for filename in ["analyzed_video.mp4", "results.json", "results.csv", "report.pdf"]:
        assert (output_dir / filename).exists(), f"missing export: {filename}"


@pytest.mark.asyncio
async def test_stale_job_cleanup():
    app = create_testing_app()
    job_id = uuid.uuid4().hex[:12]
    old_ms = (datetime.now() - timedelta(minutes=20)).timestamp() * 1000.0
    testing_api.db.create_job(job_id, "single_camera", {}, Path("fake.mp4"), None)
    testing_api.db.update_job(job_id, "processing")
    with testing_api.db.connect() as conn:
        conn.execute("UPDATE analysis_jobs SET updated_at_ms = ? WHERE id = ?", (old_ms, job_id))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/jobs/cleanup-stale")
    assert response.status_code == 200
    assert job_id in response.json()["job_ids"]
    assert testing_api.db.get_job(job_id)["status"] == "failed"


def test_class_filter_active():
    assert BALL_CLASS_IDS
    detector = BallDetector.__new__(BallDetector)
    detector.ball_class_ids = set(BALL_CLASS_IDS)
    box = SimpleNamespace(cls=[1], conf=[0.9], xyxy=[np.array([1, 2, 3, 4])])
    accepted = [item for item in [box] if detector._is_ball_class(item)]
    assert accepted == []
