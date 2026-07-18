from pathlib import Path

import pytest

from core.testing_pipeline import AnalysisOptions, DeliveryTestingPipeline


SCENARIOS = [
    ("lbw_out_in_line.mp4", "OUT"),
    ("lbw_not_out_outside_leg.mp4", "NOT OUT"),
    ("lbw_not_out_misses_stumps.mp4", "NOT OUT"),
    ("lbw_inconclusive_tracking_lost.mp4", "REVIEW INCONCLUSIVE"),
]


@pytest.mark.parametrize(("clip", "expected"), SCENARIOS)
def test_lbw_decision(clip, expected, synthetic_e2e_env):
    del synthetic_e2e_env
    path = Path("tests/fixtures/scenarios") / clip
    pipeline = DeliveryTestingPipeline()
    result = pipeline.process(f"e2e_{path.stem}", [path], AnalysisOptions(max_frames=90, replay_generation=False))
    actual = result["summary"]["lbw_recommendation"]
    assert actual == expected, (
        f"{clip}: expected {expected}, got {actual}\n"
        f"Gates: {result['summary'].get('gate', {})}"
    )
