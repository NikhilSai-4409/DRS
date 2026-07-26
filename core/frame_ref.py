"""Frame identity — which moment, in which coordinate system, from which camera.

Three integers named ``frame_id`` circulate in this system and mean three
different things:

* a **capture** index — ``VideoFrame.frame_id``, a per-camera monotonic counter
  (``no_ball_analysis.landing_frame_id``, ``run_out_analysis.frame_number``);
* a **clip** index — ``0..total_frames-1`` inside a frozen replay window
  (UltraEdge ``events[].frame_id``, the workspace scrubber position);
* and, historically, a synthesised array position where the real id had been
  dropped entirely.

Comparing across those spaces silently produces an overlay drawn on the wrong
moment, which is misleading evidence rather than a cosmetic bug. Two capture
indices from *different cameras* are equally incomparable: camera 0 frame 194 and
camera 1 frame 194 are not the same instant.

So a frame reference always carries its space and its source, and code asks
``is_same_moment`` rather than comparing integers. ``timestamp_ms`` is the only
value that means the same thing everywhere, which makes it the join key when two
surfaces (waveform, replay, overlay) must be synchronised.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FrameSpace(str, Enum):
    CAPTURE = "capture"   # per-camera VideoFrame.frame_id counter
    CLIP = "clip"         # 0..total_frames-1 within one frozen replay window


class FrameSpaceMismatch(ValueError):
    """Raised when two frame references from different spaces or sources are
    compared. Loud by design: the alternative is an overlay on the wrong frame."""


@dataclass(frozen=True, slots=True)
class FrameRef:
    space: FrameSpace
    index: int
    timestamp_ms: float | None = None
    # Which camera / buffer produced the index. Capture indices are only
    # comparable within one source; clip indices only within one replay window.
    source: str | None = None

    @classmethod
    def capture(cls, index: int, timestamp_ms: float | None = None,
                camera_id: int | None = None) -> "FrameRef":
        """A missing timestamp is a real, degraded state — some capture paths do not
        carry one. Build the reference anyway and let the contract validator report
        it, rather than raising and costing the operator a review."""
        return cls(FrameSpace.CAPTURE, int(index),
                   None if timestamp_ms is None else float(timestamp_ms),
                   None if camera_id is None else f"camera:{camera_id}")

    @classmethod
    def clip(cls, index: int, timestamp_ms: float | None = None, window: str | None = None) -> "FrameRef":
        return cls(FrameSpace.CLIP, int(index),
                   None if timestamp_ms is None else float(timestamp_ms), window)

    @classmethod
    def coerce(cls, value) -> "FrameRef | None":
        if value is None or isinstance(value, cls):
            return value if isinstance(value, cls) else None
        if isinstance(value, dict):
            try:
                return cls(FrameSpace(value["space"]), int(value["index"]),
                           value.get("timestamp_ms"), value.get("source"))
            except (KeyError, ValueError, TypeError):
                return None
        return None

    def to_dict(self) -> dict:
        return {"space": self.space.value, "index": self.index,
                "timestamp_ms": self.timestamp_ms, "source": self.source}

    def comparable_with(self, other: "FrameRef") -> bool:
        return self.space is other.space and self.source == other.source

    def is_same_moment(self, other: "FrameRef") -> bool:
        """Index equality, but only where indices mean the same thing. Raises
        rather than returning False for an incomparable pair — a quiet False would
        read as "a different moment" when the truth is "unanswerable"."""
        if not self.comparable_with(other):
            raise FrameSpaceMismatch(
                f"cannot compare {self.space.value}/{self.source} with "
                f"{other.space.value}/{other.source} — join on timestamp_ms instead"
            )
        return self.index == other.index
