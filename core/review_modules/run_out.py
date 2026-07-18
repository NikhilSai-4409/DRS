"""Run Out review module.

Emphasises the CREASE, not a trajectory. The popping crease is drawn from the
calibration homography; the bat/batter is localised near the crease by a swappable
motion heuristic (:class:`BatLocator` — replace with a trained bat/pose model); the
grounding point of the bat relative to the popping crease decides OUT / NOT OUT.

Bails status and ball possession are honest placeholders until dedicated detectors
exist — the geometry (crease + bat-to-crease distance) is real, the rest is flagged
so the umpire confirms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from core.camera_roles import STUMP
from core.review_modules.base import ReviewContext, ReviewModule
from utils.logger import get_logger

log = get_logger("review_engine")

CREASE_HALF_WIDTH_MM = 1830.0   # popping crease spans ~3.66 m (1.83 m each side of middle)


@dataclass(slots=True)
class BatLocation:
    outline_px: list           # convex-hull points [[x, y], ...]
    ground_px: tuple           # bottom-most point (bat grounding)
    confidence: float
    frame_id: int


class BatLocator:
    """Motion-based bat/batter localiser near the crease. Swap ``locate`` for a
    trained bat/pose model; the decision geometry around it is exact."""

    def __init__(self, motion_threshold: int = 18, min_area: int = 500):
        self.motion_threshold = motion_threshold
        self.min_area = min_area

    def locate(self, frames: list) -> Optional[BatLocation]:
        grays = []
        for vf in frames:
            frame = getattr(vf, "frame", None)
            if frame is None:
                continue
            grays.append((vf.frame_id, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
        if len(grays) < 3:
            return None

        best = None  # (motion, frame_id, diff)
        for (_, prev), (fid, cur) in zip(grays, grays[1:]):
            diff = cv2.absdiff(cur, prev)
            motion = float(diff.sum())
            if best is None or motion > best[0]:
                best = (motion, fid, diff)
        if best is None or best[0] <= 0:
            return None

        _, frame_id, diff = best
        blurred = cv2.GaussianBlur(diff, (5, 5), 0)
        _, mask = cv2.threshold(blurred, self.motion_threshold, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        if cv2.contourArea(contour) < self.min_area:
            return None

        hull = cv2.convexHull(contour).reshape(-1, 2).astype(float)
        ground = hull[int(np.argmax(hull[:, 1]))]   # bottom-most point (grounding)
        strength = min(1.0, cv2.contourArea(contour) / (diff.size * 0.05 + 1.0))
        return BatLocation(
            outline_px=[[float(x), float(y)] for x, y in hull],
            ground_px=(float(ground[0]), float(ground[1])),
            confidence=round(max(0.25, min(0.8, 0.4 + 0.4 * strength)), 3),
            frame_id=int(frame_id),
        )


class RunOutReviewModule(ReviewModule):
    key = "runout"
    label = "Run Out"
    required_role = STUMP
    timeline = ("Appeal", "Crease", "Bat", "Bails", "Decision")
    evidence = ("bat_tip", "crease", "bail_separation", "frame_stepping", "replay")
    replay_mode = "frame_stepping"
    decision_card = ("Bat", "Bails", "Frame", "Decision")
    supports = {"crease": True, "freeze_frame": True, "zoom": True,
                "frame_step": True, "measurement": True}

    def __init__(self, bat_locator: BatLocator | None = None):
        self.bat_locator = bat_locator or BatLocator()

    def analyze(self, ctx: ReviewContext) -> dict:
        camera_id = self.select_camera(ctx)
        if camera_id is None:
            return self._awaiting("No run-out / stump camera available.")
        frames = ctx.frames.get(camera_id, [])[-ctx.max_frames:]
        if len(frames) < 3:
            return self._awaiting("Insufficient replay frames for run-out.", camera_id)

        calibrator = ctx.calibrators.get(camera_id)
        if calibrator is None:
            return self._awaiting("Run-out camera is not calibrated — calibrate the crease to measure.", camera_id)

        bat = self.bat_locator.locate(frames)
        if bat is None:
            return self._awaiting("Bat / batter not detected near the crease.", camera_id)

        mapped = calibrator.pixel_to_pitch_mm(camera_id, *bat.ground_px)
        if mapped is None:
            return self._awaiting("Could not project the bat onto the pitch.", camera_id)

        crease_along = ctx.crease_along_mm
        # >0 = grounded behind the crease (safe); <0 = short of the line (run out).
        distance_mm = mapped[1] - crease_along
        is_out = distance_mm < 0
        distance_cm = distance_mm / 10.0
        bails_status = "dislodged"  # placeholder until a bails detector is added

        result = self.base_result(
            f"Bat {'short of' if is_out else 'grounded behind'} the crease by {abs(distance_cm):.1f} cm "
            f"(frame {bat.frame_id}). Bails detection pending — confirm the call.",
            bat.confidence,
        )
        result["geometry"] = {
            "kind": "runout", "camera_id": camera_id,
            "crease_world": [[-CREASE_HALF_WIDTH_MM, crease_along], [CREASE_HALF_WIDTH_MM, crease_along]],
            "bat_px": bat.outline_px,
            "bails_status": bails_status,
            "frame_number": bat.frame_id,
            "distance_cm": round(distance_cm, 1),
        }
        result["run_out_analysis"] = {
            "distance_cm": round(distance_cm, 1), "is_out": is_out,
            "bails_status": bails_status, "frame_number": bat.frame_id,
            "ball_possession": None, "confidence": bat.confidence, "camera_id": camera_id,
            "requires_calibration": False,
        }
        result["summary"] = {
            "headline": "OUT" if is_out else "NOT OUT",
            "measurements": [
                {"label": "Bat to crease", "value": f"{abs(distance_cm):.1f} cm", "flag": is_out},
                {"label": "Frame", "value": str(bat.frame_id)},
                {"label": "Bails", "value": bails_status.title()},
            ],
            "confidence": bat.confidence,
            "warnings": ["Bat localised by a motion heuristic and bails status is a placeholder — verify before the final call."],
        }
        return result

    def _awaiting(self, reason: str, camera_id: int | None = None) -> dict:
        result = self.base_result(reason, None)
        result["run_out_analysis"] = {
            "distance_cm": None, "is_out": None, "bails_status": None, "frame_number": None,
            "ball_possession": None, "confidence": None, "camera_id": camera_id, "reason": reason,
            "requires_calibration": camera_id is not None,
        }
        return result
