"""Tests for the canonical ReviewResult trajectory artifact (core/review_result.py).

Covers the validity/confidence split, the swappable producer abstraction, and JSON
serialization — the contract every downstream surface (animation, diagnostics, replay,
export) now depends on.
"""

import json
import math

from core.review_result import (
    MIN_REAL_DETECTIONS,
    CalibratedTrajectoryProducer,
    ObservedTrajectory,
    PredictedTrajectory,
    build_review_result,
)


def _track(i, x, y, real=True, conf=0.8):
    return {
        "frame_id": i,
        "timestamp_ms": i * 20.0,
        "x": x,
        "y": y,
        "confidence": conf,
        "real_detection": real,
        "predicted": not real,
    }


def _moving(n=20):
    # ~30 px/frame at 20 ms → a plausible delivery speed once scaled by ppm.
    return [_track(i, 200 + i * 30.0, 600 - i * 12.0) for i in range(n)]


def test_observed_counts_distinguish_real_from_gap_fill():
    tracks = [_track(0, 10, 10), _track(1, 20, 20, real=False), _track(2, 30, 30)]
    obs = ObservedTrajectory.from_tracks(tracks, camera_id=0, fps=50.0)
    assert obs.tracked_count == 3
    assert obs.real_count == 2  # the Kalman gap-fill is not a real detection


