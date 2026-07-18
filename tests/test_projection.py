"""Tests for the projection engine + trajectory overlay payload."""

from __future__ import annotations

from types import SimpleNamespace

from core.overlay_builder import build_overlay_payload, decision_cards_for
from core.projection import HomographyProjection, PoseProjection, resolve_projection


class _Calibrator:
    """Linear pixel<->pitch map: x = 640 + lateral_mm/4, y = 250 - along_mm/4."""

    def pitch_mm_to_pixel(self, camera_id, lateral_mm, along_mm):
        return (640.0 + lateral_mm / 4.0, 250.0 - along_mm / 4.0)


def test_homography_projection_ground_matches_calibrator():
    proj = HomographyProjection(_Calibrator(), camera_id=0)
    assert proj.available is True
    px = proj.world_to_pixel(0.0, 0.0, 0.0)
    assert px == (640.0, 250.0)
    px2 = proj.world_to_pixel(400.0, -1000.0, 0.0)
    assert px2 == (640.0 + 100.0, 250.0 + 250.0)


def test_homography_projection_lifts_with_height():
    proj = HomographyProjection(_Calibrator(), camera_id=0)
    ground = proj.world_to_pixel(0.0, 0.0, 0.0)
    lifted = proj.world_to_pixel(0.0, 0.0, 500.0)
    assert lifted[0] == ground[0]        # same horizontal position
    assert lifted[1] < ground[1]         # higher ball → higher in image (smaller y)


def test_resolve_projection_prefers_pose_then_homography_then_none():
    calibrators = {0: _Calibrator()}
    assert isinstance(resolve_projection(0, calibrators), HomographyProjection)
    assert resolve_projection(1, calibrators) is None

    pose = SimpleNamespace(world_to_pixel=lambda x, y, z: (10.0, 20.0))
    model = resolve_projection(0, calibrators, pose_projectors={0: pose})
    assert isinstance(model, PoseProjection)
    assert model.world_to_pixel(0.0, 0.0, 0.0) == (10.0, 20.0)


def test_build_overlay_payload_projects_measured_predicted_and_markers():
    # OverlayBuilder projects the module's analytical geometry — no drawing.
    decision = {
        "review_type": "lbw",
        "outcome": "OUT",
        "wicket_zone_status": "HITTING",
        "review_result": {"review_type": "lbw", "verdict": "HITTING", "confidence": 0.9, "measurements": []},
        "geometry": {
            "kind": "lbw", "camera_id": 0,
            "measured": [[600, 240, -160.0, 400.0], [640, 250, 0.0, 0.0], [660, 260, 80.0, -400.0]],
            "predicted_world": [[0.0, -0.4, 0.4], [0.05, -1.0, 0.2], [0.1, -1.6, 0.05]],
            "bounce_world": [0.05, -1.0],
            "impact_px": [660, 260],
            "hitting": True,
        },
    }
    payload = build_overlay_payload(decision, calibrators={0: _Calibrator()})

    assert payload["review_type"] == "lbw" and payload["verdict"] == "HITTING"
    assert payload["projection"] == "homography"
    assert payload["measured_px"][0][:2] == [600, 240]     # observed pixels passthrough
    assert len(payload["predicted_px"]) == 3               # projected arc
    assert payload["shadow_px"]                             # ground projections present
    assert payload["bounce_px"] is not None
    assert payload["impact_px"] == {"x": 660, "y": 260}
    assert len(payload["stumps_px"]) == 3                   # off/middle/leg
    assert payload["hitting"] is True
    assert len(payload["ball_path"]) == 6
    # decision cards are built here (graphics prep), not in the module
    assert [c["label"] for c in payload["decision_cards"]][0] == "ORIGINAL DECISION"


def test_build_overlay_payload_without_calibration_keeps_measured_only():
    decision = {
        "review_type": "lbw",
        "review_result": {"review_type": "lbw", "verdict": "MISSING", "confidence": None, "measurements": []},
        "geometry": {"kind": "lbw", "camera_id": 9, "measured": [[600, 240, None, None]],
                     "predicted_world": [], "bounce_world": None, "impact_px": [600, 240], "hitting": None},
    }
    payload = build_overlay_payload(decision, calibrators={0: _Calibrator()})  # camera 9 uncalibrated
    assert payload["projection"] is None
    assert payload["measured_px"] == [[600, 240, 0.0]]     # observed pixels still available
    assert payload["predicted_px"] == [] and payload["stumps_px"] == []


def test_decision_cards_lbw_only():
    lbw = decision_cards_for("lbw", {"outcome": "OUT", "wicket_zone_status": "HITTING"})
    labels = [c["label"] for c in lbw]
    assert labels == ["ORIGINAL DECISION", "PITCHING", "IMPACT", "WICKETS"]
    assert lbw[0]["status"] == "out"
    assert lbw[3]["value"] == "HITTING" and lbw[3]["status"] == "out"
    assert decision_cards_for("wide", {"outcome": "WIDE"}) == []
