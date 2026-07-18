"""CameraDirector — runs the CAMERA, separate from overlay animation (as broadcast
replay systems are structured). It **consumes** a declarative
:class:`~core.timelines.ReviewTimeline` (resolved by the caller) and returns the
camera state at time ``t``:

    freeze · zoom · frame-step · slow-motion · pan

ReplayBuilder applies these to the video frame; the live dashboard applies the same
state to its canvas. Overlay reveals stay in :class:`AnimationDirector`.
"""

from __future__ import annotations


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class CameraDirector:
    def __init__(self, zoom_max: float = 0.08):
        self.zoom_max = float(zoom_max)

    def state_at(self, timeline, t: float) -> dict:
        state = {"t": t, "duration": timeline.duration, "zoom": 0.0,
                 "freeze": False, "frame_step": False, "slowmo": False, "pan": 0.0}
        for action, start, dur in timeline.camera:
            if action == "zoom":
                state["zoom"] = _clamp((t - start) / max(1e-6, dur)) * self.zoom_max
            elif action == "freeze":
                state["freeze"] = t < start + dur
            elif action == "framestep":
                state["frame_step"] = start <= t <= start + dur
            elif action == "slowmo":
                state["slowmo"] = start <= t <= start + dur
            elif action == "pan":
                state["pan"] = _clamp((t - start) / max(1e-6, dur))
        return state

    def full_state(self, timeline) -> dict:
        return self.state_at(timeline, timeline.duration)

    def state_for_progress(self, timeline, progress: float) -> dict:
        return self.state_at(timeline, _clamp(progress) * timeline.duration)
