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
from core.observation import BailsState, Observation
from core.overlay_contract import check_overlay_payload
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
    elif kind == "wide":
        payload.update(_wide_payload(geometry, calibrators, pose_projectors))
    else:
        payload.update(_marker_payload(decision))
    # Validate at the boundary — warn, never raise. A contract violation must not
    # cost an operator their review; it is logged loudly and asserted in tests.
    check_overlay_payload(payload, context=str(review_type or "unknown"))
    return payload


def _runout_payload(geometry: dict, calibrators, pose_projectors) -> dict:
    camera_id = geometry.get("camera_id")
    # coerce, so a legacy None payload becomes NOT_OBSERVED rather than silently
    # falling through a truthiness test somewhere downstream.
    bails = BailsState.coerce(geometry.get("bails_status"))
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
        "bails_status": bails.value,
        "frame_number": geometry.get("frame_number"),
        # Tri-state, not a boolean. UNKNOWN stays None: False would claim the wicket
        # was NOT broken, which is just as unmeasured as claiming it was.
        "hitting": bails.wicket_broken,
    }


# How far down the pitch the wide line is drawn, from the stump line toward the
# bowler. Long enough to read as a line running down the pitch rather than a tick.
_WIDE_LINE_FROM_MM = 200.0
_WIDE_LINE_TO_MM = -4200.0
_WIDE_LINE_STEPS = 12


def _wide_payload(geometry: dict, calibrators, pose_projectors) -> dict:
    """Wide review geometry. The wide line is projected from world coordinates into
    frame pixels, which is what lets it be drawn as MEASURED (solid, carries a
    number) rather than as a schematic guide. Without a projection it can only be a
    dashed guess, and a guess may not carry a measurement."""
    camera_id = geometry.get("camera_id")
    projection = resolve_projection(camera_id, calibrators or {}, pose_projectors)

    lateral_mm = geometry.get("wide_line_lateral_mm")
    wide_line_px: list[list[float]] = []
    if projection is not None and lateral_mm is not None:
        for step in range(_WIDE_LINE_STEPS + 1):
            along = _WIDE_LINE_FROM_MM + (_WIDE_LINE_TO_MM - _WIDE_LINE_FROM_MM) * step / _WIDE_LINE_STEPS
            point = projection.world_to_pixel(float(lateral_mm), along, 0.0)
            if point is not None:
                wide_line_px.append([round(point[0], 1), round(point[1], 1)])

    ball = geometry.get("ball_px")
    return {
        "projection": getattr(projection, "kind", None),
        # Empty when uncalibrated — the renderer then falls back to the schematic
        # tier and says so, rather than drawing this line somewhere plausible.
        "wide_line_px": wide_line_px,
        "ball_centre": {"x": ball[0], "y": ball[1]} if ball else None,
        "ball_radius_px": geometry.get("ball_radius_px"),
        "distance_cm": geometry.get("distance_cm"),
        "is_wide": geometry.get("is_wide"),
        "frame": geometry.get("frame"),
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
        # Frame-keyed track: each point carries the capture frame it was seen on, so
        # an overlay can FOLLOW the ball as the umpire steps rather than being pinned
        # to one decision frame. `ball_path` / `measured_px` stay as flat polylines
        # for the animation, which is time-driven and needs no frame keys.
        "track": geometry.get("track") or [],
        # Render flag, deliberately Optional[bool] and NOT the raw tri-state string:
        # renderers test `hitting` for truthiness, and any non-empty string is truthy,
        # so an "not_observed" literal here would light the stumps red. None and False
        # both render idle — which is correct, because "missing" and "never checked"
        # look the same on the stumps. The epistemic difference is carried by the
        # WICKETS decision card, which is where an umpire reads it.
        "hitting": Observation.coerce(geometry.get("hitting")).as_optional_bool(),
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


def _fmt(value, absent: str = "—") -> str:
    """Render a card value. `absent` lets a field say WHY it is empty — an
    unmeasured check reads "NOT OBSERVED", not a bare dash that looks like a
    rendering glitch."""
    if value is None or value == "" or value == "--":
        return absent
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
            {"label": "BAILS", "value": BailsState.coerce(run_out.get("bails_status")).label().upper(), "status": "info"},
            {"label": "FRAME", "value": str(run_out.get("frame_number")) if run_out.get("frame_number") is not None else "—", "status": "info"},
        ]
    if review_type != "lbw":
        return []
    wicket = decision.get("wicket_zone_status")
    outcome = decision.get("outcome") or decision.get("status")
    outcome_upper = str(outcome or "").upper()
    outcome_status = "not-out" if "NOT OUT" in outcome_upper else \
        "out" if "OUT" in outcome_upper else "info"
    # The three DRS gates are NOT computed by the live pipeline: nothing sets
    # `pitching_zone` / `impact_zone`, and `wicket_zone_status` is seeded "--" and
    # never written. They render as NOT OBSERVED rather than an em dash — a dash
    # reads as a rendering glitch, while the whole point is to tell the umpire this
    # check did not run so they judge it themselves.
    return [
        {"label": "ORIGINAL DECISION", "value": _fmt(outcome), "status": outcome_status},
        {"label": "PITCHING", "value": _fmt(decision.get("pitching_zone"), absent="NOT OBSERVED"), "status": "info"},
        {"label": "IMPACT", "value": _fmt(decision.get("impact_zone"), absent="NOT OBSERVED"), "status": "info"},
        {"label": "WICKETS", "value": _fmt(wicket, absent="NOT OBSERVED"),
         "status": "out" if wicket == "HITTING" else "not-out" if wicket == "MISSING" else "info"},
    ]
