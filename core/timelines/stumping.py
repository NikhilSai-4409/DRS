"""Stumping timeline: freeze → UltraEdge → foot → crease → bails → verdict.

Leads with the UltraEdge check (distinct from Run Out), then foot vs crease. The
camera zooms early and runs a slow-motion pass over the crease moment.
"""

from __future__ import annotations

from core.timelines.base import ReviewTimeline


class StumpingTimeline(ReviewTimeline):
    key = "stumping"
    duration = 5.2
    theme = "stumping"
    verdict = (4.8, 0.3)
    cards_start = 3.8
    cues = {"ultraedge": (0.9, 0.5), "foot": (1.6, 0.6), "crease": (2.4, 0.5), "bails": (3.0, 0.4)}
    camera = (("freeze", 0.0, 0.4), ("zoom", 0.5, 0.6), ("slowmo", 2.4, 1.6))
