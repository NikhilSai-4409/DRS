"""OverlayBuilder — turns an analytical review into a render-ready OverlayPayload.

    ReviewResult (analysis) ─► build_overlay_payload(projection) ─► OverlayPayload ─► OverlayRenderer

This is the ONLY place projection (world → broadcast-image pixels) and graphics
preparation happen. Review modules stay purely analytical: they emit world-space
``geometry`` (observed ball pixels + predicted world arc + markers), and this layer
projects it for a specific broadcast camera and formats the decision cards. Nothing
here draws — that is :mod:`core.overlay_renderer`.
"""

from __future__ import annotations

from config.settings import STUMP_HEIGHT_M
from core.projection import STUMP_HALF_WIDTH_MM, resolve_projection

STUMP_HEIGHT_MM = STUMP_HEIGHT_M * 1000.0


def build_overlay_payload(decision: dict, calibrators: dict | None = None, pose_projectors: dict | None = None) -> dict:
    """Project a decision's analytical geometry into a self-contained OverlayPayload."""
    review_result = decision.get("review_result") or {}
    review_type = review_result.get("review_type") or decision.get("review_type")
    payload: dict = {
        "review_type": review_type,
        "verdict": review_result.get("verdict"),
        "confidence": review_result.get("confidence"),
        "measurements": review_result.get("measurements") or [],
        "decision_cards": decision_cards_for(review_type, decision),
    }
    geometry = decision.get("geometry") or {}
    kind = geometry.get("kind")
    if kind == "lbw":
        payload.update(_lbw_payload(geometry, calibrators, pose_projectors))
    elif kind == "runout":
        payload.update(_runout_payload(geometry, calibrators, pose_projectors))
    else:
        payload.update(_marker_payload(decision))
    return payload


def _runout_payload(geometry: dict, calibrators, pose_projectors) -> dict:
    camera_id = geometry.get("camera_id")
    projection = resolve_projection(camera_id, calibrators or {}, pose_projectors)
    crease_px: list[list[float]] = []
    stumps_px: list[dict] = []
    bails_px: list[dict] = []
    if projection is not None:
        for lateral, along in (geometry.get("crease_world") or []):
            point = projection.world_to_pixel(lateral, along, 0.0)
            if point is not None:
                crease_px.append([round(point[0], 1), round(point[1], 1)])
        stumps_px = _stump_pixels(projection)
        for lateral in (-STUMP_HALF_WIDTH_MM, 0.0, STUMP_HALF_WIDTH_MM):
            top = projection.world_to_pixel(lateral, 0.0, STUMP_HEIGHT_MM)
            if top is not None:
                bails_px.append({"x": round(top[0], 1), "y": round(top[1], 1)})
    return {
        "projection": getattr(projection, "kind", None),
        "crease_px": crease_px,
        "bat_px": geometry.get("bat_px") or [],
        "stumps_px": stumps_px,
        "bails_px": bails_px,
        "bails_status": geometry.get("bails_status"),
        "frame_number": geometry.get("frame_number"),
        "hitting": geometry.get("bails_status") == "dislodged",
    }


