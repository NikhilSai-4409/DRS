"""Synchronized multi-camera frame buffer — the single source of frames every
review module consumes.

:class:`CameraManager` keeps an independent ring buffer per camera worker. Before
this, each review module pulled ``worker.snapshot()`` on its own, so two modules
could analyse slightly different moments of the same delivery. :class:`FrameBuffer`
takes ONE synchronized snapshot of every camera at appeal time — the same buffers,
but aligned to a common reference timestamp and carrying per-camera sync/health
telemetry. A :class:`ReviewContext` is built from exactly one
:class:`SynchronizedFrames`, which is what makes a review deterministic:

    Camera ──> FrameBuffer ──> SynchronizedFrames ──> ReviewContext ──> {LBW, Wide, NoBall, Edge}
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from config.settings import SYNC_TOLERANCE_MS
from utils.logger import get_logger

log = get_logger("frame_buffer")


@dataclass(frozen=True, slots=True)
class CameraTelemetry:
    """Per-camera health + synchronization at the instant of one snapshot."""

    camera_id: int
    fps: float
    frame_count: int
    dropped_frames: int
    last_frame_age_ms: float          # -1 when the camera has produced no frames
    sync_offset_ms: float             # signed offset of this camera vs the reference
    synthetic: bool
    reconnect_attempts: int
    health_score: float               # 0.0 dead .. 1.0 perfect
    connected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "dropped_frames": self.dropped_frames,
            "last_frame_age_ms": self.last_frame_age_ms,
            "sync_offset_ms": self.sync_offset_ms,
            "synthetic": self.synthetic,
            "reconnect_attempts": self.reconnect_attempts,
            "health_score": self.health_score,
            "connected": self.connected,
        }


@dataclass(frozen=True, slots=True)
class SynchronizedFrames:
    """An immutable, synchronized view of every camera's replay buffer."""

    reference_timestamp_ms: Optional[float]
    frames: dict[int, list]                 # camera_id -> list[VideoFrame] (chronological)
    timestamps: dict[int, list[float]]      # camera_id -> [timestamp_ms ...]
    telemetry: dict[int, CameraTelemetry]
    sync_tolerance_ms: float = SYNC_TOLERANCE_MS

    @property
    def camera_ids(self) -> list[int]:
        return sorted(self.frames.keys())

    @property
    def max_offset_ms(self) -> float:
        offsets = [abs(item.sync_offset_ms) for item in self.telemetry.values()]
        return max(offsets) if offsets else 0.0

    @property
    def in_sync(self) -> bool:
        offsets = [abs(item.sync_offset_ms) for item in self.telemetry.values()]
        return all(offset <= self.sync_tolerance_ms for offset in offsets) if offsets else True

    def telemetry_dict(self) -> dict[int, dict]:
        return {camera_id: item.to_dict() for camera_id, item in self.telemetry.items()}

    def sync_report(self) -> dict:
        """Compact sync summary for the operator dashboard / decision payload."""
        return {
            "reference_timestamp_ms": self.reference_timestamp_ms,
            "in_sync": self.in_sync,
            "max_offset_ms": round(self.max_offset_ms, 2),
            "tolerance_ms": self.sync_tolerance_ms,
            "cameras": self.telemetry_dict(),
        }


class FrameBuffer:
    """Produces synchronized snapshots over a live :class:`CameraManager`."""

    def __init__(self, camera_manager: Any, sync_tolerance_ms: float = SYNC_TOLERANCE_MS):
        self.camera_manager = camera_manager
        self.sync_tolerance_ms = float(sync_tolerance_ms)

    @staticmethod
    def _now_ms() -> float:
        return time.time() * 1000.0

    @staticmethod
    def _health_score(fps: float, connected: bool, synthetic: bool) -> float:
        if not connected:
            return 0.0
        score = min(1.0, fps / 24.0) * 0.65 + 0.35
        if synthetic:
            score *= 0.6
        return max(0.0, min(1.0, score))

    def snapshot(self) -> SynchronizedFrames:
        """Capture every camera's buffer aligned to a common reference instant."""
        workers = getattr(self.camera_manager, "workers", {}) or {}
        raw: dict[int, list] = {}
        for camera_id, worker in workers.items():
            try:
                raw[camera_id] = list(worker.snapshot())
            except Exception as exc:  # a flaky camera must never break a review
                log.warning("FrameBuffer snapshot failed for cam {}: {}", camera_id, exc)
                raw[camera_id] = []

        # Reference = most recent frame captured across all cameras (the appeal instant).
        latest_per_cam = {
            camera_id: (frames[-1].timestamp_ms if frames else None)
            for camera_id, frames in raw.items()
        }
        present = [ts for ts in latest_per_cam.values() if ts is not None]
        reference = max(present) if present else None
        now_ms = self._now_ms()

        frames_out: dict[int, list] = {}
        timestamps: dict[int, list[float]] = {}
        telemetry: dict[int, CameraTelemetry] = {}
        for camera_id, frames in raw.items():
            worker = workers[camera_id]
            last_ts = latest_per_cam[camera_id]
            offset = (last_ts - reference) if (last_ts is not None and reference is not None) else 0.0
            age = (now_ms - last_ts) if last_ts is not None else float("inf")
            fps = float(getattr(worker, "fps_actual", 0.0))
            connected = last_ts is not None and age < 2000.0
            synthetic = bool(getattr(worker, "synthetic", False))

            frames_out[camera_id] = frames
            timestamps[camera_id] = [float(getattr(item, "timestamp_ms", 0.0)) for item in frames]
            telemetry[camera_id] = CameraTelemetry(
                camera_id=camera_id,
                fps=round(fps, 2),
                frame_count=len(frames),
                dropped_frames=int(getattr(worker, "dropped_queue_frames", 0)),
                last_frame_age_ms=round(age, 1) if age != float("inf") else -1.0,
                sync_offset_ms=round(offset, 2),
                synthetic=synthetic,
                reconnect_attempts=int(getattr(worker, "reconnect_attempts", 0)),
                health_score=round(self._health_score(fps, connected, synthetic), 3),
                connected=connected,
            )

        return SynchronizedFrames(
            reference_timestamp_ms=reference,
            frames=frames_out,
            timestamps=timestamps,
            telemetry=telemetry,
            sync_tolerance_ms=self.sync_tolerance_ms,
        )
