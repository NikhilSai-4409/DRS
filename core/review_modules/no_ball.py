"""Front Foot No Ball review module.

Pipeline: crease line (from the calibration homography) -> front-foot localisation
on the front-foot camera -> project toe/heel to pitch millimetres -> legal vs
no-ball decision from the back of the foot relative to the popping crease.

Foot localisation uses a motion + contour heuristic (the front-foot camera is a
fixed, downward view of the crease, so the landing foot is the dominant moving
object in the lower frame). The decision geometry is exact; the localiser is the
component to upgrade with a trained foot-segmentation / pose model — see
:class:`FootLocator`, which is intentionally swappable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from config.settings import NO_BALL_CREASE_MARGIN_MM
from core.review_modules.base import ReviewContext, ReviewModule
from utils.logger import get_logger

log = get_logger("review_engine")


@dataclass(slots=True)
class FootLocation:
    toe_px: tuple[float, float]
    heel_px: tuple[float, float]
    confidence: float
    landing_frame_id: int


class FootLocator:
    """Motion-based front-foot localiser. Replace ``locate`` with a trained model."""

    def __init__(self, roi_top_ratio: float = 0.4, motion_threshold: int = 18, min_area: int = 400):
        self.roi_top_ratio = roi_top_ratio
        self.motion_threshold = motion_threshold
        self.min_area = min_area

    def locate(self, frames: list) -> Optional[FootLocation]:
        if len(frames) < 3:
            return None
        grays = []
        for vf in frames:
            frame = getattr(vf, "frame", None)
            if frame is None:
                continue
            grays.append((vf.frame_id, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
        if len(grays) < 3:
            return None

        height = grays[0][1].shape[0]
        roi_top = int(height * self.roi_top_ratio)

        best = None  # (motion, frame_id, diff_roi)
        for (_, prev), (fid, cur) in zip(grays, grays[1:]):
            diff = cv2.absdiff(cur, prev)[roi_top:, :]
            motion = float(diff.sum())
            if best is None or motion > best[0]:
                best = (motion, fid, diff)
        if best is None or best[0] <= 0:
            return None

        _, landing_frame_id, diff_roi = best
        blurred = cv2.GaussianBlur(diff_roi, (5, 5), 0)
        _, mask = cv2.threshold(blurred, self.motion_threshold, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area < self.min_area:
            return None

        pts = contour.reshape(-1, 2).astype(np.float64)
        pts[:, 1] += roi_top  # shift back into full-frame coordinates

        # Assume the camera is mounted so down-pitch ~ image vertical: the toe is the
        # forward-most point (largest y, toward the stumps/batter), the heel the
        # rear-most (smallest y). Calibration resolves the true direction afterwards.
        toe = pts[int(np.argmax(pts[:, 1]))]
        heel = pts[int(np.argmin(pts[:, 1]))]

        motion_strength = min(1.0, best[0] / (diff_roi.size * 40.0))
        area_strength = min(1.0, area / (diff_roi.size * 0.04 + 1.0))
        confidence = max(0.25, min(0.8, 0.4 + 0.4 * (0.5 * motion_strength + 0.5 * area_strength)))

        return FootLocation(
            toe_px=(float(toe[0]), float(toe[1])),
            heel_px=(float(heel[0]), float(heel[1])),
            confidence=round(confidence, 3),
            landing_frame_id=int(landing_frame_id),
        )


class NoBallReviewModule(ReviewModule):
    key = "noball"
    label = "Front Foot No Ball"
    required_role = "Front Foot"
    timeline = ("Release", "Landing", "Front Foot", "Decision")
    evidence = ("release_frame", "front_foot_frame", "crease_line", "foot_zoom",
                "foot_polygon", "contact_area", "overstep_percent", "replay")
    replay_mode = "freeze_frame"
    decision_card = ("Front foot", "Margin", "Decision")
    supports = {"crease": True, "freeze_frame": True, "zoom": True,
                "frame_step": True, "measurement": True}

    def __init__(self, foot_locator: FootLocator | None = None):
        self.foot_locator = foot_locator or FootLocator()

    def analyze(self, ctx: ReviewContext) -> dict:
        camera_id = self.select_camera(ctx)
        if camera_id is None:
            return self._awaiting("No front-foot camera available.")

        frames = ctx.frames.get(camera_id, [])[-ctx.max_frames:]
        if len(frames) < 3:
            return self._awaiting("Insufficient replay frames for foot landing.", camera_id)

        foot = self.foot_locator.locate(frames)
        if foot is None:
            return self._awaiting("Front foot not detected in the replay buffer.", camera_id, foot_detected=False)

        calibrator = ctx.calibrators.get(camera_id)
        if calibrator is None:
            return self._partial(
                "Front-foot camera is not calibrated — calibrate the crease markers to measure the overstep.",
                camera_id, foot,
            )

        toe_mm = calibrator.pixel_to_pitch_mm(camera_id, *foot.toe_px)
        heel_mm = calibrator.pixel_to_pitch_mm(camera_id, *foot.heel_px)
        if toe_mm is None or heel_mm is None:
            return self._partial("Could not project the foot onto the pitch.", camera_id, foot)

        crease_along = ctx.crease_along_mm
        # The back of the foot (most negative along) decides legality: a no-ball is
        # called only when no part of the foot is behind the popping crease.
        back_along_mm = min(toe_mm[1], heel_mm[1])
        distance_past_mm = back_along_mm - crease_along
        is_no_ball = distance_past_mm > NO_BALL_CREASE_MARGIN_MM
        distance_past_cm = distance_past_mm / 10.0

        explanation = (
            f"Back of the front foot {abs(distance_past_cm):.1f} cm "
            f"{'past' if is_no_ball else 'behind'} the popping crease — "
            f"{'NO BALL' if is_no_ball else 'legal delivery'}."
        )
        result = self.base_result(explanation, foot.confidence)
        result["no_ball_analysis"] = {
            "distance_past_cm": round(distance_past_cm, 1),
            "is_no_ball": is_no_ball,
            "foot_position": "Past line" if is_no_ball else "Behind line",
            "confidence": foot.confidence,
            "toe_mm": {"lateral": round(toe_mm[0], 1), "along": round(toe_mm[1], 1)},
            "heel_mm": {"lateral": round(heel_mm[0], 1), "along": round(heel_mm[1], 1)},
            "toe_px": {"x": round(foot.toe_px[0], 1), "y": round(foot.toe_px[1], 1)},
            "heel_px": {"x": round(foot.heel_px[0], 1), "y": round(foot.heel_px[1], 1)},
            "landing_frame_id": foot.landing_frame_id,
            "camera_id": camera_id,
            "foot_detected": True,
            "requires_calibration": False,
        }
        result["summary"] = {
            "headline": "NO BALL" if is_no_ball else "Legal delivery",
            "measurements": [
                {
                    "label": "Past crease" if is_no_ball else "Behind crease",
                    "value": f"{abs(distance_past_cm):.1f} cm",
                    "flag": is_no_ball,
                },
            ],
            "confidence": foot.confidence,
            "warnings": ["Foot localised by a motion heuristic — verify before the final call."],
        }
        return result

    def _awaiting(self, reason: str, camera_id: int | None = None, foot_detected: bool = False) -> dict:
        result = self.base_result(reason, None)
        result["no_ball_analysis"] = {
            "distance_past_cm": None, "is_no_ball": None, "foot_position": None,
            "confidence": None, "camera_id": camera_id, "foot_detected": foot_detected,
            "reason": reason, "requires_calibration": False,
        }
        return result

    def _partial(self, reason: str, camera_id: int, foot: FootLocation) -> dict:
        result = self.base_result(reason, foot.confidence)
        result["no_ball_analysis"] = {
            "distance_past_cm": None, "is_no_ball": None, "foot_position": None,
            "confidence": foot.confidence,
            "toe_px": {"x": round(foot.toe_px[0], 1), "y": round(foot.toe_px[1], 1)},
            "heel_px": {"x": round(foot.heel_px[0], 1), "y": round(foot.heel_px[1], 1)},
            "camera_id": camera_id, "foot_detected": True,
            "reason": reason, "requires_calibration": True,
        }
        return result
