"""Stumping review module.

The geometric question is the same as Run Out — is the bat (or batter) grounded
BEHIND the popping crease at the moment the bails come off? — so the crease
projection and motion-based localisation are shared with
:class:`RunOutReviewModule`. What differs is the evidence emphasis (wicketkeeper
gloves, ball collection, bail removal, bat position) and the timeline. Glove /
ball-collection / bail detection are honest placeholders until dedicated
detectors exist: the geometry (crease + bat-to-crease distance) is real and the
rest is flagged so the umpire confirms.
"""

from __future__ import annotations

from core.camera_roles import STUMP
from core.observation import Observation
from core.review_modules.base import ReviewContext
from core.review_modules.run_out import RunOutReviewModule


class StumpingReviewModule(RunOutReviewModule):
    key = "stumping"
    label = "Stumping"
    required_role = STUMP
    timeline = ("Appeal", "Gloves", "Bails", "Bat", "Decision")
    evidence = ("wicketkeeper_gloves", "ball_collection", "bail_removal",
                "bat_position", "crease", "frame_stepping", "replay")
    replay_mode = "frame_stepping"
    decision_card = ("Gloves", "Bails", "Bat", "Decision")
    # Operator workflow: crease check + bail-removal timing, then decide.
    protocol = (("crease", "Crease Check"), ("timing", "Bail Timing"), ("decision", "Decision"))
    # Stumping assists: frame stepping + bail timing; the umpire decides.
    decision_mode = "assisted"
    supports = {"crease": True, "freeze_frame": True, "zoom": True,
                "frame_step": True, "measurement": True}

    def analyze(self, ctx: ReviewContext) -> dict:
        result = super().analyze(ctx)
        # Re-shape the shared crease analysis into stumping vocabulary. The measured
        # geometry stays; detection gaps are declared, never silently filled.
        analysis = result.pop("run_out_analysis", {})
        # Tri-state, not None: "no detector exists" is a distinct answer from "the
        # keeper had no gloves on the ball". A consumer testing these for truthiness
        # would silently read the gap as a negative finding.
        analysis["gloves_detected"] = Observation.UNKNOWN.value   # pending a keeper-gloves detector
        analysis["ball_collected"] = Observation.UNKNOWN.value    # pending ball-in-gloves detection
        result["stumping_analysis"] = analysis
        # Only annotate a review that actually produced crease data — before any
        # analysis exists there is nothing for the operator to "manually check" yet.
        has_data = analysis.get("is_out") is not None or analysis.get("distance_cm") is not None
        summary = result.get("summary")
        if summary and has_data:
            summary["measurements"] = [
                {"label": "Gloves", "value": "Manual check"},
                *summary.get("measurements", []),
            ]
            summary["warnings"] = [
                "Glove / ball-collection detection pending — confirm keeper had the ball "
                "and broke the stumps fairly before the final call.",
                *summary.get("warnings", []),
            ]
        return result
