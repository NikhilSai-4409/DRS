"""Tests for the match replay ring buffer."""

from __future__ import annotations

import numpy as np

from core.replay_buffer import ReplayBuffer


def _frame() -> np.ndarray:
    return np.zeros((2, 2, 3), dtype=np.uint8)


def test_capacity_and_eviction():
    buffer = ReplayBuffer(seconds=1.0, fps=10)  # capacity 10
    for i in range(25):
        buffer.add(_frame(), timestamp_ms=i * 100.0, camera_id=0)
    assert len(buffer) == 10
    assert buffer.latest().timestamp_ms == 2400.0


def test_window_around_timestamp():
    buffer = ReplayBuffer(seconds=10.0, fps=10)
    for i in range(100):
        buffer.add(_frame(), timestamp_ms=i * 100.0)
    window = buffer.window(5000.0, half_window_ms=300.0)
    timestamps = sorted(frame.timestamp_ms for frame in window)
    assert timestamps == [4700.0, 4800.0, 4900.0, 5000.0, 5100.0, 5200.0, 5300.0]


def test_clip_and_camera_filter():
    buffer = ReplayBuffer(seconds=10.0, fps=10)
    for i in range(20):
        buffer.add(_frame(), timestamp_ms=i * 100.0, camera_id=i % 2)
    clip = buffer.clip(0.0, 1000.0, camera_id=1)
    assert clip and all(frame.camera_id == 1 for frame in clip)


def test_duration_and_clear():
    buffer = ReplayBuffer(seconds=10.0, fps=10)
    for i in range(5):
        buffer.add(_frame(), timestamp_ms=i * 100.0)
    assert buffer.duration_ms == 400.0
    buffer.clear()
    assert len(buffer) == 0
    assert buffer.latest() is None
