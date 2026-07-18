"""No Ball timeline: freeze → zoom → crease → front foot → distance → cards → verdict."""

from __future__ import annotations

from core.timelines.base import ReviewTimeline


class NoBallTimeline(ReviewTimeline):
    key = "noball"
    duration = 4.4
    theme = "noball"
    verdict = (4.0, 0.3)
    cards_start = 3.2
    cues = {"crease": (0.9, 0.5), "foot": (1.6, 0.6), "distance": (2.6, 0.4)}
    camera = (("freeze", 0.0, 0.4), ("zoom", 0.4, 0.6))
