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

from core.camera_roles import BALL_TRACKING
from core.review_modules.base import ReviewContext, ReviewModule, confidence_score


class LbwReviewModule(ReviewModule):
    key = "lbw"
    label = "LBW"
    required_role = BALL_TRACKING
    # DRS protocol order: the umpire clears BAT INVOLVEMENT (UltraEdge) before
    # reading the ball-tracking gates — bat-first contact kills an LBW appeal.
    timeline = ("Appeal", "UltraEdge", "Pitching", "Impact", "Wickets", "Decision")
    evidence = ("ultraedge_check", "ball_tracking", "bounce_point", "impact_point",
                "predicted_trajectory", "pitching", "impact", "wickets", "ball_speed", "replay")
    replay_mode = "trajectory"
    decision_card = ("UltraEdge", "Pitching", "Impact", "Wickets", "Decision")
    supports = {"trajectory": True, "audio": True, "frame_step": True, "measurement": True}

    def analyze(self, ctx: ReviewContext) -> dict:
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

        # DRS protocol: run the UltraEdge check on the SAME captured frames in the same
        # appeal — bat-first contact invalidates LBW, so the umpire clears the edge
        # BEFORE reading the tracking gates. Without a stump mic the edge module
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
            "headline": headline,
            "measurements": [
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
    def _geometry(camera_id, samples, prediction) -> dict:
        measured = [
            [round(float(s.cx), 1), round(float(s.cy), 1),
             round(s.lateral_mm, 1) if s.lateral_mm is not None else None,
             round(s.along_mm, 1) if s.along_mm is not None else None]
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
            "predicted_world": predicted_world,
            "bounce_world": bounce_world,
            "impact_px": [measured[-1][0], measured[-1][1]] if measured else None,
            "hitting": bool(getattr(prediction, "hit_wicket", False)) if prediction is not None else None,
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
