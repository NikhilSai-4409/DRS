"""LBW review module.

Wraps detection → projection → trajectory behind the same ReviewModule interface
as Wide / No Ball / Edge, so the API runs every review type through
``run_review(ctx)`` with no special-casing. The ball is detected over the
synchronized replay buffer, projected to pitch-world coordinates, and a ballistic
trajectory is predicted from the calibrated world track.

The impact / wicket-zone geometry is still the prototype decision package the
backend seeds (``DRSBackend._sample_decision``); this module supplies the REAL
detection, tracking confidence and predicted trajectory on top of it. Replacing
the seeded impact/wicket fields with measured LBW geometry is the accuracy
follow-up — the interface here will not change when that lands.
"""

from __future__ import annotations

from typing import Optional

from core.camera_roles import BALL_TRACKING
from core.frame_ref import FrameRef
from core.observation import Observation
from core.review_modules.base import ReviewContext, ReviewModule, confidence_score


def _wicket_observation(prediction) -> Observation:
    """Did the predicted path hit the stumps? UNKNOWN unless the collision test was
    actually performed — "we never checked" is not the same answer as "it missed"."""
    if prediction is None or not getattr(prediction, "wicket_evaluated", False):
        return Observation.UNKNOWN
    return Observation.of(bool(getattr(prediction, "wicket_collision", False)))


