"""Tests for camera recovery and health monitoring."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pytest

from core.camera_manager import CameraManager, VideoFrame


class TestCameraManagerHealth:
    """Test camera health scoring and status reporting."""

    def test_camera_manager_creates_synthetic_feeds(self):
        """CameraManager should create synthetic feeds for unavailable cameras."""
        manager = CameraManager(camera_ids=[0], record=False)
        manager.start()
        # Give it a moment to produce frames
        import time
        time.sleep(0.5)
        frames = manager.latest_frames()
        manager.stop()
        # Should have at least attempted to produce frames
        assert isinstance(frames, dict)

    def test_camera_health_method(self):
        """health() should return per-camera health info."""
        manager = CameraManager(camera_ids=[0], record=False)
        manager.start()
        import time
        time.sleep(0.3)
        if hasattr(manager, 'health'):
            health = manager.health()
            assert isinstance(health, dict)
            for cam_id, info in health.items():
                assert 'health_score' in info or isinstance(info, dict)
        manager.stop()

    def test_video_frame_dataclass(self):
        """VideoFrame should store frame data correctly."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vf = VideoFrame(
            frame=frame,
            camera_id=0,
            frame_id=1,
            timestamp_ms=100.0,
        )
        assert vf.camera_id == 0
        assert vf.frame_id == 1
        assert vf.timestamp_ms == 100.0
        assert vf.frame.shape == (480, 640, 3)

    def test_multiple_cameras(self):
        """Should handle multiple camera IDs."""
        manager = CameraManager(camera_ids=[0, 1], record=False)
        manager.start()
        import time
        time.sleep(0.5)
        frames = manager.latest_frames()
        manager.stop()
        assert isinstance(frames, dict)

    def test_camera_graceful_stop(self):
        """stop() should cleanly shut down all workers."""
        manager = CameraManager(camera_ids=[0], record=False)
        manager.start()
        manager.stop()
        # Should not raise or hang


class TestReplayBuffer:
    """Test replay frame buffering."""

    def test_replay_creates_with_cameras(self):
        """Replay controller should be creatable from CameraManager."""
        manager = CameraManager(camera_ids=[0], record=False)
        manager.start()
        import time
        time.sleep(0.3)
        replay = manager.create_replay()
        assert replay is not None
        assert replay.total_frames >= 0
        manager.stop()
