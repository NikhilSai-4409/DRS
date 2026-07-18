"""Unit tests for core/replay_reconstruction.py — the decision-service side of the
broadcast replay package. Pure logic, no video/chrome; covers the three delivery modes
and the honesty rules (no invented bounce, no overclaimed pitching)."""
from __future__ import annotations

from core.replay_reconstruction import CameraAnchor, build_replay_reconstruction


def _trajectory(points, valid=True, end_frame=None):
    return {
        "valid": valid,
        "observed": {
            "display_end_frame": end_frame if end_frame is not None else points[-1]["frame_id"],
            "points": points,
        },
    }


def _pt(f, x, y):
    return {"frame_id": f, "x_px": float(x), "y_px": float(y)}


def _yorker_track():
    # descends to ground at idx 47 (y 470), then 2 samples of sharp pad-deflection rise
    pts = [_pt(i, 800 + i * 3, 150 + i * 6.8) for i in range(48)]
    pts += [_pt(48, 946, 420), _pt(49, 948, 390)]
    return pts


def _normal_track():
    # bounce mid-track (idx 30) with a real post-bounce flight (20 samples rising)
    pts = [_pt(i, 820 + i * 2, 170 + i * 10) for i in range(31)]       # down to y=470
    pts += [_pt(31 + j, 882 + 2 * j, 470 - j * 5) for j in range(20)]  # rises to y=375
    return pts


def _full_toss_track():
    # y only ever increases: no ground touch inside the track
    return [_pt(i, 830 + i * 2.5, 160 + i * 6) for i in range(50)]


def test_invalid_trajectory_returns_none():
    assert build_replay_reconstruction(_trajectory(_yorker_track(), valid=False), {}) is None


def test_too_short_track_returns_none():
    assert build_replay_reconstruction(_trajectory(_yorker_track()[:6]), {}) is None


def test_yorker_mode_detects_bounce_and_gates():
    rec = build_replay_reconstruction(_trajectory(_yorker_track()), {})
    assert rec is not None
    assert rec["meta"]["mode"] == "yorker_pad"
    assert rec["bounce_px"] is not None and rec["bounce_frac"] is not None
    g = rec["gates"]
    assert g["pitching"] in ("IN LINE", "OUTSIDE OFF", "OUTSIDE LEG")
    assert g["wickets"] in ("HITTING", "MISSING")
    assert rec["cards"]["decision"] in ("OUT", "NOT OUT")
    # geometry contract: observed points precede prediction; bounce inside observed
    assert 0 < rec["bounce_index"] < rec["observed_n"] <= len(rec["points"])


def test_normal_mode_keeps_post_bounce_flight():
    rec = build_replay_reconstruction(_trajectory(_normal_track()), {})
    assert rec is not None
    assert rec["meta"]["mode"] == "normal"
    # the observed segment includes the post-bounce flight (not truncated at the bounce)
    assert rec["observed_n"] > rec["bounce_index"] + 3


def test_full_toss_never_fakes_a_bounce():
    rec = build_replay_reconstruction(_trajectory(_full_toss_track()), {})
    assert rec is not None
    assert rec["meta"]["mode"] == "full_toss"
    assert rec["bounce_px"] is None and rec["bounce_frac"] is None
    # no post-ground evidence => pitching must not overclaim
    assert rec["cards"]["pitching"] in ("UNKNOWN", "FULL TOSS")


def test_stump_anchor_drives_lateral_gates():
    # same track, anchor shifted far off -> everything reads far outside off
    rec = build_replay_reconstruction(
        _trajectory(_yorker_track()), {}, anchor=CameraAnchor(stump_x_px=500.0)
    )
    assert rec["gates"]["impact"] == "OUTSIDE OFF"
    assert rec["gates"]["wickets"] == "MISSING"
