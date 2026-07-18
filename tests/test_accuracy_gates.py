from pathlib import Path

import pytest

from core.testing_pipeline import AnalysisOptions, DeliveryTestingPipeline


REQUIRED_GATES = [
    "model_map50",
    "ball_recall",
    "calibration",
    "sync",
    "decision_confidence",
    "tracking",
]


def test_all_available_gates_reported(synthetic_e2e_env):
    del synthetic_e2e_env
    fixture = Path("tests/fixtures/scenarios/lbw_out_in_line.mp4")
    pipeline = DeliveryTestingPipeline()
    result = pipeline.process("gate_coverage", [fixture], AnalysisOptions(max_frames=90, replay_generation=False))
    failed = result["summary"]["gate"]["failed_gates"]
    metrics = result["summary"]["gate"]["metrics"]
    reported = set(failed) | set(metrics)
    for gate in REQUIRED_GATES:
        assert any(item.startswith(gate) for item in reported), f"Gate '{gate}' missing from result"


def test_inconclusive_when_tracking_lost(synthetic_e2e_env):
    del synthetic_e2e_env
    fixture = Path("tests/fixtures/scenarios/lbw_inconclusive_tracking_lost.mp4")
    pipeline = DeliveryTestingPipeline()
    result = pipeline.process("tracking_lost", [fixture], AnalysisOptions(max_frames=90, replay_generation=False))
    assert result["summary"]["lbw_recommendation"] == "REVIEW INCONCLUSIVE"
    assert any(item.startswith("tracking") for item in result["summary"]["gate"]["failed_gates"])
