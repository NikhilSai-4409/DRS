"""Tests for OverlayRenderer, the thin ReplayBuilder, and ReviewLogger."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from core.overlay_renderer import OverlayRenderer
from core.replay_builder import ReplayBuilder
from core.review_logger import ReviewLogger


def _frame(ts: float):
    img = np.full((180, 320, 3), (40, 90, 60), dtype=np.uint8)
    return SimpleNamespace(frame=img, frame_id=int(ts), timestamp_ms=float(ts), camera_id=0)


# A render-ready OverlayPayload (what core.overlay_builder produces).
PAYLOAD = {
    "review_type": "lbw",
    "verdict": "OUT",
    "confidence": 0.94,
    "measurements": [{"label": "Impact", "value": "in line"}],
    "measured_px": [[80, 40, 0], [120, 70, 0], [150, 100, 0]],
    "predicted_px": [[150, 100, 0], [180, 120, 0]],
    "shadow_px": [[80, 150], [120, 150], [150, 150]],
    "bounce_px": {"x": 120, "y": 70},
    "impact_px": {"x": 150, "y": 100},
    "stumps_px": [{"x": 190, "y": 90}, {"x": 198, "y": 88}],
    "hitting": True,
    "decision_cards": [{"label": "WICKETS", "value": "HITTING", "status": "out"}],
}


def test_overlay_renderer_preserves_size_and_does_not_mutate():
    src = _frame(0).frame
    out = OverlayRenderer().render(src, PAYLOAD, 0.9)   # float progress or a director state dict
    assert out.shape == src.shape
    assert not np.array_equal(out, src)          # overlay was drawn
    assert src[0, 0].tolist() == [40, 90, 60]    # original untouched (copy)


def test_replaybuilder_delegates_to_renderer_and_writes_mp4(tmp_path):
    frames = [_frame(i * 16.0) for i in range(8)]
    out = tmp_path / "replay.mp4"
    meta = ReplayBuilder(fps=10).build(frames, PAYLOAD, out)
    assert meta["available"] is True
    assert meta["frame_count"] == 8
    assert out.exists() and out.stat().st_size > 0


def test_build_with_no_frames_is_honest(tmp_path):
    meta = ReplayBuilder().build([], PAYLOAD, tmp_path / "x.mp4")
    assert meta["available"] is False
    assert "No frames" in meta["reason"]


def test_review_logger_writes_folder_and_renders_overlay(tmp_path):
    decision = {
        "review_type": "lbw",
        "status": "PROCESSING",
        "review_result": {"review_type": "lbw", "verdict": "OUT", "confidence": 0.94,
                          "measurements": PAYLOAD["measurements"]},
        "overlay": PAYLOAD,
        "camera_sync": {"in_sync": True, "max_offset_ms": 2.0},
        "trajectory": [{"x": 0.1, "y": 0.0, "z": 0.3}],
        "edge_analysis": None,
    }
    logger = ReviewLogger(root=tmp_path / "reviews")
    frames = [_frame(i * 16.0) for i in range(6)]
    result = logger.log(decision, frames=frames, calibration={"ready": True},
                        frame_timestamps={0: [0.0, 16.0]})

    assert result["saved"] is True
    review_dir = tmp_path / "reviews" / result["review_id"]
    assert (review_dir / "review.json").exists()
    assert (review_dir / "trajectory.json").exists()
    assert (review_dir / "logs.txt").exists()
    assert (review_dir / "frames").is_dir()
    assert result["replay"]["available"] is True
    assert (review_dir / "replay.mp4").exists()


def test_review_logger_increments_ids(tmp_path):
    logger = ReviewLogger(root=tmp_path / "reviews")
    decision = {"review_type": "lbw", "status": "PROCESSING", "review_result": {"verdict": "MISSING"}}
    first = logger.log(decision, frames=None, save_replay=False)
    second = logger.log(decision, frames=None, save_replay=False)
    assert first["review_id"] == "Review_001"
    assert second["review_id"] == "Review_002"


def test_review_logger_edge_writes_ultraedge(tmp_path):
    decision = {
        "review_type": "edge", "status": "PROCESSING",
        "review_result": {"verdict": "INCONCLUSIVE", "warnings": []},
        "edge_analysis": {"edge_probability": 0.0, "inconclusive": True, "events": []},
    }
    logger = ReviewLogger(root=tmp_path / "reviews")
    result = logger.log(decision, frames=None, save_replay=False)
    assert "ultraedge.json" in result["artifacts"]
    assert (tmp_path / "reviews" / result["review_id"] / "ultraedge.json").exists()
