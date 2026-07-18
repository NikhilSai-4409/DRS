"""Timeline registry + factory.

    timeline = timeline_for(review_type)   # one isolated ReviewTimeline per review
    reveals  = AnimationDirector().state_at(payload, t)   # reads timeline.cues
    camera   = CameraDirector().state_at(payload, t)      # reads timeline.camera
"""

from __future__ import annotations

from core.timelines.base import ReviewTimeline
from core.timelines.edge import EdgeTimeline
from core.timelines.lbw import LbwTimeline
from core.timelines.no_ball import NoBallTimeline
from core.timelines.run_out import RunOutTimeline
from core.timelines.stumping import StumpingTimeline
from core.timelines.wide import WideTimeline

_TIMELINES: dict[str, ReviewTimeline] = {
    timeline.key: timeline
    for timeline in (LbwTimeline(), WideTimeline(), NoBallTimeline(),
                     RunOutTimeline(), StumpingTimeline(), EdgeTimeline())
}

_ALIASES = {
    "no_ball": "noball", "front_foot": "noball", "frontfoot": "noball",
    "ultraedge": "edge", "ultra_edge": "edge", "snicko": "edge", "run_out": "runout",
}


def timeline_for(review_type: str | None) -> ReviewTimeline:
    key = _ALIASES.get(str(review_type or "lbw").lower(), str(review_type or "lbw").lower())
    return _TIMELINES.get(key, _TIMELINES["lbw"])


def supported_timeline_types() -> list[str]:
    return sorted(_TIMELINES)


__all__ = [
    "ReviewTimeline", "LbwTimeline", "WideTimeline", "NoBallTimeline",
    "RunOutTimeline", "StumpingTimeline", "EdgeTimeline",
    "timeline_for", "supported_timeline_types",
]
