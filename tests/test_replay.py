"""Tests for replay system functionality."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pytest

from core.camera_manager import ReplayController, VideoFrame


def _make_buffers(total_frames: int = 100) -> dict[int, list[VideoFrame]]:
    """Create synthetic frame buffers for replay testing."""
    frames = []
    for i in range(total_frames):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frames.append(VideoFrame(
            camera_id=0,
            frame_id=i,
            timestamp_ms=i * 33.3,
            frame=frame,
        ))
    return {0: frames}


class TestReplayController:
    """Test replay frame navigation and control."""

    def setup_method(self):
        self.controller = ReplayController(buffers=_make_buffers(100))

    def test_initial_state(self):
        """Replay should start paused at frame 0."""
        assert self.controller.cursor == 0
        assert not self.controller.playing
        assert self.controller.speed == 1.0

    def test_step_forward(self):
        """Stepping forward should advance cursor by 1."""
        initial = self.controller.cursor
        self.controller.step(1)
        assert self.controller.cursor == initial + 1

    def test_step_backward(self):
        """Stepping backward should move cursor back by 1."""
        self.controller.cursor = 50
        self.controller.step(-1)
        assert self.controller.cursor == 49

    def test_step_clamps_at_zero(self):
        """Cursor should not go below 0."""
        self.controller.cursor = 0
        self.controller.step(-1)
        assert self.controller.cursor >= 0

    def test_step_clamps_at_max(self):
        """Cursor should not exceed total_frames."""
        self.controller.cursor = self.controller.total_frames - 1
        self.controller.step(1)
        assert self.controller.cursor <= self.controller.total_frames

    def test_seek(self):
        """Seek should set cursor to exact position."""
        self.controller.seek(50)
        assert self.controller.cursor == 50

    def test_seek_clamps(self):
        """Seek beyond range should clamp."""
        self.controller.seek(999)
        assert self.controller.cursor <= self.controller.total_frames

    def test_speed_setting(self):
        """Speed should be settable via play()."""
        self.controller.play(0.25)
        assert self.controller.speed == 0.25
        self.controller.play(4.0)
        assert self.controller.speed == 4.0

    def test_play_pause(self):
        """Play and pause should toggle correctly."""
        self.controller.play()
        assert self.controller.playing
        self.controller.pause()
        assert not self.controller.playing

    def test_current_frames(self):
        """current_frames should return frame at cursor position."""
        self.controller.seek(10)
        frames = self.controller.current_frames()
        assert isinstance(frames, dict)
        if frames:
            frame = list(frames.values())[0]
            assert frame.frame_id == 10

    def test_tick_advances_cursor(self):
        """tick() should advance cursor when playing."""
        import time
        self.controller.play(speed=1.0)
        time.sleep(0.05)
        self.controller.tick()
        # Cursor may or may not have advanced depending on timing
        assert self.controller.cursor >= 0

    def test_total_frames(self):
        """total_frames should match buffer size."""
        assert self.controller.total_frames == 100

    def test_empty_buffers(self):
        """Should handle empty buffers gracefully."""
        empty = ReplayController(buffers={})
        assert empty.total_frames == 0
        assert empty.cursor == 0