class LbwReviewModule(ReviewModule):
    key = "lbw"
    label = "LBW"
    required_role = BALL_TRACKING
    # DRS protocol order: a front-foot NO BALL voids the dismissal outright, then the
    # umpire clears BAT INVOLVEMENT (UltraEdge), and only then reads ball-tracking.
    timeline = ("Appeal", "No Ball", "UltraEdge", "Pitching", "Impact", "Wickets", "Decision")
    evidence = ("front_foot_check", "ultraedge_check", "ball_tracking", "bounce_point",
                "impact_point", "predicted_trajectory", "pitching", "impact", "wickets",
                "ball_speed", "replay")
    replay_mode = "trajectory"
    decision_card = ("No Ball", "UltraEdge", "Pitching", "Impact", "Wickets", "Decision")
    # Operator workflow: clear the front foot, clear the bat, then read tracking.
    protocol = (("front_foot", "Front Foot"), ("ultra_edge", "UltraEdge"),
                ("trajectory", "Ball Tracking"), ("decision", "Decision"))
    supports = {"trajectory": True, "audio": True, "crease": True,
                "frame_step": True, "measurement": True}

    def analyze(self, ctx: ReviewContext) -> dict:
        # DRS protocol step 1 — FRONT-FOOT NO BALL, checked FIRST, exactly like TV
        # umpiring: an overstep ends the review on the spot. No UltraEdge, no ball
        # tracking, no trajectory — the batter cannot be out LBW off a no-ball.
        no_ball_analysis = self._front_foot_check(ctx)
        if (no_ball_analysis or {}).get("is_no_ball") is True:
            return self._no_ball_short_circuit(no_ball_analysis)

        camera_id = self.select_camera(ctx)
        frames = ctx.frames.get(camera_id, []) if camera_id is not None else []
        capped = frames[-ctx.max_frames:]
        samples = self.detect_samples(ctx, camera_id) if camera_id is not None else []

        detection_rate = round(len(samples) / max(1, len(capped)), 4)
        avg_conf = round(sum(s.confidence for s in samples) / len(samples), 4) if samples else 0.0

        # LBW deliberately does NOT call base_result(): the seeded prototype impact /
        # wicket-zone fields must survive the merge in request_review. We only add the
        # parts this module actually measures.
        result: dict = {
            "review_type": "lbw",
            "detection_rate": detection_rate,
            "avg_confidence": avg_conf,
        }

        calibrator = ctx.calibrators.get(camera_id) if camera_id is not None else None
        warnings: list[str] = []
        prediction = None
        if calibrator is not None and samples:
            self.project(ctx, camera_id, samples)
            prediction = self._predict(samples)

        if prediction is not None:
            trajectory_points = prediction.to_dict().get("points") or []
            result["trajectory"] = trajectory_points
            result["predicted_extension"] = trajectory_points[-8:]
            result["ball_confidence"] = avg_conf
            result["overall_confidence"] = round(confidence_score(avg_conf, calibrator, len(samples)), 3)
            headline = "Ball tracked"
        else:
            if not samples:
                warnings.append("No ball detected in the replay buffer — showing prototype decision data.")
            elif calibrator is None:
                warnings.append("Ball-tracking camera is not calibrated — trajectory not measured.")
            headline = "LBW review"

        # Purely ANALYTICAL geometry (world coordinates + observed pixels, no
        # projection, no styling). core.overlay_builder projects it for a broadcast
        # camera; core.overlay_renderer draws it. Graphics never leak into a module.
        if samples:
            result["geometry"] = self._geometry(camera_id, samples, prediction)

        # Legal (or unchecked) delivery: merge the step-1 front-foot result and
        # continue the protocol. A NO BALL never reaches this point — it already
        # short-circuited above.
        no_ball_flag = False
        if no_ball_analysis is not None:
            result["no_ball_analysis"] = no_ball_analysis
        no_ball = no_ball_analysis or {}
        if no_ball.get("is_no_ball") is False:
            behind = no_ball.get("distance_past_cm")
            no_ball_value = f"Legal ({abs(behind):.1f} cm behind)" if behind is not None else "Legal delivery"
        else:
            reason = no_ball.get("reason") or "front-foot camera not available/calibrated"
            no_ball_value = "Unchecked — verify manually"
            warnings.append(f"Front-foot no-ball unchecked ({reason}) — verify before confirming OUT.")

        # DRS protocol step 2: run the UltraEdge check on the SAME captured frames in
        # the same appeal — bat-first contact invalidates LBW, so the umpire clears the
        # edge BEFORE reading the tracking gates. Without a stump mic the edge module
        # honestly reports inconclusive; the reminder to clear it manually stands.
        try:
            from core.review_modules.edge import EdgeReviewModule

            edge_result = EdgeReviewModule().analyze(ctx) or {}
            if edge_result.get("edge_analysis") is not None:
                result["edge_analysis"] = edge_result["edge_analysis"]
            if edge_result.get("hotspot_analysis") is not None:
                result["hotspot_analysis"] = edge_result["hotspot_analysis"]
        except Exception:
            pass
        edge = result.get("edge_analysis") or {}
        if edge.get("inconclusive"):
            edge_value = "Inconclusive — clear manually"
            warnings.append("UltraEdge inconclusive (no stump-mic audio) — manually clear bat involvement before confirming OUT.")
        elif edge.get("edge_probability") is not None:
            edge_value = f"{(edge.get('edge_probability') or 0.0) * 100:.0f}% spike"
            if (edge.get("edge_probability") or 0.0) >= 0.5:
                warnings.append("Possible BAT INVOLVEMENT (UltraEdge spike) — bat-first contact means NOT OUT for LBW.")
        else:
            edge_value = "Not run — clear manually"
            warnings.append("UltraEdge check unavailable for this appeal — clear bat involvement manually.")

        result["summary"] = {
            "headline": "NOT OUT — NO BALL" if no_ball_flag else headline,
            "measurements": [
                {"label": "No Ball", "value": no_ball_value, "flag": no_ball_flag},
                {"label": "UltraEdge", "value": edge_value,
                 "flag": (edge.get("edge_probability") or 0.0) >= 0.5},
                {"label": "Detection rate", "value": f"{detection_rate * 100:.0f}%"},
                {"label": "Frames tracked", "value": str(len(samples))},
            ],
            "confidence": result.get("overall_confidence", avg_conf if samples else None),
            "warnings": warnings,
        }
        return result

    @staticmethod
    def _front_foot_check(ctx: ReviewContext) -> Optional[dict]:
        """Run the front-foot module on the same captured frames; None when the
        check itself could not run (its honest reason travels in the analysis)."""
        try:
            from core.review_modules.no_ball import NoBallReviewModule

            nb_result = NoBallReviewModule().analyze(ctx) or {}
            return nb_result.get("no_ball_analysis")
        except Exception:
            return None

    @staticmethod
    def _no_ball_short_circuit(no_ball_analysis: dict) -> dict:
        """The review ends at step 1: NO BALL. Nothing further is analysed — the
        result explicitly says the later protocol stages were not needed."""
        overstep = no_ball_analysis.get("distance_past_cm")
        value = f"NO BALL — over by {abs(overstep):.1f} cm" if overstep is not None else "NO BALL"
        return {
            "review_type": "lbw",
            "no_ball_analysis": no_ball_analysis,
            "verdict": "NOT OUT - NO BALL",
            "review_ended": "no_ball",           # protocol short-circuit marker
            "summary": {
                "headline": "NOT OUT — NO BALL",
                "measurements": [
                    {"label": "No Ball", "value": value, "flag": True},
                    {"label": "UltraEdge", "value": "Not needed — review ended"},
                    {"label": "Ball Tracking", "value": "Not needed — review ended"},
                ],
                "confidence": no_ball_analysis.get("confidence"),
                "warnings": ["FRONT-FOOT NO BALL — the delivery is illegal; the batter cannot be out LBW."],
            },
        }

    @staticmethod
    def _geometry(camera_id, samples, prediction) -> dict:
        measured = [
            [round(float(s.cx), 1), round(float(s.cy), 1),
             round(s.lateral_mm, 1) if s.lateral_mm is not None else None,
             round(s.along_mm, 1) if s.along_mm is not None else None]
            for s in samples
        ]
        # Frame identity per tracked point. `measured` above is a bare polyline —
        # its frame ids were dropped here, so an overlay could be drawn once on a
        # decision frame but could never FOLLOW the ball as the umpire steps. The
        # frontend was reduced to synthesising `frame_id: i` array positions.
        # `track` carries the real capture frame and timestamp alongside each
        # point; `measured` stays for existing consumers.
        track = [
            {
                "px": [round(float(s.cx), 1), round(float(s.cy), 1)],
                "world_mm": [
                    round(s.lateral_mm, 1) if s.lateral_mm is not None else None,
                    round(s.along_mm, 1) if s.along_mm is not None else None,
                ],
                "confidence": s.confidence,
                "frame": FrameRef.capture(s.frame_id, s.timestamp_ms, camera_id).to_dict(),
            }
            for s in samples
        ]
        predicted_world: list[list[float]] = []
        bounce_world = None
        if prediction is not None and getattr(prediction, "points", None):
            points = prediction.points
            predicted_world = [[float(p.x), float(p.y), float(p.z)] for p in points]
            bounce_index = getattr(prediction, "bounce_index", None)
            if isinstance(bounce_index, int) and 0 <= bounce_index < len(points):
                bounce_world = [float(points[bounce_index].x), float(points[bounce_index].y)]
        return {
            "kind": "lbw",
            "camera_id": camera_id,
            "measured": measured,
            "track": track,
            "predicted_world": predicted_world,
            "bounce_world": bounce_world,
            "impact_px": [measured[-1][0], measured[-1][1]] if measured else None,
            # Tri-state. Two defects made this permanently False — a claim that the
            # ball MISSED the stumps on every delivery:
            #   1. it read `hit_wicket`, but the field is `wicket_collision`;
            #   2. `_predict` never supplies `wicket_x_m`, so the collision test is
            #      never performed at all (see `wicket_evaluated`).
            # (2) is a real gap, not a typo: `_find_wicket_collision` treats x as the
            # down-pitch axis while `_predict` packs x as LATERAL offset, so wiring it
            # up needs the axis convention reconciled first — a functional change that
            # can move LBW verdicts, deliberately not made here. Until then the honest
            # answer is UNKNOWN.
            "hitting": _wicket_observation(prediction).value,
        }

    def _predict(self, samples):
        world_points: list[tuple[float, float, float]] = []
        times: list[float] = []
        for sample in samples[-10:]:
            if sample.lateral_mm is None or sample.along_mm is None:
                continue
            world_points.append((sample.lateral_mm / 1000.0, sample.along_mm / 1000.0, 0.12))
            times.append(sample.timestamp_ms / 1000.0)
        if len(world_points) < 2:
            return None
        try:
            from core.trajectory import TrajectoryPredictor

            return TrajectoryPredictor().predict_from_world_points(world_points, times)
        except Exception:  # prediction must never break a review
            return None
