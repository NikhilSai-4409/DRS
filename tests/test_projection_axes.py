"""Axis agreement between the pitch frame, the pose world frame, and the image.

The pose calibration and the ground homography use DIFFERENT world frames:

    pitch frame  lateral 0 at the middle stump; along 0 at the STRIKER'S stumps,
                 negative toward the bowler (striker's popping crease = -1220 mm)
    pose frame   X 0 on the centre line; Y 0 at the BOWLING crease, +Y toward the
                 striker, striker's stumps at STRIKER_STUMPS_ALONG_M

Converting between them is not a no-op, and getting it wrong moves every
measurement while the overlay still looks plausible. These tests pin the mapping
to the published PITCH_WORLD_POINTS rather than to an implementation.
"""

from __future__ import annotations

import numpy as np
import pytest

from config.settings import CREASE_TO_STUMPS_M, STUMP_WIDTH_M
from core.calibration import PITCH_WORLD_POINTS, STRIKER_STUMPS_ALONG_M
from core.projection import PoseProjection

RETURN_CREASE_HALF_M = 1.32


class _PoseCam:
    """A known camera looking down the pitch. Any invertible mapping works — the
    point is that BOTH sides go through the same one, so only the frame
    conversion is under test."""

    def world_to_pixel(self, x_m, y_m, z_m):
        return (640.0 + 300.0 * x_m - 4.0 * y_m, 700.0 - 30.0 * y_m - 120.0 * z_m)


@pytest.fixture()
def proj():
    return PoseProjection(_PoseCam())


def _expected(x_m, y_m, z_m):
    return _PoseCam().world_to_pixel(x_m, y_m, z_m)


def test_pitch_origin_is_the_strikers_stumps_not_the_bowlers_end(proj) -> None:
    """along = 0 must land on the striker's stumps (pose Y = 20.12), not Y = 0.
    The earlier mapping put it 20 m down the wrong end of the pitch."""
    got = proj.world_to_pixel(0.0, 0.0, 0.0)
    assert got == pytest.approx(_expected(0.0, STRIKER_STUMPS_ALONG_M, 0.0))
    wrong_end = _expected(0.0, 0.0, 0.0)
    assert abs(got[1] - wrong_end[1]) > 100, "must not collapse onto the bowler's end"


def test_lateral_zero_is_the_centre_line(proj) -> None:
    """No stump-width offset: the pose frame is already centred on the middle
    stump, so adding STUMP_WIDTH_M/2 shifted the world 11.4 cm sideways."""
    got = proj.world_to_pixel(0.0, 0.0, 0.0)
    assert got[0] == pytest.approx(_expected(0.0, STRIKER_STUMPS_ALONG_M, 0.0)[0])
    offset_by_half_stump = _expected(STUMP_WIDTH_M / 2.0, STRIKER_STUMPS_ALONG_M, 0.0)
    assert got[0] != pytest.approx(offset_by_half_stump[0])


def test_strikers_popping_crease_matches_the_published_world_frame(proj) -> None:
    """1.22 m in front of the striker's stumps → pose Y = 20.12 - 1.22."""
    got = proj.world_to_pixel(0.0, -CREASE_TO_STUMPS_M * 1000.0, 0.0)
    assert got == pytest.approx(
        _expected(0.0, STRIKER_STUMPS_ALONG_M - CREASE_TO_STUMPS_M, 0.0))


def test_return_crease_corners_land_symmetrically(proj) -> None:
    left = proj.world_to_pixel(-RETURN_CREASE_HALF_M * 1000.0, 0.0, 0.0)
    right = proj.world_to_pixel(RETURN_CREASE_HALF_M * 1000.0, 0.0, 0.0)
    centre = proj.world_to_pixel(0.0, 0.0, 0.0)
    assert centre[0] - left[0] == pytest.approx(right[0] - centre[0])


def test_stump_height_lifts_toward_the_top_of_the_frame(proj) -> None:
    base = proj.world_to_pixel(0.0, 0.0, 0.0)
    bail = proj.world_to_pixel(0.0, 0.0, 711.0)
    assert bail[1] < base[1], "a taller point must project higher in the image"


def test_striker_stump_tops_agree_with_PITCH_WORLD_POINTS(proj) -> None:
    """The published target's last three points are the striker's stump tops. The
    pitch frame must reach exactly those world positions."""
    tops = np.asarray(PITCH_WORLD_POINTS)[6:9]
    for x_m, y_m, z_m in tops:
        lateral_mm = float(x_m) * 1000.0
        along_mm = (float(y_m) - STRIKER_STUMPS_ALONG_M) * 1000.0
        got = proj.world_to_pixel(lateral_mm, along_mm, float(z_m) * 1000.0)
        assert got == pytest.approx(_expected(float(x_m), float(y_m), float(z_m)))


def test_pose_and_homography_agree_on_the_same_pitch_points() -> None:
    """Both backends receive pitch-frame values and must resolve to the same image
    point when they describe the same camera — the check that catches a frame
    mismatch between the two projection paths."""
    class _Homography:
        """Ground homography for the same camera, expressed in pitch mm."""

        def pitch_mm_to_pixel(self, camera_id, lateral_mm, along_mm):
            return _PoseCam().world_to_pixel(
                lateral_mm / 1000.0,
                along_mm / 1000.0 + STRIKER_STUMPS_ALONG_M,
                0.0,
            )

    from core.projection import HomographyProjection

    pose = PoseProjection(_PoseCam())
    homography = HomographyProjection(_Homography(), camera_id=0)
    for lateral_mm, along_mm in [(0.0, 0.0), (889.0, -1220.0), (-1320.0, -1220.0)]:
        a = pose.world_to_pixel(lateral_mm, along_mm, 0.0)
        b = homography.world_to_pixel(lateral_mm, along_mm, 0.0)
        assert a == pytest.approx(b, abs=1e-6), f"frames disagree at {lateral_mm},{along_mm}"
