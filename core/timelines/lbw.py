"""LBW timeline: freeze → zoom → trajectory → bounce → impact → prediction → stumps → cards → verdict."""

from __future__ import annotations

from core.timelines.base import ReviewTimeline


class LbwTimeline(ReviewTimeline):
    key = "lbw"
    duration = 4.9
    theme = "lbw"
    verdict = (4.5, 0.3)
    cards_start = 3.3
    cues = {"measured": (1.0, 1.2), "bounce": (1.8, 0.12), "impact": (2.2, 0.2),
            "predicted": (2.3, 0.8), "stumps": (3.1, 0.3)}
    camera = (("freeze", 0.0, 0.4), ("zoom", 0.4, 0.6))
