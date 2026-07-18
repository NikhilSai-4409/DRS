"""AnimationDirector — decides WHEN each OVERLAY element appears; never draws, never
moves the camera. It **consumes** a declarative :class:`~core.timelines.ReviewTimeline`
(resolved by the caller via ``timeline_for``) — it does not look one up itself:

    timeline_for(review_type) ─► Timeline ─► AnimationDirector.state_at(timeline, payload, t)

Camera work (freeze/zoom/frame-step/slow-mo) lives in
:class:`~core.camera_director.CameraDirector`.
"""

from __future__ import annotations

import math


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ease(t: float) -> float:
    t = _clamp(t)
    return t * t * (3.0 - 2.0 * t)


class AnimationDirector:
    def state_at(self, timeline, payload: dict, t: float) -> dict:
        payload = payload or {}
        cards = payload.get("decision_cards") or []
        hitting = bool(payload.get("hitting"))

        def ramp(start: float, dur: float) -> float:
            return _clamp((t - start) / max(1e-6, dur))

        reveals = {key: _ease(ramp(start, dur)) for key, (start, dur) in timeline.cues.items()}
        measured = reveals.get("measured", 0.0)
        impact_visible = "impact" in timeline.cues and measured >= 1.0
        impact_start = timeline.cues.get("impact", (2.2, 0.2))[0]

        stumps_cue = "stumps" if "stumps" in timeline.cues else ("bails" if "bails" in timeline.cues else None)
        stumps_reveal = reveals.get(stumps_cue, 0.0) if stumps_cue else 0.0
        vibration = 0.0
        if hitting and stumps_cue:
            start = timeline.cues[stumps_cue][0]
            if start <= t <= start + 0.6:
                vibration = math.sin((t - start) * 40.0) * (1.0 - (t - start) / 0.6)

        return {
            "t": t, "duration": timeline.duration, "review_type": timeline.key,
            "reveals": reveals,
            "measured_reveal": measured,
            "predicted_reveal": reveals.get("predicted", 0.0),
            "bounce_visible": reveals.get("bounce", 0.0) > 0.05,
            "impact_visible": impact_visible,
            "impact_pulse": abs(math.sin((t - impact_start) * math.pi * 3.0)) if impact_visible else 0.0,
            "stumps_reveal": stumps_reveal,
            "stump_vibration": vibration,
            "hitting": hitting,
            "cards": [_ease(ramp(timeline.cards_start + index * 0.32, 0.25)) for index in range(len(cards))],
            "verdict_reveal": _ease(ramp(timeline.verdict[0], timeline.verdict[1])),
        }

    def full_state(self, timeline, payload: dict) -> dict:
        return self.state_at(timeline, payload, timeline.duration)

    def state_for_progress(self, timeline, payload: dict, progress: float) -> dict:
        return self.state_at(timeline, payload, _clamp(progress) * timeline.duration)
