"""Tests for the AnimationDirector — timing/sequencing only, no drawing."""

from __future__ import annotations

from core.animation_director import AnimationDirector
from core.timelines import timeline_for

PAYLOAD = {
    "hitting": True,
    "decision_cards": [{"label": "ORIGINAL"}, {"label": "PITCHING"}, {"label": "IMPACT"}, {"label": "WICKETS"}],
}
LBW = timeline_for("lbw")


def test_nothing_visible_at_start_everything_at_end():
    d = AnimationDirector()
    start = d.state_at(LBW, PAYLOAD, 0.0)
    end = d.full_state(LBW, PAYLOAD)
    assert start["measured_reveal"] == 0.0 and start["predicted_reveal"] == 0.0
    assert not start["impact_visible"]
    assert all(card == 0.0 for card in start["cards"])
    assert end["measured_reveal"] == 1.0 and end["predicted_reveal"] == 1.0
    assert end["stumps_reveal"] == 1.0 and all(card == 1.0 for card in end["cards"])


def test_measured_reveals_before_predicted():
    d = AnimationDirector()
    mid = d.state_at(LBW, PAYLOAD, 1.8)          # inside the measured window, before prediction
    assert 0.0 < mid["measured_reveal"] < 1.0
    assert mid["predicted_reveal"] == 0.0


def test_cards_stagger_in_order():
    d = AnimationDirector()
    state = d.state_at(LBW, PAYLOAD, 3.6)        # first cards in, later ones not yet
    cards = state["cards"]
    assert cards[0] > cards[1] >= cards[2] >= cards[3]
    assert cards[0] > 0.0 and cards[3] == 0.0


def test_animation_state_has_no_camera_keys():
    # Camera moves now live in CameraDirector, not the animation state.
    assert "zoom" not in AnimationDirector().state_at(LBW, PAYLOAD, 1.0)


def test_stump_vibration_only_when_hitting():
    d = AnimationDirector()
    assert d.state_at(LBW, PAYLOAD, 3.2)["stump_vibration"] != 0.0
    not_hitting = d.state_at(LBW, {"decision_cards": [], "hitting": False}, 3.2)
    assert not_hitting["stump_vibration"] == 0.0


def test_each_review_type_has_its_own_timeline():
    # The director consumes whatever timeline the factory resolves — no per-type logic.
    d = AnimationDirector()
    lbw = d.state_at(timeline_for("lbw"), {}, 2.5)
    assert "measured" in lbw["reveals"] and "predicted" in lbw["reveals"]

    # Run Out reveals crease → bat → bails (no trajectory), longer duration.
    ro_tl = timeline_for("runout")
    ro = {"hitting": True, "decision_cards": [{}, {}, {}, {}]}
    early = d.state_at(ro_tl, ro, 1.1)
    assert early["reveals"]["crease"] > 0 and early["reveals"]["bat"] == 0
    assert d.state_at(ro_tl, ro, 2.0)["reveals"]["bat"] > 0
    full = d.full_state(ro_tl, ro)
    assert full["duration"] == 5.0 and full["reveals"]["bails"] == 1.0
    assert "measured" not in full["reveals"]

    # factory resolves aliases
    assert timeline_for("run_out").key == "runout"
