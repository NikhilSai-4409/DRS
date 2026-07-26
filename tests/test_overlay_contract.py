"""Contract tests for the overlay payload.

These lock the "every value the pipeline produces is semantically correct"
milestone: an unmeasured check must never render as a negative finding, and the
render flags must be shaped so a renderer cannot misread them.
"""

from __future__ import annotations

import pytest

from core.observation import BailsState, Observation
from core.overlay_contract import Severity, suppressed_keys, validate_overlay_payload


def test_valid_payload_has_no_violations() -> None:
    assert validate_overlay_payload({
        "bails_status": BailsState.NOT_OBSERVED.value,
        "hitting": None,
        "frame": {"space": "capture", "index": 194, "timestamp_ms": 6453.0,
                  "source": "camera:1"},
    }) == []


def test_capture_frame_without_a_source_is_only_a_warning() -> None:
    """Degraded, not misleading: the index is still usable within one camera."""
    problems = validate_overlay_payload(
        {"frame": {"space": "capture", "index": 194, "timestamp_ms": 1.0}})
    assert [p.severity for p in problems] == [Severity.WARNING]
    assert suppressed_keys(problems) == set()


def test_hitting_must_not_be_a_string() -> None:
    """A tri-state literal in the render flag is truthy — it would light the stumps
    red and tell the umpire the ball hit the wicket. That is misleading evidence,
    so it is FATAL and the element is suppressed."""
    problems = validate_overlay_payload({"hitting": Observation.UNKNOWN.value})
    assert any(p.severity is Severity.FATAL and p.field == "hitting" for p in problems)
    assert "hitting" in suppressed_keys(problems)


def test_unobserved_bails_cannot_claim_a_wicket_strike() -> None:
    problems = validate_overlay_payload(
        {"bails_status": BailsState.NOT_OBSERVED.value, "hitting": True})
    assert any(p.severity is Severity.FATAL for p in problems)
    assert "hitting" in suppressed_keys(problems)


def test_fatal_suppresses_only_its_own_element() -> None:
    """A bad marker must not cost the umpire the whole review — only the marker."""
    problems = validate_overlay_payload({
        "measured_px": [[1.0, 2.0]], "transition_px": [99.0, 99.0],
        "bails_status": BailsState.NOT_OBSERVED.value, "hitting": None,
    })
    assert suppressed_keys(problems) == {"transition_px"}


def test_legacy_none_bails_is_rejected_as_a_literal() -> None:
    """None is tolerated (legacy) but a bogus string is not."""
    assert validate_overlay_payload({"bails_status": None}) == []
    assert validate_overlay_payload({"bails_status": "maybe"}) != []


@pytest.mark.parametrize("field", ["gloves_detected", "ball_collected", "ball_possession"])
def test_tristate_fields_must_use_observation_literals(field: str) -> None:
    assert validate_overlay_payload({field: Observation.UNKNOWN.value}) == []
    assert validate_overlay_payload({field: False}) != []      # a bare bool loses "unknown"
    assert validate_overlay_payload({field: None}) != []


def test_frame_reference_must_name_its_coordinate_space() -> None:
    """capture-counter and replay-clip indices are different spaces; comparing them
    silently is the mismatch this check exists to prevent."""
    assert validate_overlay_payload({"frame": {"index": 194, "timestamp_ms": 1.0}}) != []
    assert validate_overlay_payload({"frame": {"space": "wallclock", "index": 1, "timestamp_ms": 1.0}}) != []
    assert validate_overlay_payload({"frame": {"space": "capture", "index": 194}}) != []   # needs a timestamp
    assert validate_overlay_payload({"frame": {"space": "clip", "index": 12}}) == []


def test_transition_marker_must_be_derived_from_the_measured_path() -> None:
    path = [[10.0, 20.0], [30.0, 40.0]]
    assert validate_overlay_payload({"measured_px": path, "transition_px": [30.0, 40.0]}) == []
    assert validate_overlay_payload({"measured_px": path, "transition_px": [99.0, 99.0]}) != []
    assert validate_overlay_payload({"transition_px": [1.0, 2.0]}) != []   # no path at all


@pytest.mark.parametrize("kind,geometry", [
    ("runout", {"kind": "runout", "camera_id": 1, "bat_px": [[10, 20], [30, 40]],
                "crease_world": [], "bails_status": BailsState.NOT_OBSERVED.value,
                "frame_number": 412}),
    ("lbw", {"kind": "lbw", "camera_id": 0, "measured": [[10.0, 20.0, None, None]],
             "predicted_world": [], "bounce_world": None, "impact_px": [10.0, 20.0],
             "hitting": Observation.UNKNOWN.value}),
])
def test_builder_output_satisfies_its_own_contract(kind: str, geometry: dict) -> None:
    """The real builder output must pass — this is what catches a regression where a
    tri-state literal leaks into a render flag."""
    from core.overlay_builder import build_overlay_payload

    payload = build_overlay_payload({"review_type": kind, "geometry": geometry}, calibrators={})
    assert validate_overlay_payload(payload) == []
    # the LBW render flag must survive as Optional[bool], never the wire literal
    assert payload.get("hitting") in (None, True, False)
