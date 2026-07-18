"""Tests for the CameraDirector — camera moves per review type, no overlay/drawing."""

from __future__ import annotations

from core.camera_director import CameraDirector
from core.timelines import timeline_for

LBW = timeline_for("lbw")
RUNOUT = timeline_for("runout")
STUMPING = timeline_for("stumping")


def test_zoom_ramps_in_after_freeze():
    d = CameraDirector(zoom_max=0.08)
    assert d.state_at(LBW, 0.3)["zoom"] == 0.0        # still frozen
    assert d.state_at(LBW, 1.0)["zoom"] == 0.08       # fully zoomed
    assert 0.0 < d.state_at(LBW, 0.7)["zoom"] < 0.08


def test_freeze_active_only_at_the_start():
    d = CameraDirector()
    assert d.state_at(LBW, 0.2)["freeze"] is True
    assert d.state_at(LBW, 1.0)["freeze"] is False


def test_run_out_camera_frame_steps_then_zooms():
    d = CameraDirector()
    stepping = d.state_at(RUNOUT, 1.2)      # frame-step window (0.5–2.3), zoom (from 2.6) not yet
    assert stepping["frame_step"] is True and stepping["zoom"] == 0.0
    later = d.state_at(RUNOUT, 3.0)
    assert later["frame_step"] is False and later["zoom"] > 0.0


def test_stumping_camera_runs_slow_motion():
    d = CameraDirector()
    assert d.state_at(STUMPING, 3.0)["slowmo"] is True
    assert d.full_state(STUMPING)["duration"] == 5.2
