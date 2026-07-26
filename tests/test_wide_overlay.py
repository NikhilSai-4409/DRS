"""Wide overlay — the first review type built on Evidence Platform v1.

Wide is the smallest complete overlay: one detected ball, one reference line, one
measurement. These tests lock the two things that were missing before, without
which the line could only ever be a schematic guess:

* the wide line projected into frame pixels, and
* a frame reference, so the measurement can be tied to a replay frame.
"""

from __future__ import annotations

from core.frame_ref import FrameSpace
from core.overlay_builder import build_overlay_payload
from core.overlay_contract import Severity, validate_overlay_payload


class _Calibrator:
    """Linear stand-in for a homography: lateral →x, along →y. `resolve_projection`
    wraps this in a HomographyProjection exactly as it does a real calibrator."""

    def pitch_mm_to_pixel(self, camera_id, lateral_mm, along_mm):
        return (640.0 + lateral_mm * 0.25, 360.0 - along_mm * 0.08)


def _wide_decision(**over):
    geometry = {
        "kind": "wide", "camera_id": 1,
        "ball_px": [742.0, 300.0], "ball_radius_px": 9.0,
        "wide_line_lateral_mm": 889.0, "crease_along_mm": -1220.0,
        "distance_cm": 14.3, "is_wide": True,
        "frame": {"space": "capture", "index": 287, "timestamp_ms": 9560.0,
                  "source": "camera:1"},
    }
    geometry.update(over)
    return {"review_type": "wide", "geometry": geometry}


def _payload(**over):
    return build_overlay_payload(_wide_decision(**over), calibrators={1: _Calibrator()})


def test_wide_line_is_projected_into_frame_pixels() -> None:
    payload = _payload()
    line = payload["wide_line_px"]
    assert len(line) >= 2, "the wide line must be a polyline, not a single point"
    xs = {round(p[0], 1) for p in line}
    assert len(xs) == 1, "a constant lateral offset must project to a constant x here"
    ys = [p[1] for p in line]
    assert ys == sorted(ys) or ys == sorted(ys, reverse=True), "line must run monotonically down-pitch"


def test_wide_line_follows_the_side_the_ball_passed() -> None:
    """A correct number drawn against the wrong reference line is still wrong."""
    off = _payload(wide_line_lateral_mm=-889.0)["wide_line_px"]
    leg = _payload(wide_line_lateral_mm=889.0)["wide_line_px"]
    assert off[0][0] < 640.0 < leg[0][0]


def test_uncalibrated_camera_yields_no_line_rather_than_a_guess() -> None:
    """No projection means no measured line. The renderer falls back to the
    schematic tier and says so — it must not place the line somewhere plausible."""
    payload = build_overlay_payload(_wide_decision(), calibrators={})
    assert payload["wide_line_px"] == []
    assert payload["projection"] is None


def test_measurement_and_ball_survive_into_the_payload() -> None:
    payload = _payload()
    assert payload["ball_centre"] == {"x": 742.0, "y": 300.0}
    assert payload["ball_radius_px"] == 9.0          # dropped by the old marker branch
    assert payload["distance_cm"] == 14.3
    assert payload["is_wide"] is True


def test_wide_carries_a_frame_reference() -> None:
    frame = _payload()["frame"]
    assert frame["space"] == FrameSpace.CAPTURE.value
    assert frame["index"] == 287 and frame["source"] == "camera:1"
    assert frame["timestamp_ms"] == 9560.0


def test_wide_payload_satisfies_the_evidence_contract() -> None:
    assert validate_overlay_payload(_payload()) == []


def test_a_frame_without_a_space_is_fatal_for_wide_too() -> None:
    problems = validate_overlay_payload(_payload(frame={"index": 287}))
    assert any(p.severity is Severity.FATAL for p in problems)
