"""Per-review-type timelines.

Each :class:`ReviewTimeline` isolates ONE review's timing in its own class:
  * ``cues``   — overlay reveal cues consumed by AnimationDirector
  * ``camera`` — the camera script consumed by CameraDirector
  * ``theme``  — the review's colour identity

Directors hold NO per-type data — they read a timeline chosen by
``core.timelines.timeline_for`` (the factory). Tuning one review's pacing or camera
work means editing one small class, and the renderer never contains review logic.
"""

from __future__ import annotations


class ReviewTimeline:
    key: str = "base"
    duration: float = 4.5
    theme: str = "broadcast"
    verdict: tuple = (4.2, 0.3)          # overlay: verdict reveal (start_s, ramp_s)
    cards_start: float = 3.2             # overlay: first decision card (then +0.32 each)
    cues: dict = {}                      # overlay reveals: key -> (start_s, ramp_s)
    # camera script: (action, start_s, dur_s); action in
    # {"freeze", "zoom", "framestep", "slowmo", "pan"}
    camera: tuple = (("freeze", 0.0, 0.4), ("zoom", 0.4, 0.6))
