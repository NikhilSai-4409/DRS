"""Contract check: the calibration marker → homography → world mapping is self-consistent.

Verifies the workspace/producer agree on marker keys/order, that clicked pixels reproject
to the intended world points, and that pixel→world→pixel round-trips cleanly. This removes
the 'is the profile wrong or is the producer wrong' ambiguity before any real profile.
"""

import cv2
import numpy as np

from core.pitch_calibration import (
    ICCPitchDimensions,
    ManualPitchCalibrator,
    _world_points_for_markers,
)

# Marker pixels for a plausible near-stumps view (natural image pixels, as the workspace stores).
MARKERS = {
    "off_stump": {"x": 900.0, "y": 300.0},
    "middle_stump": {"x": 960.0, "y": 302.0},
    "leg_stump": {"x": 1020.0, "y": 300.0},
    "bowling_crease": {"x": 958.0, "y": 305.0},
    "popping_crease": {"x": 955.0, "y": 520.0},
}


def test_markers_reproject_to_their_intended_world_points():
    calib = ManualPitchCalibrator()
    homography, err_cm = calib.compute_homography(MARKERS)
    assert err_cm < 5.0  # clicked points reproject close to their world references

    world_map = _world_points_for_markers(ICCPitchDimensions())
    for key, (wx, wy) in world_map.items():
        gx, gy = calib.pixel_to_world(MARKERS[key]["x"], MARKERS[key]["y"], homography)
        # each key's pixel must map to THAT key's world point — proves ordering/keys agree
        assert abs(gx - wx) < 0.05, f"{key} lateral off by {gx - wx:.3f} m"
        assert abs(gy - wy) < 0.05, f"{key} along off by {gy - wy:.3f} m"


def test_pixel_world_pixel_roundtrip_is_tight():
    calib = ManualPitchCalibrator()
    homography, _ = calib.compute_homography(MARKERS)
    inv = np.linalg.inv(np.asarray(homography, dtype=np.float64)).astype(np.float32)
    for px, py in [(1000.0, 350.0), (930.0, 480.0), (980.0, 260.0)]:
        wx, wy = calib.pixel_to_world(px, py, homography)
        back = cv2.perspectiveTransform(np.array([[[wx, wy]]], dtype=np.float32), inv)
        bx, by = float(back[0, 0, 0]), float(back[0, 0, 1])
        assert abs(bx - px) < 1.0 and abs(by - py) < 1.0, f"roundtrip drift ({bx - px:.2f},{by - py:.2f})px"


def test_coordinate_convention_lateral_and_along():
    """+lateral goes off→leg stump; along is 0 at the stumps and negative toward the bowler
    (popping crease). This is exactly what CalibratedTrajectoryProducer assumes."""
    world_map = _world_points_for_markers(ICCPitchDimensions())
    assert world_map["off_stump"][0] < world_map["leg_stump"][0]      # lateral increases off→leg
    assert world_map["popping_crease"][1] < world_map["middle_stump"][1]  # popping crease is toward the bowler


# --- canonical calibration verification (core/calibration.py) ----------------

def test_summarize_ground_trajectory_speed_and_bounce():
    from core.calibration import summarize_ground_trajectory
    # fake projector: pixel → world ground metres; the ball moves UP the frame toward stumps.
    def project(x, y):
        return ((x - 960) / 100.0, (800.0 - y) / 40.0, 0.0)   # X lateral, Y along-pitch
    pts = [(960, 800 - i * 30) for i in range(20)]
    times = [i * 0.02 for i in range(20)]
    s = summarize_ground_trajectory(project, pts, times, bounce_px=(960, 500))
    assert s["points_projected"] == 20
    assert 100 <= s["ground_speed_kmh"] <= 200
    assert s["bounce"] is not None
    assert s["bounce"]["from_stumps_m"] == round(20.12 - (800 - 500) / 40.0, 2)   # 12.62 m


def test_summarize_skips_unprojectable_points():
    from core.calibration import summarize_ground_trajectory
    def project(x, y):
        if y < 0:
            raise ValueError("behind camera")
        return ((x - 960) / 100.0, (800.0 - y) / 40.0, 0.0)
    pts = [(960, 700), (960, -50), (960, 600)]   # middle one fails to project
    s = summarize_ground_trajectory(project, pts, [0.0, 0.02, 0.04])
    assert s["points_projected"] == 2 and s["points_total"] == 3


def test_ground_solve_falls_back_to_estimated_intrinsics(capsys):
    from core.calibration import PitchCalibrator
    pc = PitchCalibrator()
    camera_matrix, dist, source, warnings = pc._load_intrinsics(999999, (1920, 1080))  # no such profile
    assert source == "estimated"
    assert camera_matrix.shape == (3, 3)
    assert float(camera_matrix[0, 0]) == 1920.0                                # focal = max(w, h)
    assert warnings and "estimated" in warnings[0].lower()                     # persisted into the profile
    assert "estimated" in capsys.readouterr().out.lower()                     # loud on the console, not silent


def test_ground_solve_uses_charuco_intrinsics_when_present():
    import json
    from core.calibration import PitchCalibrator, intrinsics_path
    cam = 987654
    path = intrinsics_path(cam)   # the dedicated intrinsics file, separate from pose
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "type": "intrinsics",
        "camera_matrix": [[1234.0, 0, 960], [0, 1234.0, 540], [0, 0, 1]],
        "distortion_coeffs": [[0.1, -0.05, 0.0, 0.0, 0.0]],
    }), encoding="utf-8")
    try:
        camera_matrix, dist, source, warnings = PitchCalibrator()._load_intrinsics(cam, (1920, 1080))
        assert source == "charuco"
        assert warnings == []                              # no warning when real intrinsics are used
        assert float(camera_matrix[0, 0]) == 1234.0        # real focal, not max(w,h)
        assert dist.shape[0] == 5
    finally:
        path.unlink(missing_ok=True)


def test_pixel_to_world_roundtrips_ground_points():
    from core.calibration import PitchCalibrator, PITCH_WORLD_POINTS
    K = np.array([[1000.0, 0, 960], [0, 1000.0, 540], [0, 0, 1]])
    rvec = np.zeros((3, 1))
    tvec = np.array([[0.0], [-10.0], [30.0]])
    dist = np.zeros(5)
    img = cv2.projectPoints(PITCH_WORLD_POINTS, rvec, tvec, K, dist)[0].reshape(-1, 2)
    pc = PitchCalibrator()
    pc.profile = {"camera_matrix": K.tolist(), "rvec": rvec.flatten().tolist(),
                  "tvec": tvec.flatten().tolist(), "dist_coeffs": dist.tolist()}
    for i in range(6):   # the 6 ground-plane crease points (Z=0) must round-trip
        wx, wy, _ = pc.pixel_to_world(float(img[i][0]), float(img[i][1]), 0.0)
        assert abs(wx - float(PITCH_WORLD_POINTS[i][0])) < 0.05
        assert abs(wy - float(PITCH_WORLD_POINTS[i][1])) < 0.05