def _lbw_payload(geometry: dict, calibrators, pose_projectors) -> dict:
    camera_id = geometry.get("camera_id")
    projection = resolve_projection(camera_id, calibrators or {}, pose_projectors)

    measured = geometry.get("measured") or []                 # [[cx, cy, lateral_mm, along_mm], ...]
    measured_px = [[m[0], m[1], (m[3] if len(m) > 3 and m[3] is not None else 0.0)] for m in measured]
    shadow_px: list[list[float]] = []
    predicted_px: list[list[float]] = []
    bounce_px = None
    stumps_px: list[dict] = []

    if projection is not None:
        for point in measured:
            if len(point) >= 4 and point[2] is not None and point[3] is not None:
                ground = projection.world_to_pixel(point[2], point[3], 0.0)
                if ground is not None:
                    shadow_px.append([round(ground[0], 1), round(ground[1], 1)])
        for wx, wy, wz in (geometry.get("predicted_world") or []):
            lateral_mm, along_mm, height_mm = wx * 1000.0, wy * 1000.0, max(0.0, wz) * 1000.0
            arc = projection.world_to_pixel(lateral_mm, along_mm, height_mm)
            if arc is not None:
                predicted_px.append([round(arc[0], 1), round(arc[1], 1), round(along_mm, 1)])
            ground = projection.world_to_pixel(lateral_mm, along_mm, 0.0)
            if ground is not None:
                shadow_px.append([round(ground[0], 1), round(ground[1], 1)])
        bounce = geometry.get("bounce_world")
        if bounce:
            projected = projection.world_to_pixel(bounce[0] * 1000.0, bounce[1] * 1000.0, 0.0)
            if projected is not None:
                bounce_px = {"x": round(projected[0], 1), "y": round(projected[1], 1)}
        stumps_px = _stump_pixels(projection)

    impact = geometry.get("impact_px")
    impact_px = {"x": impact[0], "y": impact[1]} if impact else None
    ball_path = [[m[0], m[1]] for m in measured_px] + [[p[0], p[1]] for p in predicted_px]
    return {
        "projection": getattr(projection, "kind", None),
        "measured_px": measured_px,
        "predicted_px": predicted_px,
        "shadow_px": shadow_px,
        "bounce_px": bounce_px,
        "impact_px": impact_px,
        "stumps_px": stumps_px,
        "hitting": geometry.get("hitting"),
        "ball_path": ball_path,
    }


def _marker_payload(decision: dict) -> dict:
    """Observed pixel markers for the simple (Wide / No Ball) renderer path."""
    payload: dict = {}
    wide = decision.get("wide_analysis") or {}
    if wide.get("ball_centre"):
        payload["ball_centre"] = wide["ball_centre"]
    no_ball = decision.get("no_ball_analysis") or {}
    if no_ball.get("toe_px"):
        payload["toe_px"] = no_ball["toe_px"]
        payload["heel_px"] = no_ball.get("heel_px")
    return payload


def _stump_pixels(projection) -> list[dict]:
    points = []
    for lateral in (-STUMP_HALF_WIDTH_MM, 0.0, STUMP_HALF_WIDTH_MM):
        pixel = projection.world_to_pixel(lateral, 0.0, 0.0)
        if pixel is not None:
            points.append({"x": round(pixel[0], 1), "y": round(pixel[1], 1)})
    return points


def _fmt(value) -> str:
    if value is None or value == "" or value == "--":
        return "—"
    return str(value).upper()


def decision_cards_for(review_type: str, decision: dict) -> list[dict]:
    """The cards that animate in beside the overlay. Per review type; other types
    have none (their summary carries the verdict)."""
    if review_type in {"runout", "run_out"}:
        run_out = decision.get("run_out_analysis") or {}
        is_out = run_out.get("is_out")
        distance = run_out.get("distance_cm")
        return [
            {"label": "DECISION", "value": "OUT" if is_out else "NOT OUT" if is_out is False else "—",
             "status": "out" if is_out else "not-out" if is_out is False else "info"},
            {"label": "BAT TO CREASE", "value": f"{abs(distance):.1f} cm" if distance is not None else "—", "status": "info"},
            {"label": "BAILS", "value": _fmt(run_out.get("bails_status")), "status": "info"},
            {"label": "FRAME", "value": str(run_out.get("frame_number")) if run_out.get("frame_number") is not None else "—", "status": "info"},
        ]
    if review_type != "lbw":
        return []
    wicket = decision.get("wicket_zone_status")
    outcome = decision.get("outcome") or decision.get("status")
    outcome_upper = str(outcome or "").upper()
    outcome_status = "not-out" if "NOT OUT" in outcome_upper else \
        "out" if "OUT" in outcome_upper else "info"
    return [
        {"label": "ORIGINAL DECISION", "value": _fmt(outcome), "status": outcome_status},
        {"label": "PITCHING", "value": _fmt(decision.get("pitching_zone")), "status": "info"},
        {"label": "IMPACT", "value": _fmt(decision.get("impact_zone")), "status": "info"},
        {"label": "WICKETS", "value": _fmt(wicket),
         "status": "out" if wicket == "HITTING" else "not-out" if wicket == "MISSING" else "info"},
    ]
