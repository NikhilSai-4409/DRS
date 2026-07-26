"""Degeneracy detection for pitch calibration.

A calibration routine must not claim success when the input geometry cannot
determine a projection. The five-marker set below fits its own markers exactly
(RMS ~0) while putting the projected wide line hundreds of pixels off, because
three of its four distinct world points lie on the stump line.
"""

from __future__ import annotations

import numpy as np

from core.pitch_calibration import (
    MARKER_KEYS,
    ICCPitchDimensions,
    ManualPitchCalibrator,
    _world_points_for_markers,
    assess_marker_geometry,
)

DIMS = ICCPitchDimensions()
HALF = DIMS.stump_width_m / 2.0


def _image_from_world(world):
    """A known camera, so the clicks are internally consistent."""
    H = np.array([[420.0, 40.0, 640.0], [0.0, -260.0, 300.0], [0.0, -0.10, 1.0]])
    out = []
    for wx, wy in world:
        p = H @ np.array([wx, wy, 1.0])
        out.append([p[0] / p[2], p[1] / p[2]])
    return np.array(out, dtype=np.float64)


def test_the_shipped_marker_set_is_reported_as_degenerate() -> None:
    world_map = _world_points_for_markers(DIMS)
    world = np.array([world_map[k] for k in MARKER_KEYS], dtype=np.float64)
    verdict = assess_marker_geometry(_image_from_world(world), world)
    assert verdict["ok"] is False
    assert verdict["rms_is_meaningful"] is False
    # both faults are named, so an operator knows what to change
    joined = " ".join(verdict["reasons"])
    assert "collinear" in joined
    assert "no information" in joined            # bowling_crease duplicates middle_stump
    assert verdict["distinct_world_points"] == 4  # five clicks, four distinct positions


def test_a_well_spread_quad_passes() -> None:
    world = np.array([[-1.32, 0.0], [1.32, 0.0], [-1.32, -1.22], [1.32, -1.22]])
    verdict = assess_marker_geometry(_image_from_world(world), world)
    assert verdict["ok"] is True and verdict["rms_is_meaningful"] is True


def test_points_on_a_single_line_are_rejected() -> None:
    world = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
    verdict = assess_marker_geometry(_image_from_world(world), world)
    assert verdict["ok"] is False
    assert any("collinear" in r for r in verdict["reasons"])


def test_duplicate_world_positions_are_called_out() -> None:
    world = np.array([[-1.0, 0.0], [1.0, 0.0], [-1.0, -1.0], [-1.0, 0.0]])
    verdict = assess_marker_geometry(_image_from_world(world), world)
    assert verdict["distinct_world_points"] == 3
    assert any("no information" in r for r in verdict["reasons"])


def test_assess_markers_runs_off_real_clicks() -> None:
    world_map = _world_points_for_markers(DIMS)
    world = np.array([world_map[k] for k in MARKER_KEYS], dtype=np.float64)
    pixels = _image_from_world(world)
    markers = {key: {"x": float(pixels[i][0]), "y": float(pixels[i][1])}
               for i, key in enumerate(MARKER_KEYS)}
    verdict = ManualPitchCalibrator(DIMS).assess_markers(markers)
    assert verdict["ok"] is False


def test_low_rms_does_not_imply_a_usable_calibration() -> None:
    """The exact failure that hid this: a perfect fit on an unusable configuration."""
    world_map = _world_points_for_markers(DIMS)
    world = np.array([world_map[k] for k in MARKER_KEYS], dtype=np.float64)
    pixels = _image_from_world(world)
    markers = {key: {"x": float(pixels[i][0]), "y": float(pixels[i][1])}
               for i, key in enumerate(MARKER_KEYS)}
    cal = ManualPitchCalibrator(DIMS)
    _, rms_cm = cal.compute_homography(markers)
    assert rms_cm < 0.001                        # the solver is delighted
    assert cal.assess_markers(markers)["ok"] is False   # the geometry is not
