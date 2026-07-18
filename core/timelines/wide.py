"""Wide timeline: freeze → zoom → ball → wide line → distance → cards → verdict."""

from __future__ import annotations

from core.timelines.base import ReviewTimeline


class WideTimeline(ReviewTimeline):
    key = "wide"
    duration = 4.4
    theme = "wide"
    verdict = (4.0, 0.3)
    cards_start = 3.2
    cues = {"ball": (1.0, 0.6), "wideline": (1.8, 0.5), "distance": (2.6, 0.4)}
    camera = (("freeze", 0.0, 0.4), ("zoom", 0.4, 0.6))
