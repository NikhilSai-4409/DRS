"""OverlayRenderer — the ONE cricket-aware renderer. It only DRAWS.

    OverlayPayload + AnimationState (from AnimationDirector) ─► render(frame) ─► frame

Timing/sequencing come from :class:`~core.animation_director.AnimationDirector`;
colours/sizes come from a :class:`~core.renderer_theme.RendererTheme`. This class
decides none of that — given a payload and the animation state it renders the
volumetric broadcast overlay: glowing ball spheres (not a line), a three-phase
trajectory (white measured → orange transition → grey-purple predicted), an animated
ground ribbon that grows beneath the ball, bounce/impact pulses, and stump glow +
vibration. ReplayBuilder feeds it video frames; the dashboard feeds it canvas frames.
"""

from __future__ import annotations

import cv2
import numpy as np

from core.animation_director import AnimationDirector
from core.observation import BailsState
from core.renderer_theme import BROADCAST_THEME, RendererTheme, theme_for
from core.timelines import timeline_for


class OverlayRenderer:
    def __init__(self, theme: RendererTheme | None = None, director: AnimationDirector | None = None):
        self._fixed_theme = theme                       # None → pick per review type
        self.theme = theme or BROADCAST_THEME
        self._director = director or AnimationDirector()

    def render(self, frame: np.ndarray, payload: dict, state=1.0) -> np.ndarray:
        """``state`` is an AnimationState dict (from the director) or a float
        progress 0..1 (mapped through a default director for convenience)."""
        payload = payload or {}
        # Each review type carries its own colour identity unless a theme was fixed.
        if self._fixed_theme is None:
            self.theme = theme_for(payload.get("review_type"))
        if not isinstance(state, dict):
            state = self._director.state_for_progress(timeline_for(payload.get("review_type")), payload, float(state))

        frame = frame.copy()
        verdict = str(payload.get("verdict", "")).upper() or "—"

        measured = [(float(p[0]), float(p[1])) for p in (payload.get("measured_px") or [])]
        predicted = [(float(p[0]), float(p[1])) for p in (payload.get("predicted_px") or [])]
        shadow = [(float(p[0]), float(p[1])) for p in (payload.get("shadow_px") or [])]

        reveals = state.get("reveals") or {}
        drew = False
        if measured or predicted:
            self._draw_trajectory(frame, payload, measured, predicted, shadow, state)
            drew = True
        # Field elements (Run Out / Stumping / No Ball) — each gated by its own cue,
        # so the review's animation identity comes entirely from the timeline.
        if payload.get("crease_px"):
            self._draw_crease(frame, payload["crease_px"], reveals.get("crease", 1.0)); drew = True
        if payload.get("bat_px"):
            self._draw_outline(frame, payload["bat_px"], reveals.get("bat", 1.0), self.theme.transition_color, "BAT"); drew = True
        if payload.get("foot_px_outline"):
            self._draw_outline(frame, payload["foot_px_outline"], reveals.get("foot", 1.0), (90, 200, 90), "FOOT"); drew = True
        if payload.get("bails_px"):
            self._draw_bails(frame, payload["bails_px"], payload.get("bails_status"), reveals.get("bails", 1.0)); drew = True
        if payload.get("stumps_px") and not (measured or predicted):
            self._stumps(frame, payload["stumps_px"], {**state, "stumps_reveal": reveals.get("bails", state.get("stumps_reveal", 1.0))})
        if payload.get("frame_number") is not None and reveals.get("framestep", 0.0) > 0.05:
            self._draw_framestep(frame, payload["frame_number"])
        if not drew:
            self._draw_simple_markers(frame, payload)

        self._draw_banner(frame, payload, verdict)
        self._draw_cards(frame, payload.get("decision_cards") or [], state.get("cards") or [])
        self._draw_measurements(frame, payload)
        return frame

    # ------------------------------------------------------------------ #
    # Volumetric three-phase trajectory
    # ------------------------------------------------------------------ #
    def _draw_trajectory(self, frame, payload, measured, predicted, shadow, state):
        theme = self.theme
        height = frame.shape[0]
        confidence = payload.get("confidence")
        confidence = float(confidence) if confidence is not None else 0.6

        measured_show = int(round(state.get("measured_reveal", 1.0) * len(measured)))
        predicted_show = int(round(state.get("predicted_reveal", 1.0) * len(predicted)))
        measured_show = max(0, min(len(measured), measured_show))
        predicted_show = max(0, min(len(predicted), predicted_show))

        def radius(y: float, scale: float = 1.0) -> int:
            span = theme.sphere_max_r - theme.sphere_min_r
            return max(2, int((theme.sphere_min_r + span * (max(0.0, min(height, y)) / max(1, height))) * scale))

        # ground ribbon grows to the current ball tip
        total = len(measured) + len(predicted)
        ribbon_reveal = (measured_show + predicted_show) / total if total else 0.0
        self._ribbon(frame, shadow[:max(2, int(ribbon_reveal * len(shadow)))])

        # PHASE 1 — measured: bright white spheres, orange transition glow near impact
        for index in range(measured_show):
            x, y = measured[index]
            near_impact = index >= len(measured) - 2
            glow = theme.transition_color if near_impact else None
            self._sphere(frame, x, y, radius(y), theme.measured_color, glow=glow)
        if measured_show:
            self._motion_blur(frame, measured[:measured_show], theme.measured_color, radius)

        # bounce sphere (orange, transition)
        bounce = payload.get("bounce_px")
        if bounce and state.get("bounce_visible"):
            self._sphere(frame, bounce["x"], bounce["y"], radius(bounce["y"], 1.25),
                         theme.bounce_color, glow=theme.transition_color)
            cv2.putText(frame, "BOUNCE", (int(bounce["x"]) + 16, int(bounce["y"]) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, theme.bounce_color, 1, cv2.LINE_AA)

        # PHASE 3 — predicted: grey-purple translucent spheres (opacity ∝ confidence)
        alpha = theme.predicted_alpha * (0.6 + 0.4 * max(0.0, min(1.0, confidence)))
        for index in range(predicted_show):
            x, y = predicted[index]
            self._sphere(frame, x, y, radius(y, 0.85), theme.predicted_color, alpha=alpha)

        # impact pulse
        impact = payload.get("impact_px")
        if impact and state.get("impact_visible"):
            self._impact(frame, int(impact["x"]), int(impact["y"]), state.get("impact_pulse", 0.0))

        # stumps: glow + vibration when hitting
        self._stumps(frame, payload.get("stumps_px") or [], state)

        # bright animated ball head at the current tip
        head = predicted[predicted_show - 1] if predicted_show else (measured[measured_show - 1] if measured_show else None)
        if head is not None:
            self._sphere(frame, head[0], head[1], radius(head[1], 1.1) + 1, (255, 255, 255))

    def _sphere(self, frame, x, y, r, color, alpha: float = 1.0, glow=None):
        x, y, r = int(x), int(y), max(2, int(r))
        if glow is not None:
            halo = frame.copy()
            cv2.circle(halo, (x, y), int(r * 2.1), glow, -1, cv2.LINE_AA)
            cv2.addWeighted(halo, self.theme.glow_strength, frame, 1 - self.theme.glow_strength, 0, frame)
        shadow = frame.copy()
        cv2.ellipse(shadow, (x, y + int(r * 0.9)), (int(r * 1.15), max(1, int(r * 0.4))), 0, 0, 360, (18, 18, 18), -1, cv2.LINE_AA)
        cv2.addWeighted(shadow, 0.22, frame, 0.78, 0, frame)

        target = frame if alpha >= 1.0 else frame.copy()
        cv2.circle(target, (x, y), r, color, -1, cv2.LINE_AA)
        cv2.circle(target, (x, y), r, tuple(int(c * 0.55) for c in color), 1, cv2.LINE_AA)
        cv2.circle(target, (x - int(r * 0.3), y - int(r * 0.3)), max(1, int(r * 0.34)), (245, 245, 245), -1, cv2.LINE_AA)
        if alpha < 1.0:
            cv2.addWeighted(target, alpha, frame, 1 - alpha, 0, frame)

    def _motion_blur(self, frame, points, color, radius_fn):
        layer = frame.copy()
        for depth, (x, y) in enumerate(points[-4:]):
            fade = 0.12 + 0.10 * depth
            cv2.circle(layer, (int(x), int(y)), radius_fn(y) + 3 - depth, color, -1, cv2.LINE_AA)
            cv2.addWeighted(layer, fade, frame, 1 - fade, 0, frame)

    def _ribbon(self, frame, shadow):
        if len(shadow) < 2:
            return
        layer = frame.copy()
        pts = np.array([(int(x), int(y)) for x, y in shadow], dtype=np.int32)
        cv2.polylines(layer, [pts], False, self.theme.ribbon_color, 16, cv2.LINE_AA)
        cv2.addWeighted(layer, self.theme.ribbon_alpha, frame, 1 - self.theme.ribbon_alpha, 0, frame)
        cv2.polylines(frame, [pts], False, (110, 110, 110), 1, cv2.LINE_AA)

    # ----- field elements (Run Out / Stumping / No Ball) -----
    def _draw_crease(self, frame, crease, reveal):
        """The popping crease — emphasised (glow + bright line), grown to `reveal`."""
        pts = [(float(p[0]), float(p[1])) for p in crease]
        if len(pts) < 2:
            return
        cut = max(2, int(reveal * len(pts))) if len(pts) > 2 else 2
        pts = pts[:cut]
        arr = np.array([(int(x), int(y)) for x, y in pts], dtype=np.int32)
        glow = frame.copy()
        cv2.polylines(glow, [arr], False, (120, 220, 255), 12, cv2.LINE_AA)
        cv2.addWeighted(glow, 0.28, frame, 0.72, 0, frame)
        cv2.polylines(frame, [arr], False, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(frame, "CREASE", (int(pts[0][0]) + 8, int(pts[0][1]) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 230, 255), 1, cv2.LINE_AA)

    def _draw_outline(self, frame, outline, reveal, color, label):
        pts = [(int(p[0]), int(p[1])) for p in outline]
        if len(pts) < 2:
            return
        layer = frame.copy()
        cv2.polylines(layer, [np.array(pts, dtype=np.int32)], True, color, 2, cv2.LINE_AA)
        cv2.addWeighted(layer, max(0.0, min(1.0, reveal)), frame, 1 - max(0.0, min(1.0, reveal)), 0, frame)
        if reveal > 0.5:
            cx = int(sum(p[0] for p in pts) / len(pts))
            cy = int(min(p[1] for p in pts)) - 8
            cv2.putText(frame, label, (cx - 12, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    def _draw_bails(self, frame, bails, status, reveal):
        """Three states, never two. Absent status means UNKNOWN — it must not fall
        back to "INTACT", which would assert the wicket was unbroken just as falsely
        as the old hard-coded "DISLODGED" asserted the opposite."""
        if reveal <= 0:
            return
        state = BailsState.coerce(status)
        dislodged = state is BailsState.DISLODGED
        if dislodged:
            color, caption = (60, 60, 235), "BAILS DISLODGED"
        elif state is BailsState.INTACT:
            color, caption = (255, 255, 255), "BAILS INTACT"
        else:
            color, caption = (153, 139, 125), "BAILS NOT OBSERVED"   # neutral grey
        for bail in bails:
            x, y = int(bail["x"]), int(bail["y"])
            if dislodged:
                glow = frame.copy()
                cv2.circle(glow, (x, y), int(12 * reveal) + 3, color, -1, cv2.LINE_AA)
                cv2.addWeighted(glow, 0.3 * reveal, frame, 1 - 0.3 * reveal, 0, frame)
            cv2.line(frame, (x - 7, y), (x + 7, y), color, max(2, int(3 * reveal)), cv2.LINE_AA)
        if reveal > 0.5 and bails:
            cv2.putText(frame, caption,
                        (int(bails[0]["x"]) + 14, int(bails[0]["y"]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    def _draw_framestep(self, frame, frame_number):
        cv2.putText(frame, f"FRAME {frame_number}", (16, 74),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 220, 255), 2, cv2.LINE_AA)

    def _impact(self, frame, x, y, pulse):
        color = self.theme.impact_color
        cv2.circle(frame, (x, y), int(11 + 13 * pulse), color, 2, cv2.LINE_AA)
        cv2.circle(frame, (x, y), 5, color, -1, cv2.LINE_AA)
        cv2.putText(frame, "IMPACT", (x + 15, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    def _stumps(self, frame, stumps, state):
        reveal = state.get("stumps_reveal", 1.0)
        if reveal <= 0 or not stumps:
            return
        hitting = state.get("hitting")
        color = self.theme.stump_hit_color if hitting else self.theme.stump_idle_color
        shift = int(state.get("stump_vibration", 0.0) * 3) if hitting else 0
        for stump in stumps:
            x, y = int(stump["x"]) + shift, int(stump["y"])
            if hitting:
                glow = frame.copy()
                cv2.circle(glow, (x, y - 18), int(18 * reveal) + 4, color, -1, cv2.LINE_AA)
                cv2.addWeighted(glow, 0.25 * reveal, frame, 1 - 0.25 * reveal, 0, frame)
            cv2.line(frame, (x, y), (x, y - int(42 * reveal)), color, 3, cv2.LINE_AA)
            if hitting and reveal > 0.8:                       # bails illuminate
                cv2.line(frame, (x - 5, y - 42), (x + 5, y - 42), (255, 255, 255), 2, cv2.LINE_AA)

    # ------------------------------------------------------------------ #
    # Chrome
    # ------------------------------------------------------------------ #
    def _draw_banner(self, frame, payload, verdict):
        width = frame.shape[1]
        label = str(payload.get("review_type", "review")).upper()
        cv2.rectangle(frame, (0, 0), (width, 46), self.theme.banner_bg, -1)
        cv2.putText(frame, label, (14, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (235, 235, 235), 2, cv2.LINE_AA)
        (text_w, _), _ = cv2.getTextSize(verdict, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame, verdict, (width - text_w - 16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    _verdict_color(payload.get("verdict", "")), 2, cv2.LINE_AA)

    def _draw_cards(self, frame, cards, card_states):
        if not cards:
            return
        width = frame.shape[1]
        card_w, card_h, gap = 190, 46, 10
        x0, y0 = width - card_w - 16, 60
        status_colors = {"out": self.theme.card_status[0], "not-out": self.theme.card_status[1], "info": self.theme.card_status[2]}
        for index, card in enumerate(cards):
            reveal = card_states[index] if index < len(card_states) else 1.0
            if reveal <= 0:
                continue
            slide = int((1.0 - reveal) * 40)                    # slide-in from the right
            y = y0 + index * (card_h + gap)
            x = x0 + slide
            panel = frame.copy()
            cv2.rectangle(panel, (x, y), (x + card_w, y + card_h), self.theme.card_bg, -1)
            blend = 0.65 * reveal
            cv2.addWeighted(panel, blend, frame, 1 - blend, 0, frame)
            color = status_colors.get(card.get("status"), self.theme.card_status[2])
            cv2.rectangle(frame, (x, y), (x + card_w, y + card_h), color, 1, cv2.LINE_AA)
            cv2.rectangle(frame, (x, y), (x + 5, y + card_h), color, -1)
            cv2.putText(frame, str(card.get("label", ""))[:20], (x + 12, y + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(frame, str(card.get("value", ""))[:16], (x + 12, y + 38),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

    @staticmethod
    def _draw_measurements(frame, payload):
        cursor_y = frame.shape[0] - 16
        for measurement in reversed((payload.get("measurements") or [])[:3]):
            text = f"{measurement.get('label', '')}: {measurement.get('value', '')}"
            cv2.putText(frame, text, (14, cursor_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
            cursor_y -= 24
        confidence = payload.get("confidence")
        if confidence is not None:
            cv2.putText(frame, f"Confidence {float(confidence) * 100:.0f}%", (14, cursor_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 220, 255), 1, cv2.LINE_AA)

    @staticmethod
    def _draw_simple_markers(frame, payload):
        centre = payload.get("ball_centre")
        if isinstance(centre, dict) and centre.get("x") is not None:
            cv2.circle(frame, (int(centre["x"]), int(centre["y"])), 9, (0, 215, 255), 2, cv2.LINE_AA)
        for key, color in (("toe_px", (80, 200, 90)), ("heel_px", (60, 60, 235))):
            point = payload.get(key)
            if isinstance(point, dict) and point.get("x") is not None:
                cv2.drawMarker(frame, (int(point["x"]), int(point["y"])), color, cv2.MARKER_TILTED_CROSS, 18, 2)


def _verdict_color(verdict: str) -> tuple:
    colors = {
        "out": (60, 60, 235), "wide": (60, 60, 235), "no ball": (60, 60, 235), "edge": (60, 60, 235),
        "not out": (90, 200, 90), "not wide": (90, 200, 90), "legal": (90, 200, 90), "no edge": (90, 200, 90),
    }
    return colors.get((verdict or "").strip().lower(), (160, 160, 160))
