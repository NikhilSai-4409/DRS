"""Wide review module.

Judges where the ball passes the striker relative to the wide guideline. The ball
is tracked across the replay buffer, projected to pitch-world millimetres, and its
lateral offset from the middle stump at the popping crease is compared with the
configured wide line.
"""

from __future__ import annotations

from config.settings import WIDE_LINE_FROM_MIDDLE_M
from core.frame_ref import FrameRef
from core.review_modules.base import (
    ReviewContext,
    ReviewModule,
    confidence_score,
    interpolate_lateral_at,
)


class WideReviewModule(ReviewModule):
    key = "wide"
    label = "Wide"
    required_role = "Wide Camera"
    timeline = ("Release", "Passing Batter", "Wide Line", "Decision")
    evidence = ("crease", "batter_position", "ball_path", "wide_guideline",
                "guideline_distance", "deviation", "replay")
    replay_mode = "wide_line"
    decision_card = ("Guideline", "Margin", "Decision")
    # Operator workflow: measure the wide line, decide. Nothing else exists here.
    protocol = (("wide_line", "Wide Line"), ("decision", "Decision"))
    supports = {"trajectory": True, "guideline": True, "crease": True,
                "frame_step": True, "measurement": True}

    def analyze(self, ctx: ReviewContext) -> dict:
        camera_id = self.select_camera(ctx)
        if camera_id is None:
            return self._awaiting("No camera available for wide review.")

        samples = self.detect_samples(ctx, camera_id)
        if not samples:
            return self._awaiting("No ball detected in the replay buffer.", camera_id)

        avg_conf = sum(s.confidence for s in samples) / len(samples)
        last = samples[-1]
        ball_centre = {"x": round(last.cx, 1), "y": round(last.cy, 1), "z": 0.0}
        radius_px = round(last.radius_px, 1)

        calibrator = ctx.calibrators.get(camera_id)
        if calibrator is None:
            # Without a homography we cannot turn pixels into centimetres.
            return self._partial(
                "Wide camera is not calibrated — calibrate the pitch markers to measure the wide line.",
                camera_id, ball_centre, radius_px, avg_conf,
            )

        self.project(ctx, camera_id, samples)
        lateral_mm = interpolate_lateral_at(samples, ctx.crease_along_mm)
        if lateral_mm is None:
            return self._partial(
                "Ball track did not reach the popping crease line.",
                camera_id, ball_centre, radius_px, avg_conf,
            )

        lateral_m = lateral_mm / 1000.0
        wide_line_m = WIDE_LINE_FROM_MIDDLE_M
        distance_cm = (abs(lateral_m) - wide_line_m) * 100.0
        is_wide = distance_cm > 0.0
        confidence = confidence_score(avg_conf, calibrator, len(samples))

        explanation = (
            f"Ball passed {abs(lateral_m) * 100:.1f} cm from the middle stump at the popping crease; "
            f"wide line at {wide_line_m * 100:.0f} cm — "
            f"{'WIDE' if is_wide else 'within reach (not wide)'}."
        )
        # The wide line lies on the side the ball actually passed; drawing it on the
        # other side would be a correct number against the wrong reference.
        wide_line_lateral_mm = (-1.0 if lateral_m < 0 else 1.0) * wide_line_m * 1000.0
        frame_ref = FrameRef.capture(last.frame_id, last.timestamp_ms, camera_id)
        result = self.base_result(explanation, round(confidence, 3))
        result["wide_analysis"] = {
            "distance_cm": round(distance_cm, 1),
            "is_wide": is_wide,
            "ball_centre": ball_centre,
            "ball_radius_px": radius_px,
            "batter_movement": None,
            "confidence": round(confidence, 3),
            "lateral_offset_cm": round(lateral_m * 100.0, 1),
            "wide_line_cm": round(wide_line_m * 100.0, 1),
            "side": "off" if lateral_m < 0 else "leg",
            "camera_id": camera_id,
            "frames_analysed": len(samples),
            "requires_calibration": False,
            # Which frame the measurement belongs to. Wide previously emitted no
            # frame id at all, so its readout could not be tied to a replay frame.
            "frame": frame_ref.to_dict(),
        }
        # Geometry for the overlay engine. The wide LINE is the piece that was
        # missing: without a pixel projection it could only ever be drawn as a
        # schematic guess, which cannot carry a measurement.
        result["geometry"] = {
            "kind": "wide",
            "camera_id": camera_id,
            "ball_px": [round(last.cx, 1), round(last.cy, 1)],
            "ball_radius_px": radius_px,
            "wide_line_lateral_mm": round(wide_line_lateral_mm, 1),
            "crease_along_mm": ctx.crease_along_mm,
            "distance_cm": round(distance_cm, 1),
            "is_wide": is_wide,
            "frame": frame_ref.to_dict(),
        }
        warnings = []
        if abs(lateral_m) > 0.3:
            warnings.append("Ball is well outside the stump-marker span; result relies on homography extrapolation.")
        if confidence < 0.4:
            warnings.append("Low detection confidence.")
        result["summary"] = {
            "headline": "WIDE" if is_wide else "Not wide",
            "measurements": [
                {"label": "Outside" if is_wide else "Inside", "value": f"{abs(distance_cm):.1f} cm", "flag": is_wide},
                {"label": "Offset from middle stump", "value": f"{abs(lateral_m) * 100:.1f} cm"},
            ],
            "confidence": round(confidence, 3),
            "warnings": warnings,
        }
        return result

    # ----- degraded results (honest "awaiting", never fabricated) -----
    def _awaiting(self, reason: str, camera_id: int | None = None) -> dict:
        result = self.base_result(reason, None)
        result["wide_analysis"] = {
            "distance_cm": None, "is_wide": None, "ball_centre": None,
            "ball_radius_px": None, "batter_movement": None, "confidence": None,
            "camera_id": camera_id, "reason": reason, "requires_calibration": False,
        }
        return result

    def _partial(self, reason, camera_id, ball_centre, radius_px, avg_conf) -> dict:
        result = self.base_result(reason, None)
        result["wide_analysis"] = {
            "distance_cm": None, "is_wide": None, "ball_centre": ball_centre,
            "ball_radius_px": radius_px, "batter_movement": None,
            "confidence": round(avg_conf, 3), "camera_id": camera_id,
            "reason": reason, "requires_calibration": True,
        }
        return result
