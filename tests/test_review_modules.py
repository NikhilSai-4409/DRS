"""Tests for the modular review engine (Wide + Front Foot No Ball geometry).

These exercise the module logic with an exact linear pixel<->pitch calibrator so
the geometry/decision is deterministic. The real perspective homography is covered
separately in test_pitch_calibration.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.camera_roles import FRONT_FOOT, WIDE, canonical_role
from core.observation import BailsState, Observation
from core.review_modules import build_review_result, get_module, run_review, supported_types
from core.review_modules.base import ReviewContext
from core.review_modules.edge import EdgeReviewModule
from core.review_modules.lbw import LbwReviewModule
from core.review_modules.no_ball import FootLocation, NoBallReviewModule
from core.review_modules.run_out import BatLocation, RunOutReviewModule
from core.review_modules.stumping import StumpingReviewModule
from core.review_modules.wide import WideReviewModule


class LinearCalibrator:
    """Exact, invertible pixel<->pitch map. lateral 0 at x=640, crease (along<0) below."""

    MM_PER_PX = 4.0
    ORIGIN_X = 640.0
    STUMP_Y = 250.0

    def pixel_to_pitch_mm(self, camera_id, x, y):
        lateral_mm = (x - self.ORIGIN_X) * self.MM_PER_PX
        along_mm = -(y - self.STUMP_Y) * self.MM_PER_PX
        return (lateral_mm, along_mm)

    def pitch_mm_to_pixel(self, camera_id, lateral_mm, along_mm):
        x = self.ORIGIN_X + lateral_mm / self.MM_PER_PX
        y = self.STUMP_Y - along_mm / self.MM_PER_PX
        return (x, y)


def _frame(frame_id: int):
    return SimpleNamespace(frame=None, frame_id=frame_id, timestamp_ms=float(frame_id) * 16.0)


class _StubDetector:
    def __init__(self, pixels_by_fid: dict[int, tuple[float, float]]):
        self.pixels_by_fid = pixels_by_fid

    def detect(self, frame, frame_id, timestamp_ms, camera_id):
        px = self.pixels_by_fid.get(frame_id)
        best = None
        if px is not None:
            x, y = px
            best = SimpleNamespace(
                frame_id=frame_id, timestamp_ms=timestamp_ms,
                cx=x, cy=y, confidence=0.9,
                x1=x - 6, y1=y - 6, x2=x + 6, y2=y + 6,
            )
        return SimpleNamespace(best=best)


def _wide_context(lateral_mm: float, calibrated: bool = True) -> ReviewContext:
    cal = LinearCalibrator()
    alongs = {1: -1100.0, 2: -1220.0, 3: -1340.0}  # bracket the popping crease
    pixels = {fid: cal.pitch_mm_to_pixel(1, lateral_mm, along) for fid, along in alongs.items()}
    return ReviewContext(
        review_type="wide",
        frames={1: [_frame(1), _frame(2), _frame(3)]},
        detector=_StubDetector(pixels),
        calibrators={1: cal} if calibrated else {},
        camera_roles={1: "Wide Camera"},
        primary_camera_id=1,
    )


def test_registry_supports_all_review_types() -> None:
    assert isinstance(get_module("lbw"), LbwReviewModule)
    assert isinstance(get_module("wide"), WideReviewModule)
    assert isinstance(get_module("noball"), NoBallReviewModule)
    assert isinstance(get_module("edge"), EdgeReviewModule)
    assert isinstance(get_module("runout"), RunOutReviewModule)
    assert get_module("no_ball") is get_module("noball")    # alias
    assert get_module("ultraedge") is get_module("edge")    # alias
    assert get_module("run_out") is get_module("runout")    # alias
    assert get_module("stump") is get_module("stumping")    # alias
    assert set(supported_types()) == {"lbw", "wide", "noball", "edge", "runout", "stumping"}


def test_operator_protocol_is_distinct_per_type() -> None:
    # The operator protocol drives the review workspace: each type declares its OWN
    # ordered stages, every protocol ends at "decision", and only LBW ever contains
    # a trajectory stage — a Wide review must never learn trajectory exists.
    expected = {
        "lbw": ["front_foot", "ultra_edge", "trajectory", "decision"],
        "wide": ["wide_line", "decision"],
        "noball": ["front_foot", "decision"],
        "edge": ["audio_sync", "decision"],
        "runout": ["crease", "decision"],
        "stumping": ["crease", "timing", "decision"],
    }
    for key, stages in expected.items():
        contract = get_module(key).describe()
        assert [s["key"] for s in contract["protocol"]] == stages, key
        assert all(s["label"] for s in contract["protocol"]), key
    for key in expected:
        if key != "lbw":
            assert "trajectory" not in [s["key"] for s in get_module(key).describe()["protocol"]]
    # Broadcast-DRS decision modes: measurement-backed types decide automatically;
    # Edge / Run Out / Stumping ASSIST — their readings are advisory, umpire decides.
    modes = {key: get_module(key).describe()["decision_mode"] for key in expected}
    assert modes == {"lbw": "automatic", "wide": "automatic", "noball": "automatic",
                     "edge": "assisted", "runout": "assisted", "stumping": "assisted"}


def test_lbw_no_ball_voids_dismissal(monkeypatch) -> None:
    # DRS protocol: a front-foot NO BALL voids the dismissal — the LBW verdict
    # overrides to NOT OUT and the decision card's first row is the flagged check.
    from core.review_modules.no_ball import NoBallReviewModule

    monkeypatch.setattr(NoBallReviewModule, "analyze", lambda self, ctx: {
        "no_ball_analysis": {"is_no_ball": True, "distance_past_cm": 3.2}})
    ctx = ReviewContext(
        review_type="lbw", frames={0: [_frame(1), _frame(2), _frame(3)]}, detector=None,
        calibrators={}, camera_roles={0: "Ball Tracking"}, primary_camera_id=0)
    result = run_review("lbw", ctx)
    rr = build_review_result("lbw", result)
    assert rr["verdict"] == "NOT OUT - NO BALL"
    first = result["summary"]["measurements"][0]
    assert first["label"] == "No Ball" and first["flag"] is True
    assert any("cannot be out LBW" in w for w in result["summary"]["warnings"])


def test_lbw_no_ball_short_circuits_the_protocol(monkeypatch) -> None:
    # TV-umpiring flow: an overstep ENDS the review at step 1 — no UltraEdge, no
    # ball tracking ever runs, and the result carries the short-circuit marker.
    from core.review_modules.no_ball import NoBallReviewModule

    monkeypatch.setattr(NoBallReviewModule, "analyze", lambda self, ctx: {
        "no_ball_analysis": {"is_no_ball": True, "distance_past_cm": 3.2}})
    monkeypatch.setattr(LbwReviewModule, "detect_samples",
                        lambda self, ctx, camera_id: pytest.fail("ball tracking ran after a NO BALL"))
    ctx = ReviewContext(
        review_type="lbw", frames={0: [_frame(1), _frame(2), _frame(3)]}, detector=None,
        calibrators={}, camera_roles={0: "Ball Tracking"}, primary_camera_id=0)
    result = run_review("lbw", ctx)
    assert result["review_ended"] == "no_ball"
    assert "edge_analysis" not in result            # UltraEdge never ran
    assert "trajectory" not in result               # tracking never ran
    assert build_review_result("lbw", result)["verdict"] == "NOT OUT - NO BALL"
    labels = {m["label"]: m["value"] for m in result["summary"]["measurements"]}
    assert labels["UltraEdge"] == "Not needed — review ended"


def test_edge_verdict_never_fabricated_without_analysis() -> None:
    # NO EDGE is a real finding — with no audio and no heatmap analysed, the only
    # honest verdict is AWAITING (the old seeded {probability: 0.0} read as NO EDGE).
    assert build_review_result("edge", {})["verdict"] == "AWAITING"
    assert build_review_result("edge", {"edge_analysis": {"edge_probability": 0.0, "events": []},
                                        "hotspot_analysis": {"contact_detected": False}})["verdict"] == "NO EDGE"


def test_timeline_stays_neutral_until_analysis_completes() -> None:
    # Honest timeline: an awaiting review paints NO completed stages (green means
    # verified); a completed analysis marks them all complete.
    wide = WideReviewModule()
    pending = wide.base_result("awaiting", confidence=None)["timeline"]
    done = wide.base_result("done", confidence=0.9)["timeline"]
    assert all(row["status"] == "pending" for row in pending)
    assert all(row["status"] == "complete" for row in done)


def test_lbw_verdict_not_hijacked_by_merged_precheck_analysis() -> None:
    # An LBW decision now legitimately CARRIES no_ball/edge pre-check blocks; the
    # declared review type must keep verdict precedence (regression: presence-based
    # fallback used to reroute an LBW verdict to "LEGAL").
    rr = build_review_result("lbw", {
        "no_ball_analysis": {"is_no_ball": False},
        "edge_analysis": {"edge_probability": 0.0},
        "wicket_zone_status": "HITTING",
    })
    assert rr["verdict"] == "HITTING"


def test_wide_ball_outside_line_is_wide() -> None:
    # 1.00 m from middle stump, wide line at 0.889 m -> ~11 cm outside -> WIDE.
    result = run_review("wide", _wide_context(lateral_mm=1000.0))
    wide = result["wide_analysis"]
    assert wide["is_wide"] is True
    assert wide["distance_cm"] == pytest.approx(11.1, abs=0.5)
    assert wide["lateral_offset_cm"] == pytest.approx(100.0, abs=0.5)
    assert 0.0 <= wide["confidence"] <= 1.0
    assert wide["ball_radius_px"] is not None
    # generic summary block (measurements / confidence / warnings)
    assert result["summary"]["headline"] == "WIDE"
    assert any(m["value"].endswith("cm") for m in result["summary"]["measurements"])
    assert isinstance(result["summary"]["warnings"], list)


def test_wide_clears_lbw_fields() -> None:
    result = run_review("wide", _wide_context(lateral_mm=1000.0))
    assert result["trajectory"] == []
    assert result["impact_point"] is None
    assert result["review_type"] == "wide"


def test_wide_ball_inside_line_not_wide() -> None:
    wide = run_review("wide", _wide_context(lateral_mm=500.0))["wide_analysis"]
    assert wide["is_wide"] is False
    assert wide["distance_cm"] < 0


def test_wide_without_calibration_requests_calibration() -> None:
    wide = run_review("wide", _wide_context(lateral_mm=1000.0, calibrated=False))["wide_analysis"]
    assert wide["is_wide"] is None
    assert wide["requires_calibration"] is True
    assert wide["ball_centre"] is not None  # pixel data still reported


class _StubFootLocator:
    def __init__(self, toe_px, heel_px):
        self.toe_px = toe_px
        self.heel_px = heel_px

    def locate(self, frames):
        return FootLocation(toe_px=self.toe_px, heel_px=self.heel_px, confidence=0.6, landing_frame_id=2)


def _no_ball_result(toe_along_mm, heel_along_mm, lateral_mm=0.0, calibrated=True):
    cal = LinearCalibrator()
    toe_px = cal.pitch_mm_to_pixel(1, lateral_mm, toe_along_mm)
    heel_px = cal.pitch_mm_to_pixel(1, lateral_mm, heel_along_mm)
    module = NoBallReviewModule(foot_locator=_StubFootLocator(toe_px, heel_px))
    ctx = ReviewContext(
        review_type="noball",
        frames={1: [_frame(1), _frame(2), _frame(3)]},
        detector=None,
        calibrators={1: cal} if calibrated else {},
        camera_roles={1: "Front Foot"},
        primary_camera_id=1,
    )
    return module.analyze(ctx)["no_ball_analysis"]


def test_no_ball_heel_past_crease_is_no_ball() -> None:
    # Back of the foot 12 cm past the crease -> NO BALL.
    cal = LinearCalibrator()
    module = NoBallReviewModule(foot_locator=_StubFootLocator(
        cal.pitch_mm_to_pixel(1, 0.0, -1000.0), cal.pitch_mm_to_pixel(1, 0.0, -1100.0)))
    ctx = ReviewContext(
        review_type="noball", frames={1: [_frame(1), _frame(2), _frame(3)]}, detector=None,
        calibrators={1: cal}, camera_roles={1: "Front Foot"}, primary_camera_id=1)
    result = module.analyze(ctx)
    nb = result["no_ball_analysis"]
    assert nb["is_no_ball"] is True
    assert nb["distance_past_cm"] == pytest.approx(12.0, abs=0.5)
    assert nb["foot_position"] == "Past line"
    assert result["summary"]["headline"] == "NO BALL"


def test_no_ball_foot_behind_crease_is_legal() -> None:
    nb = _no_ball_result(toe_along_mm=-1250.0, heel_along_mm=-1350.0)
    assert nb["is_no_ball"] is False
    assert nb["distance_past_cm"] < 0
    assert nb["foot_position"] == "Behind line"


def test_no_ball_without_calibration_requests_calibration() -> None:
    nb = _no_ball_result(toe_along_mm=-1000.0, heel_along_mm=-1100.0, calibrated=False)
    assert nb["is_no_ball"] is None
    assert nb["requires_calibration"] is True
    assert nb["foot_detected"] is True


# --- unified ReviewResult + canonical roles ---------------------------------

def test_wide_emits_unified_review_result() -> None:
    result = run_review("wide", _wide_context(lateral_mm=1000.0))
    rr = result["review_result"]
    assert rr["review_type"] == "wide"
    assert rr["verdict"] == "WIDE"
    assert 0.0 <= rr["confidence"] <= 1.0
    assert isinstance(rr["measurements"], list) and rr["measurements"]
    assert rr["summary"]["headline"] == "WIDE"
    assert "ball_centre" in rr["overlays"]
    assert isinstance(rr["warnings"], list)


def test_not_wide_verdict_in_review_result() -> None:
    rr = run_review("wide", _wide_context(lateral_mm=500.0))["review_result"]
    assert rr["verdict"] == "NOT WIDE"


def test_no_ball_unified_result_from_decision() -> None:
    cal = LinearCalibrator()
    module = NoBallReviewModule(foot_locator=_StubFootLocator(
        cal.pitch_mm_to_pixel(1, 0.0, -1000.0), cal.pitch_mm_to_pixel(1, 0.0, -1100.0)))
    ctx = ReviewContext(
        review_type="noball", frames={1: [_frame(1), _frame(2), _frame(3)]}, detector=None,
        calibrators={1: cal}, camera_roles={1: "Front Foot"}, primary_camera_id=1)
    rr = build_review_result("noball", module.analyze(ctx))
    assert rr["verdict"] == "NO BALL"
    assert rr["summary"]["headline"] == "NO BALL"
    assert "toe_px" in rr["overlays"]


def test_canonical_role_alias_routes_to_module_camera() -> None:
    assert canonical_role("Wide Camera") == WIDE
    assert canonical_role("front_foot") == FRONT_FOOT
    base = _wide_context(lateral_mm=1000.0)
    # A lowercase shorthand role still selects the wide camera (no primary set).
    ctx = ReviewContext(
        review_type="wide", frames=base.frames, detector=base.detector,
        calibrators=base.calibrators, camera_roles={1: "wide"}, primary_camera_id=None)
    assert run_review("wide", ctx)["review_result"]["verdict"] == "WIDE"


def test_review_context_is_immutable() -> None:
    import dataclasses

    ctx = _wide_context(lateral_mm=1000.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.review_type = "noball"  # type: ignore[misc]


# --- LBW + Edge through the same interface -----------------------------------

def test_lbw_module_detects_and_emits_review_result() -> None:
    cal = LinearCalibrator()
    alongs = {1: -200.0, 2: 0.0, 3: 200.0}
    pixels = {fid: cal.pitch_mm_to_pixel(1, 50.0, along) for fid, along in alongs.items()}
    ctx = ReviewContext(
        review_type="lbw", frames={1: [_frame(1), _frame(2), _frame(3)]},
        detector=_StubDetector(pixels), calibrators={1: cal},
        camera_roles={1: "Ball Tracking"}, primary_camera_id=1)
    result = run_review("lbw", ctx)
    assert result["review_type"] == "lbw"
    assert result["detection_rate"] == 1.0
    assert result["avg_confidence"] > 0
    assert result["review_result"]["review_type"] == "lbw"
    assert isinstance(result["summary"]["measurements"], list)


def test_lbw_module_honest_without_detections() -> None:
    ctx = ReviewContext(
        review_type="lbw", frames={1: [_frame(1), _frame(2), _frame(3)]},
        detector=_StubDetector({}), calibrators={1: LinearCalibrator()},
        camera_roles={1: "Ball Tracking"}, primary_camera_id=1)
    result = run_review("lbw", ctx)
    assert result["detection_rate"] == 0.0
    assert "trajectory" not in result  # seeded prototype trajectory left untouched
    assert result["summary"]["warnings"]


def test_edge_module_inconclusive_without_audio() -> None:
    ctx = ReviewContext(
        review_type="edge", frames={1: [_frame(1), _frame(2), _frame(3)]},
        detector=None, calibrators={}, camera_roles={1: "UltraEdge"}, primary_camera_id=1)
    result = run_review("edge", ctx)
    assert result["review_type"] == "edge"
    assert result["edge_analysis"]["inconclusive"] is True
    assert result["hotspot_analysis"]["contact_detected"] is False  # frames carry no image
    assert result["review_result"]["verdict"] == "INCONCLUSIVE"
    # Edge clears LBW-only fields (it is not an LBW review).
    assert result["trajectory"] == []
    assert result["impact_point"] is None


def test_edge_alias_routes_to_module() -> None:
    ctx = ReviewContext(
        review_type="ultraedge", frames={1: [_frame(1)]}, detector=None,
        calibrators={}, camera_roles={1: "UltraEdge"}, primary_camera_id=1)
    assert run_review("ultraedge", ctx)["review_result"]["verdict"] == "INCONCLUSIVE"


# --- Run Out (crease-focused, swappable bat locator) -------------------------

class _StubBatLocator:
    def __init__(self, ground_px):
        self.ground_px = ground_px

    def locate(self, frames):
        return BatLocation(outline_px=[[600, 480], [680, 480], [680, 520], [600, 520]],
                           ground_px=self.ground_px, confidence=0.6, frame_id=2)


def _run_out_ctx(calibrated=True):
    cal = LinearCalibrator()
    return ReviewContext(
        review_type="runout", frames={1: [_frame(1), _frame(2), _frame(3)]}, detector=None,
        calibrators={1: cal} if calibrated else {}, camera_roles={1: "Stump"}, primary_camera_id=1)


def test_run_out_short_of_crease_is_out() -> None:
    # LinearCalibrator: along_mm = -(y-250)*4; crease ≈ -1220 mm → y≈600 is short (out).
    module = RunOutReviewModule(bat_locator=_StubBatLocator((640, 600)))
    result = module.analyze(_run_out_ctx())
    run_out = result["run_out_analysis"]
    assert run_out["is_out"] is True and run_out["distance_cm"] < 0
    assert result["summary"]["headline"] == "OUT"
    assert result["geometry"]["kind"] == "runout" and result["geometry"]["crease_world"]
    assert result["geometry"]["bat_px"]


def test_run_out_grounded_behind_crease_is_not_out() -> None:
    run_out = RunOutReviewModule(bat_locator=_StubBatLocator((640, 500))).analyze(_run_out_ctx())["run_out_analysis"]
    assert run_out["is_out"] is False and run_out["distance_cm"] > 0


def test_run_out_without_calibration_awaits() -> None:
    run_out = RunOutReviewModule(bat_locator=_StubBatLocator((640, 500))).analyze(_run_out_ctx(calibrated=False))["run_out_analysis"]
    assert run_out["is_out"] is None and run_out["requires_calibration"] is True


def test_run_out_overlay_payload_projects_crease_and_cards() -> None:
    from core.overlay_builder import build_overlay_payload

    module = RunOutReviewModule(bat_locator=_StubBatLocator((640, 600)))
    decision = module.analyze(_run_out_ctx())
    decision["review_result"] = build_review_result("runout", decision)
    payload = build_overlay_payload(decision, calibrators={1: LinearCalibrator()})
    assert payload["verdict"] == "OUT"
    assert len(payload["crease_px"]) == 2       # projected popping-crease line
    assert payload["bat_px"] and len(payload["stumps_px"]) == 3 and len(payload["bails_px"]) == 3
    assert [c["label"] for c in payload["decision_cards"]][0] == "DECISION"


# ---------------------------------------------------------------------------
# The bails are NOT observed by any detector. These tests exist because a
# placeholder "dislodged" once flowed all the way onto the evidence frame as a
# red "BAILS DISLODGED", telling the umpire something the system never measured.
# Nothing may assert a bails state until a real detector produces one.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bat_y,label", [(600, "short of crease"), (500, "behind crease")])
def test_bails_status_is_never_fabricated(bat_y: int, label: str) -> None:
    """Unknown is an EXPLICIT third value on the wire, not an absent one — a null
    invites consumers to collapse it into the negative case."""
    for module, block in (
        (RunOutReviewModule(bat_locator=_StubBatLocator((640, bat_y))), "run_out_analysis"),
        (StumpingReviewModule(bat_locator=_StubBatLocator((640, bat_y))), "stumping_analysis"),
    ):
        decision = module.analyze(_run_out_ctx())
        analysis = decision[block]
        assert analysis["bails_status"] == BailsState.NOT_OBSERVED.value, \
            f"{block} fabricated a bails state ({label})"
        assert BailsState.coerce(analysis["bails_status"]).wicket_broken is None
        rows = {m["label"]: m["value"] for m in decision["summary"]["measurements"]}
        assert rows["Bails"] == "Not observed"


def test_undetectable_checks_are_tri_state_not_null() -> None:
    """Every check without a detector reports UNKNOWN explicitly, so no consumer can
    read the gap as a negative finding."""
    decision = StumpingReviewModule(bat_locator=_StubBatLocator((640, 600))).analyze(_run_out_ctx())
    analysis = decision["stumping_analysis"]
    for field in ("gloves_detected", "ball_collected", "ball_possession"):
        assert analysis[field] == Observation.UNKNOWN.value, f"{field} is not tri-state"
        assert Observation.coerce(analysis[field]).as_optional_bool() is None
        assert Observation.coerce(analysis[field]).is_known is False


def test_observation_never_collapses_unknown_into_false() -> None:
    assert Observation.of(None) is Observation.UNKNOWN
    assert Observation.of(False) is Observation.FALSE
    assert Observation.of(True) is Observation.TRUE
    assert Observation.UNKNOWN.label("yes", "no") == "Not observed"
    # legacy/garbage values degrade to UNKNOWN, never to a claim
    assert Observation.coerce("nonsense") is Observation.UNKNOWN
    assert BailsState.coerce(None) is BailsState.NOT_OBSERVED
    assert BailsState.coerce("dislodged").wicket_broken is True
    assert BailsState.coerce("intact").wicket_broken is False


def test_unknown_bails_never_claims_a_broken_wicket() -> None:
    """`hitting` drives the stump-hit treatment. Unknown must stay None: False
    would assert the wicket was NOT broken, which is just as unmeasured."""
    from core.overlay_builder import build_overlay_payload

    decision = RunOutReviewModule(bat_locator=_StubBatLocator((640, 600))).analyze(_run_out_ctx())
    decision["review_result"] = build_review_result("runout", decision)
    payload = build_overlay_payload(decision, calibrators={1: LinearCalibrator()})
    assert payload["bails_status"] == BailsState.NOT_OBSERVED.value
    assert payload["hitting"] is None          # None, NOT False — see the docstring
    bails_card = next(c for c in payload["decision_cards"] if c["label"] == "BAILS")
    assert bails_card["value"] == "NOT OBSERVED"   # not a bare dash, not a claim


def test_bails_overlay_renders_a_third_unknown_state() -> None:
    """The renderer must not fall back to INTACT when the state is unknown."""
    import numpy as np
    from core.overlay_renderer import OverlayRenderer

    renderer = OverlayRenderer()
    bails = [{"x": 100.0, "y": 80.0}, {"x": 130.0, "y": 80.0}]
    seen = {}
    for status in ("dislodged", "intact", None):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        renderer._draw_bails(frame, bails, status, 1.0)
        seen[str(status)] = frame.copy()
    # all three states must be visually distinct from one another
    assert not np.array_equal(seen["None"], seen["intact"])
    assert not np.array_equal(seen["None"], seen["dislodged"])
    assert not np.array_equal(seen["intact"], seen["dislodged"])