def test_valid_delivery_renders_real():
    obs = ObservedTrajectory.from_tracks(_moving(20), 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    t = rr.trajectory
    assert t.valid is True
    assert t.reasons == []
    assert t.source == "physics"
    assert t.confidence > 0.0
    assert len(t.fitted_points) == 20
    anim = next(s for s in rr.diagnostics["stages"] if s["key"] == "animation")
    assert anim["ok"] is True  # → renderer shows "✓ Real trajectory"


def test_too_few_real_detections_is_invalid():
    obs = ObservedTrajectory.from_tracks(_moving(MIN_REAL_DETECTIONS - 1), 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is False
    assert any("real detection" in r for r in rr.trajectory.reasons)


def test_pixel_speed_does_not_gate_under_heuristic():
    # A ball moving down the pitch shows tiny pixel motion on an uncalibrated camera.
    # Pixel speed must NOT reject it, and speed is reported as unavailable (None).
    tracks = [_track(i, 200 + i * 0.3, 600) for i in range(20)]
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is True
    assert not any("speed" in r for r in rr.trajectory.reasons)
    assert rr.trajectory.release_speed_kmh is None
    assert "unavailable" in rr.diagnostics["measurements"]["speed"]


def test_implausible_speed_rejected_only_when_calibrated():
    # With a homography (calibration), a physically impossible speed IS a real failure.
    tracks = [_track(i, 200 + i * 0.3, 600) for i in range(20)]
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "calibration", pixels_per_meter=63.6)
    assert rr.trajectory.valid is False
    assert any("speed" in r for r in rr.trajectory.reasons)


# --- observation-quality checks apply on ANY geometry -----------------------

def test_mostly_gap_fill_is_invalid_even_uncalibrated():
    # 6 real + 16 Kalman gap-fills = 27% real; a hallucinated track, rejected on any camera.
    tracks = [_track(i, 200 + i * 20, 600 - i * 10, real=(i % 4 == 0)) for i in range(22)]
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is False
    assert any("gap-fill" in r for r in rr.trajectory.reasons)


def test_low_detector_confidence_is_invalid_even_uncalibrated():
    tracks = [_track(i, 200 + i * 20, 600 - i * 10, conf=0.1) for i in range(20)]
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is False
    assert any("confidence" in r for r in rr.trajectory.reasons)


def test_erratic_direction_reversals_are_invalid_even_uncalibrated():
    # x zig-zags ±80 px every frame — a tracker hopping between objects, not a flight.
    tracks = [_track(i, 300 + (80 if i % 2 else -80), 600 - i * 10) for i in range(20)]
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is False
    assert any("erratic" in r or "reversal" in r for r in rr.trajectory.reasons)


def test_large_temporal_gap_is_invalid_even_uncalibrated():
    # A clean early burst, then a 1 s dropout — a discontinuous track.
    early = [_track(i, 200 + i * 20, 600 - i * 10) for i in range(10)]
    late = [_track(50 + i, 500 + i * 20, 400 - i * 10) for i in range(10)]  # t jumps ~800 ms
    obs = ObservedTrajectory.from_tracks(early + late, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is False
    assert any("gap between detections" in r for r in rr.trajectory.reasons)


def test_nan_coordinate_is_invalid():
    tracks = _moving(20)
    tracks[5]["x"] = math.nan
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is False
    assert any("NaN" in r or "infinite" in r for r in rr.trajectory.reasons)


def test_validity_is_independent_of_confidence():
    # A valid trajectory on heuristic geometry is still shown, just at lower confidence.
    obs = ObservedTrajectory.from_tracks(_moving(20), 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6)
    assert rr.trajectory.valid is True
    assert rr.trajectory.confidence < 1.0  # heuristic geometry caps confidence


def test_producer_is_swappable():
    """PredictedTrajectory never knows its source — a different producer drops in
    without any change to build_review_result or downstream consumers."""

    class FakeProducer:
        def predict(self, observed):
            return PredictedTrajectory(
                observed=observed,
                fitted_points=[],
                predicted_path=[{"x": 0.0, "y": 0.0, "z": 0.0}],
                bounce=None,
                impact=None,
                wicket=None,
                release_speed_kmh=100.0,
                model_used="fake",
                geometry_source="heuristic",
                source="fake-producer",
                valid=True,
                reasons=[],
                confidence=0.91,
            )

    obs = ObservedTrajectory.from_tracks(_moving(20), 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", producer=FakeProducer())
    assert rr.trajectory.source == "fake-producer"
    assert rr.confidence == 0.91


# --- trajectory trim + observation summary ----------------------------------

def test_display_policy_cuts_at_impact_without_destroying_data():
    obs = ObservedTrajectory.from_tracks(_moving(40), 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6,
                             impact_frame=20, last_frame=39)
    s = rr.diagnostics["observation"]
    assert s["end_reason"] == "Impact confirmed"
    assert s["end_frame"] <= 28  # impact 20 + 8 confirmation frames
    assert s["dropped_points"] > 0
    # the full track is PRESERVED — trimming is display-only
    assert s["tracked_points"] == 40
    assert len(rr.trajectory.observed.points) == 40
    assert rr.trajectory.observed.display_end_frame == 28
    assert len(rr.trajectory.observed.display_points()) < 40


def test_no_impact_flags_reached_clip_end():
    obs = ObservedTrajectory.from_tracks(_moving(30), 0, 50.0)  # all real, no impact
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6, last_frame=29)
    s = rr.diagnostics["observation"]
    assert s["end_reason"] == "Reached clip end (no impact)"
    assert s["dropped_points"] == 0


def test_trailing_gap_fill_flagged_detections_lost():
    tracks = _moving(30) + [_track(30 + i, 1100 + i, 200, real=False) for i in range(6)]
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6, last_frame=35)
    s = rr.diagnostics["observation"]
    assert s["end_reason"] == "Detections lost"
    assert s["dropped_points"] == 6
    assert s["tracked_points"] == 36  # full track preserved


def test_midtrack_gap_terminates_left_delivery():
    # A clean delivery (frames 0-14), then the ball is lost and something else is
    # re-acquired ~1.3 s later — an unrecoverable gap that cannot be the same delivery.
    # Without termination this whole track (with its huge gap) would fail the validity
    # gate and fall back to the template; terminating trims it to the real delivery.
    tracks = _moving(15) + [_track(80 + i, 2000, 200) for i in range(6)]
    obs = ObservedTrajectory.from_tracks(tracks, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6, last_frame=85)
    s = rr.diagnostics["observation"]
    assert s["end_reason"] == "Left delivery (tracking gap)"
    assert s["end_frame"] == 14                              # last real detection before the gap
    assert s["tracked_points"] == 21                         # full track preserved (non-destructive)
    assert len(rr.trajectory.observed.display_points()) == 15  # only the delivery is displayed/fitted
    assert rr.trajectory.valid is True                       # the trimmed delivery is clean → valid


def test_reconnecting_gap_not_terminated():
    # A gap the ball emerges from ON its predicted path (e.g. a brief occlusion behind the
    # batter, common at high fps) is NOT a break — termination must not cut a delivery that
    # genuinely reconnects. It falls through to a normal clip-end instead of "Left delivery".
    pre = [_track(i, 200 + 20.0 * i, 300) for i in range(8)]          # vx = 20 px/frame
    post = [_track(40 + i, 200 + 20.0 * (40 + i), 300) for i in range(6)]  # reappears on-trajectory
    obs = ObservedTrajectory.from_tracks(pre + post, 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6, last_frame=45)
    assert rr.diagnostics["observation"]["end_reason"] != "Left delivery (tracking gap)"


def test_observation_summary_has_debugger_fields_and_quality():
    obs = ObservedTrajectory.from_tracks(_moving(20), 0, 50.0)
    rr = build_review_result("job", obs, {}, "heuristic", pixels_per_meter=63.6, last_frame=19)
    s = rr.diagnostics["observation"]
    for k in ("start_frame", "end_frame", "length_frames", "tracked_points", "displayed_points",
              "dropped_points", "real_detections", "mean_confidence", "longest_gap_frames",
              "end_reason", "quality", "quality_stars"):
        assert k in s
    assert s["mean_confidence"] > 0
    assert 0.0 <= s["quality"] <= 1.0
    assert 0 <= s["quality_stars"] <= 5


# --- calibrated producer (image → pitch ground coordinates) -----------------

class _FakeCalibrator:
    """Known linear image→pitch mapping standing in for a real homography.
    lateral_mm=(px-960)*2 ; along_mm=-(py-200)*30 (0 at the stump line, negative toward bowler)."""
    def pixel_to_pitch_mm(self, camera_id, px, py):
        return ((px - 960) * 2.0, -(py - 200) * 30.0)


def _delivery_pixels(n=20):
    # ball travels up the frame (py 800→200) toward the stumps, near the centre line
    return [_track(i, 960, 800 - i * (600 / (n - 1))) for i in range(n)]


def test_calibrated_producer_maps_to_pitch_metres():
    obs = ObservedTrajectory.from_tracks(_delivery_pixels(20), 0, 50.0)
    rr = build_review_result("job", obs, {}, "calibration",
                             producer=CalibratedTrajectoryProducer(_FakeCalibrator(), 0, homography_error_cm=2.0))
    t = rr.trajectory
    assert t.geometry_source == "calibration"
    assert t.source == "calibrated"
    assert len(t.fitted_points) == 20
    assert all("z" in p and abs(p["x"]) < 5 for p in t.fitted_points)  # metres, not pixels
    assert t.valid is True
    assert 100 <= t.release_speed_kmh <= 220           # a real ground-plane km/h
    assert t.wicket is not None and t.wicket["height_known"] is False   # line only, no height
    assert rr.diagnostics["measurements"]["speed"].endswith("km/h")      # speed now available


def test_calibrated_producer_gates_static_track_on_speed():
    # ball barely moves → ground speed ~0 → implausible (calibrated geometry DOES gate speed)
    obs = ObservedTrajectory.from_tracks([_track(i, 960, 400) for i in range(20)], 0, 50.0)
    rr = build_review_result("job", obs, {}, "calibration",
                             producer=CalibratedTrajectoryProducer(_FakeCalibrator(), 0))
    assert rr.trajectory.valid is False
    assert any("speed" in r for r in rr.trajectory.reasons)


def test_calibrated_producer_invalid_without_homography():
    class _NoHomography:
        def pixel_to_pitch_mm(self, camera_id, px, py):
            return None
    obs = ObservedTrajectory.from_tracks(_delivery_pixels(20), 0, 50.0)
    rr = build_review_result("job", obs, {}, "calibration",
                             producer=CalibratedTrajectoryProducer(_NoHomography(), 0))
    assert rr.trajectory.valid is False
    assert any("mapped to the pitch" in r for r in rr.trajectory.reasons)


def test_review_result_json_serializes():
    obs = ObservedTrajectory.from_tracks(_moving(20), 0, 50.0)
    rr = build_review_result("job", obs, {"verdict": "OUT"}, "heuristic", pixels_per_meter=63.6)
    blob = json.dumps(rr.to_dict())  # must survive results.json + API payload
    reloaded = json.loads(blob)
    assert reloaded["trajectory"]["observed"]["tracked_count"] == 20
    assert "stages" in reloaded["diagnostics"]
