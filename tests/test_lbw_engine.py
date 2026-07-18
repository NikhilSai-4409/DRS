from core.lbw_engine import LBWDecisionEngine


def test_out_in_line_hitting_stumps():
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.9, 0.9)
    assert decision.verdict == "OUT"


def test_not_out_pitched_outside_leg():
    decision = LBWDecisionEngine().evaluate(-200, 0, 350, 0.9, 0.9)
    assert decision.verdict == "NOT_OUT"


def test_umpires_call_clipping_margin():
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.52, 0.9)
    assert decision.verdict == "UMPIRE_CALL"


def test_inconclusive_when_missing_data():
    decision = LBWDecisionEngine().evaluate(None, 0, 350, 0.9, 0.9)
    assert decision.verdict == "REVIEW_INCONCLUSIVE"


def test_impact_outside_off_with_shot():
    """Impact outside off stump WITH shot offered should be NOT OUT."""
    decision = LBWDecisionEngine().evaluate(0, 200, 350, 0.9, 0.9, shot_attempted=True)
    assert decision.verdict == "NOT_OUT"


def test_out_with_high_stump_probability():
    """High stump hit probability with in-line impact should be OUT."""
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.95, 0.95)
    assert decision.verdict == "OUT"
    assert decision.confidence > 0.7


def test_inconclusive_when_low_tracking_quality():
    """Low tracking quality should produce REVIEW_INCONCLUSIVE."""
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.9, 0.3)
    assert decision.verdict == "REVIEW_INCONCLUSIVE"


def test_decision_has_confidence():
    """Every decision should include a confidence score."""
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.9, 0.9)
    assert 0.0 <= decision.confidence <= 1.0


def test_decision_has_explanation():
    """Every decision should include explanation text."""
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.9, 0.9)
    assert decision.explanation is not None
    assert len(decision.explanation) > 0


def test_out_with_middle_stump_impact():
    """Ball pitching and impacting on middle stump should be OUT."""
    decision = LBWDecisionEngine().evaluate(0, 0, 300, 0.92, 0.88)
    assert decision.verdict == "OUT"


def test_not_out_when_impact_is_none():
    """None impact_y should be REVIEW_INCONCLUSIVE."""
    decision = LBWDecisionEngine().evaluate(0, None, 350, 0.9, 0.9)
    assert decision.verdict == "REVIEW_INCONCLUSIVE"


def test_umpires_call_borderline_stump():
    """Borderline stump hit probability should trigger umpire's call."""
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.50, 0.85)
    assert decision.verdict in ("UMPIRE_CALL", "NOT_OUT")


def test_ball_low_stump_probability_is_not_out():
    """Low stump hit probability should be NOT OUT."""
    decision = LBWDecisionEngine().evaluate(0, 0, 350, 0.2, 0.9)
    assert decision.verdict == "NOT_OUT"


def test_multiple_evaluations_independent():
    """Multiple evaluations should be independent (no state leakage)."""
    engine = LBWDecisionEngine()
    d1 = engine.evaluate(0, 0, 350, 0.9, 0.9)
    d2 = engine.evaluate(-200, 0, 350, 0.9, 0.9)
    d3 = engine.evaluate(0, 0, 350, 0.9, 0.9)
    assert d1.verdict == "OUT"
    assert d2.verdict == "NOT_OUT"
    assert d3.verdict == "OUT"

