"""Tests for the synchronized FrameBuffer that feeds every review module."""

from __future__ import annotations

import time
from types import SimpleNamespace

from core.frame_buffer import FrameBuffer, SynchronizedFrames


def _frame(ts: float):
    return SimpleNamespace(frame=None, frame_id=int(ts), timestamp_ms=float(ts))


class _Worker:
    def __init__(self, frames, fps=50.0, synthetic=False):
        self._frames = frames
        self.fps_actual = fps
        self.dropped_queue_frames = 2
        self.synthetic = synthetic
        self.reconnect_attempts = 1

    def snapshot(self):
        return list(self._frames)


class _Manager:
    def __init__(self, workers):
        self.workers = workers


def test_snapshot_aligns_to_latest_reference():
    now = time.time() * 1000.0
    mgr = _Manager({
        0: _Worker([_frame(now - 32), _frame(now - 16), _frame(now)]),       # latest = now
        1: _Worker([_frame(now - 37), _frame(now - 21), _frame(now - 5)]),    # 5 ms behind
    })
    snap = FrameBuffer(mgr).snapshot()

    assert isinstance(snap, SynchronizedFrames)
    assert snap.reference_timestamp_ms == now
    assert snap.camera_ids == [0, 1]
    # Reference camera has zero offset; the lagging camera is 5 ms behind.
    assert snap.telemetry[0].sync_offset_ms == 0.0
    assert snap.telemetry[1].sync_offset_ms == -5.0
    assert snap.telemetry[0].frame_count == 3
    assert snap.telemetry[0].connected is True
    assert snap.timestamps[0][-1] == now
    # 5 ms spread is within the default 8 ms tolerance -> in sync.
    assert snap.max_offset_ms == 5.0
    assert snap.in_sync is True


def test_snapshot_reports_out_of_sync_when_spread_exceeds_tolerance():
    now = time.time() * 1000.0
    mgr = _Manager({
        0: _Worker([_frame(now)]),
        1: _Worker([_frame(now - 40)]),   # 40 ms behind > 8 ms tolerance
    })
    snap = FrameBuffer(mgr).snapshot()
    assert snap.in_sync is False
    assert snap.max_offset_ms == 40.0
    report = snap.sync_report()
    assert report["in_sync"] is False
    assert report["tolerance_ms"] == snap.sync_tolerance_ms
    assert set(report["cameras"].keys()) == {0, 1}


def test_snapshot_handles_empty_and_synthetic_cameras():
    mgr = _Manager({
        0: _Worker([], fps=0.0),                                  # no frames
        1: _Worker([_frame(time.time() * 1000.0)], synthetic=True),
    })
    snap = FrameBuffer(mgr).snapshot()
    assert snap.telemetry[0].connected is False
    assert snap.telemetry[0].health_score == 0.0
    assert snap.telemetry[0].last_frame_age_ms == -1.0
    assert snap.telemetry[1].synthetic is True


def test_snapshot_with_no_cameras_is_safe():
    snap = FrameBuffer(_Manager({})).snapshot()
    assert snap.reference_timestamp_ms is None
    assert snap.camera_ids == []
    assert snap.in_sync is True
    assert snap.sync_report()["max_offset_ms"] == 0.0
