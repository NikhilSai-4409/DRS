"""Edge (UltraEdge) timeline: freeze → zoom → UltraEdge → HotSpot → cards → verdict."""

from __future__ import annotations

from core.timelines.base import ReviewTimeline


class EdgeTimeline(ReviewTimeline):
    key = "edge"
    duration = 4.0
    theme = "edge"
    verdict = (3.6, 0.3)
    cards_start = 2.8
    cues = {"ultraedge": (1.0, 0.8), "hotspot": (2.0, 0.6)}
    camera = (("freeze", 0.0, 0.4), ("zoom", 0.4, 0.6))
