"""Match replay buffer: a fixed-size ring of timestamped frames.

Holds the most recent N seconds of frames per the configured target FPS so any
delivery can be replayed or a clip extracted around an impact timestamp. This is
the storage layer the future broadcast-replay and live-decision stages build on.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from config.settings import BUFFER_SECONDS, TARGET_FPS


@dataclass(slots=True)
class ReplayFrame:
    timestamp_ms: float
    frame: np.ndarray
    camera_id: int = 0


class ReplayBuffer:
    """Ring buffer of recent frames, indexed by timestamp and camera."""

    def __init__(self, seconds: float = BUFFER_SECONDS, fps: int = TARGET_FPS) -> None:
        self.seconds = float(seconds)
        self.fps = int(fps)
        self.capacity = max(1, int(round(self.seconds * self.fps)))
        self._frames: deque[ReplayFrame] = deque(maxlen=self.capacity)

    def add(self, frame: np.ndarray, timestamp_ms: float, camera_id: int = 0) -> None:
        self._frames.append(ReplayFrame(float(timestamp_ms), frame, int(camera_id)))

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def duration_ms(self) -> float:
        if len(self._frames) < 2:
            return 0.0
        return self._frames[-1].timestamp_ms - self._frames[0].timestamp_ms

    def latest(self) -> Optional[ReplayFrame]:
        return self._frames[-1] if self._frames else None

    def window(
        self,
        center_timestamp_ms: float,
        half_window_ms: float = 1000.0,
        camera_id: int | None = None,
    ) -> list[ReplayFrame]:
        low = center_timestamp_ms - half_window_ms
        high = center_timestamp_ms + half_window_ms
        return [
            frame
            for frame in self._frames
            if low <= frame.timestamp_ms <= high and (camera_id is None or frame.camera_id == camera_id)
        ]

    def clip(self, start_ms: float, end_ms: float, camera_id: int | None = None) -> list[ReplayFrame]:
        return [
            frame
            for frame in self._frames
            if start_ms <= frame.timestamp_ms <= end_ms and (camera_id is None or frame.camera_id == camera_id)
        ]

    def clear(self) -> None:
        self._frames.clear()
