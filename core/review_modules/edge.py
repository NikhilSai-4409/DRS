"""UltraEdge (Edge) review module.

Same ReviewModule interface as the others. Internally it consults the two edge
signals — UltraEdge (stump-mic audio, :class:`~core.audio_edge.AudioEdgeDetector`)
and HotSpot (optical-flow contact proxy, :class:`~core.hotspot.HotSpotAnalyzer`) —
and returns the unified result.

Synchronized stump-mic audio is not yet carried on the :class:`ReviewContext`, so
without it the audio verdict is reported honestly as INCONCLUSIVE rather than
guessed; HotSpot still runs on the replay frames. When audio is wired into the
context, fill :meth:`_ultraedge` and the verdict resolves automatically.
"""

from __future__ import annotations

from core.camera_roles import ULTRA_EDGE
from core.review_modules.base import ReviewContext, ReviewModule


class EdgeReviewModule(ReviewModule):
    key = "edge"
    label = "UltraEdge"
    required_role = ULTRA_EDGE
    timeline = ("Approach", "Bat-Pad", "Contact", "Decision")
    evidence = ("waveform", "spike", "snickometer", "hotspot", "frame_sync", "replay")
    replay_mode = "audio_sync"
    decision_card = ("Spike", "HotSpot", "Decision")
    supports = {"audio": True, "frame_step": True, "zoom": True, "measurement": True}

    def analyze(self, ctx: ReviewContext) -> dict:
        camera_id = self.select_camera(ctx)
        frames = ctx.frames.get(camera_id, []) if camera_id is not None else []

        edge_analysis = self._ultraedge(ctx, camera_id)
        hotspot_analysis = self._hotspot(frames)

        result = self.base_result(
            "UltraEdge requires synchronized stump-mic audio; HotSpot contact proxy shown.",
            None,
        )
        result["edge_analysis"] = edge_analysis
        result["hotspot_analysis"] = hotspot_analysis

        warnings = ["UltraEdge audio feed not connected — verdict inconclusive."]
        if hotspot_analysis.get("contact_detected"):
            warnings.append("HotSpot motion proxy flagged contact — not real thermal imaging.")
        result["summary"] = {
            "headline": "Inconclusive",
            "measurements": [
                {"label": "Edge probability", "value": f"{edge_analysis['edge_probability'] * 100:.0f}%"},
                {
                    "label": "HotSpot contact",
                    "value": "Yes" if hotspot_analysis.get("contact_detected") else "No",
                    "flag": bool(hotspot_analysis.get("contact_detected")),
                },
            ],
            "confidence": None,
            "warnings": warnings,
        }
        return result

    def _ultraedge(self, ctx: ReviewContext, camera_id) -> dict:
        # No synchronized audio on the context yet — report inconclusive, never guess.
        return {
            "edge_probability": 0.0,
            "events": [],
            "inconclusive": True,
            "camera_id": camera_id,
            "reason": "No synchronized stump-mic audio — UltraEdge needs the audio feed.",
        }

    def _hotspot(self, frames) -> dict:
        images = [getattr(vf, "frame", None) for vf in frames]
        images = [image for image in images if image is not None]
        if len(images) < 2:
            return {
                "contact_detected": False,
                "confidence": 0.0,
                "reason": "Need at least two frames around contact for HotSpot.",
                "contact_region": None,
            }
        try:
            from core.hotspot import HotSpotAnalyzer

            res = HotSpotAnalyzer().analyze_contact(images, len(images) // 2)
            return {
                "contact_detected": bool(res.contact_detected),
                "confidence": round(float(res.confidence), 3),
                "reason": res.reason,
                "contact_region": list(res.contact_region) if res.contact_region else None,
            }
        except Exception as exc:  # HotSpot must never break a review
            return {
                "contact_detected": False,
                "confidence": 0.0,
                "reason": f"HotSpot unavailable: {exc}",
                "contact_region": None,
            }
