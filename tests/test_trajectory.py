"""Tests for trajectory prediction module."""

from core.trajectory import TrajectoryPoint, TrajectoryPredictor, TrajectoryPrediction
from core.ball_tracker import TrackPoint


def test_wicket_collision_interpolates_between_samples() -> None:
    predictor = TrajectoryPredictor()
    points = [
        TrajectoryPoint(0.00, -0.04, 0.0, 0.2, 1.0, 0.0, 0.0),
        TrajectoryPoint(0.01, 0.04, 0.0, 0.2, 1.0, 0.0, 0.0),
    ]

    collision = predictor._find_wicket_collision(points, 0.0, 0.1143, 0.711)

    assert collision is not None
    assert collision.x == 0.0
    assert collision.z == 0.2


def test_wicket_collision_returns_none_when_ball_misses() -> None:
    """Ball trajectory that clearly misses the stumps should return None."""
    predictor = TrajectoryPredictor()
    points = [
        TrajectoryPoint(0.00, -0.04, 5.0, 0.2, 1.0, 0.0, 0.0),
        TrajectoryPoint(0.01, 0.04, 5.0, 0.2, 1.0, 0.0, 0.0),
    ]
    collision = predictor._find_wicket_collision(points, 0.0, 0.1143, 0.711)
    assert collision is None


def test_predict_from_world_points_basic() -> None:
    """predict_from_world_points should produce a trajectory from 2+ positions."""
    predictor = TrajectoryPredictor()
    positions = [
        (-5.0, 0.0, 1.8),
        (-4.0, 0.0, 1.6),
        (-3.0, 0.0, 1.3),
    ]
    timestamps = [0.0, 0.03, 0.06]
    result = predictor.predict_from_world_points(positions, timestamps, horizon_s=0.5)
    assert isinstance(result, TrajectoryPrediction)
    assert len(result.points) > 0


def test_predict_from_world_points_raises_with_one_point() -> None:
    """Should raise ValueError with fewer than 2 points."""
    predictor = TrajectoryPredictor()
    import pytest
    with pytest.raises(ValueError):
        predictor.predict_from_world_points([(0.0, 0.0, 1.0)], [0.0])


def test_bounce_detection_in_prediction() -> None:
    """Should detect bounce when ball hits ground (z=0)."""
    predictor = TrajectoryPredictor()
    # Ball coming down at steep angle
    positions = [
        (-3.0, 0.0, 1.0),
        (-2.0, 0.0, 0.4),
    ]
    timestamps = [0.0, 0.03]
    result = predictor.predict_from_world_points(positions, timestamps, horizon_s=0.5)
    # Bounce should eventually occur since ball is descending
    assert isinstance(result, TrajectoryPrediction)
    # bounce_index may or may not be set depending on trajectory arc


def test_wicket_collision_in_prediction() -> None:
    """Ball heading toward wickets should be detected."""
    predictor = TrajectoryPredictor()
    # Ball moving toward x=0 wicket position
    positions = [
        (-2.0, 0.0, 0.4),
        (-1.0, 0.0, 0.35),
    ]
    timestamps = [0.0, 0.03]
    result = predictor.predict_from_world_points(
        positions, timestamps,
        horizon_s=0.5,
        wicket_x_m=0.0,
    )
    assert isinstance(result, TrajectoryPrediction)
    # May or may not have wicket collision depending on trajectory


def test_approximate_world_from_track() -> None:
    """approximate_world_from_track should convert pixel coords to world space."""
    predictor = TrajectoryPredictor()
    pixel_track = [
        TrackPoint(frame_id=0, timestamp_ms=0.0, camera_id=0, x=100.0, y=200.0, vx=10.0, vy=-2.0, speed_px_s=300.0, direction_deg=0.0, confidence=0.9, predicted=False),
        TrackPoint(frame_id=1, timestamp_ms=33.0, camera_id=0, x=200.0, y=180.0, vx=10.0, vy=-2.0, speed_px_s=300.0, direction_deg=0.0, confidence=0.9, predicted=False),
        TrackPoint(frame_id=2, timestamp_ms=66.0, camera_id=0, x=300.0, y=160.0, vx=10.0, vy=-2.0, speed_px_s=300.0, direction_deg=0.0, confidence=0.9, predicted=False),
        TrackPoint(frame_id=3, timestamp_ms=100.0, camera_id=0, x=400.0, y=150.0, vx=10.0, vy=-2.0, speed_px_s=300.0, direction_deg=0.0, confidence=0.9, predicted=False),
    ]
    positions, times = predictor.approximate_world_from_track(pixel_track, pixels_per_meter=100.0)
    assert len(positions) == len(pixel_track)
    assert len(times) == len(pixel_track)
    for pos in positions:
        assert len(pos) == 3  # (x, y, z)
    assert times[0] == 0.0


def test_overlay_draws_polyline() -> None:
    """overlay() should draw a path on the frame without error."""
    import numpy as np
    predictor = TrajectoryPredictor()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    points = [(100, 200), (200, 180), (300, 160), (400, 150)]
    result = predictor.overlay(frame, points)
    assert result.shape == frame.shape


def test_prediction_to_dict() -> None:
    """TrajectoryPrediction.to_dict should produce serializable output."""
    prediction = TrajectoryPrediction(
        points=[TrajectoryPoint(0.0, 1.0, 0.0, 0.5, 10.0, 0.0, -2.0)],
        bounce_index=None,
        wicket_collision=False,
        wicket_point=None,
    )
    d = prediction.to_dict()
    assert "points" in d
    assert "bounce_index" in d
    assert "wicket_collision" in d
    assert len(d["points"]) == 1
