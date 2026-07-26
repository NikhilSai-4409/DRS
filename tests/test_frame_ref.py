"""Frame identity tests.

Three different integers named ``frame_id`` circulate in this system — per-camera
capture counters, replay-clip positions, and (historically) synthesised array
indices. Comparing across them draws an overlay on the wrong moment, which is
misleading evidence rather than a cosmetic bug. These tests lock the guard.
"""

from __future__ import annotations

import pytest

from core.frame_ref import FrameRef, FrameSpace, FrameSpaceMismatch


def test_capture_and_clip_indices_are_not_comparable() -> None:
    capture = FrameRef.capture(194, 6453.0, camera_id=0)
    clip = FrameRef.clip(194)
    with pytest.raises(FrameSpaceMismatch):
        capture.is_same_moment(clip)


def test_same_index_from_different_cameras_is_not_the_same_moment() -> None:
    """Camera 0 frame 194 and camera 1 frame 194 are different instants."""
    a = FrameRef.capture(194, 6453.0, camera_id=0)
    b = FrameRef.capture(194, 6453.0, camera_id=1)
    with pytest.raises(FrameSpaceMismatch):
        a.is_same_moment(b)
    assert a != b


def test_mismatch_raises_rather_than_returning_false() -> None:
    """A quiet False would read as "a different moment" when the honest answer is
    "unanswerable" — the same collapse that made unmeasured checks look negative."""
    a = FrameRef.capture(1, 0.0, camera_id=0)
    with pytest.raises(FrameSpaceMismatch):
        a.is_same_moment(FrameRef.clip(1))


def test_comparable_within_one_source() -> None:
    a = FrameRef.capture(194, 6453.0, camera_id=2)
    assert a.is_same_moment(FrameRef.capture(194, 6453.0, camera_id=2))
    assert not a.is_same_moment(FrameRef.capture(195, 6486.0, camera_id=2))


def test_missing_timestamp_is_tolerated_not_fatal() -> None:
    """Some capture paths carry no timestamp. Build the ref anyway — the contract
    validator reports it; raising here would cost the operator a review."""
    ref = FrameRef.capture(12, None, camera_id=1)
    assert ref.space is FrameSpace.CAPTURE and ref.timestamp_ms is None


def test_roundtrip_through_the_wire_format() -> None:
    ref = FrameRef.capture(194, 6453.0, camera_id=1)
    assert FrameRef.coerce(ref.to_dict()) == ref
    assert FrameRef.coerce({"index": 1}) is None          # no space named
    assert FrameRef.coerce({"space": "wallclock", "index": 1}) is None


def test_lbw_track_carries_frame_identity_per_point() -> None:
    """The prerequisite for overlays that follow the ball while stepping: every
    tracked point must name its own frame, not an array position."""
    from types import SimpleNamespace

    from core.review_modules.lbw import LbwReviewModule

    samples = [
        SimpleNamespace(cx=10.0, cy=20.0, lateral_mm=1.0, along_mm=2.0,
                        confidence=0.9, frame_id=100, timestamp_ms=1000.0),
        SimpleNamespace(cx=12.0, cy=24.0, lateral_mm=3.0, along_mm=4.0,
                        confidence=0.8, frame_id=101, timestamp_ms=1033.0),
    ]
    geometry = LbwReviewModule._geometry(camera_id=0, samples=samples, prediction=None)
    track = geometry["track"]
    assert [p["frame"]["index"] for p in track] == [100, 101]
    assert all(p["frame"]["space"] == "capture" for p in track)
    assert all(p["frame"]["source"] == "camera:0" for p in track)
    assert [p["frame"]["timestamp_ms"] for p in track] == [1000.0, 1033.0]
