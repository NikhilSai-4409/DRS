"""Unit tests for the LBW validation engine.

These run WITHOUT torch, a model, video, or a backend — the pipeline call is
faked so we exercise scoring / diagnostics / aggregates / regression / reporting
logic in isolation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from core.lbw_validation import (
    ClipSpec,
    LbwValidator,
    Manifest,
    load_history,
    load_manifest,
    normalize_verdict,
    render_markdown,
    write_run,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def fake_result(
    verdict: str = "OUT",
    *,
    det_conf: float = 0.9,
    real: int = 20,
    gap: int = 2,
    bounce=(100, 200),
    impact=(300, 400),
    animation: str | None = None,
    confidence: float = 0.8,
    gates: list[str] | None = None,
) -> dict:
    """A dict shaped like DeliveryTestingPipeline.process()."""
    return {
        "summary": {
            "lbw_recommendation": verdict,
            "pitching_location": "in-line",
            "impact_location": "in-line",
            "predicted_wicket_impact": "hitting",
            "confidence_score": confidence,
            "ball_speed_kmh": 120.0,
            "gate": {"failed_gates": gates or []},
        },
        "cameras": [
            {
                "detections": [{"confidence": det_conf}] * real,
                "frames_processed": real + gap,
                "real_detection_count": real,
                "kalman_gap_fill_count": gap,
                "bounce_point_px": list(bounce) if bounce else None,
                "impact_point_px": list(impact) if impact else None,
                "ball_speed_kmh": 120.0,
            }
        ],
        "exports": {"animation_video": animation, "analyzed_video": None},
        "geometry_source": "heuristic",
    }


def runner_from(mapping: dict):
    def _run(spec: ClipSpec, model_path):
        val = mapping[spec.id]
        if isinstance(val, Exception):
            raise val
        return val

    return _run


def spec(id_: str, expected: str) -> ClipSpec:
    return ClipSpec(id=id_, path=f"{id_}.mp4", expected_verdict=expected)


FIXED_CLOCK = lambda: datetime(2026, 7, 4, 12, 0, 0)  # noqa: E731


# --------------------------------------------------------------------------- #
# Verdict normalization
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("OUT", "OUT"),
        ("out", "OUT"),
        ("NOT_OUT", "NOT OUT"),
        ("not out", "NOT OUT"),
        ("  NotOut ", "NOT OUT"),
        ("umpire's call", "UMPIRE'S CALL"),
        ("UMPIRE CALL", "UMPIRE'S CALL"),
        ("review inconclusive", "INCONCLUSIVE"),
        (None, "UNKNOWN"),
    ],
)
def test_normalize_verdict(raw, expected):
    assert normalize_verdict(raw) == expected


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def test_clipspec_id_falls_back_to_stem():
    s = ClipSpec.from_dict({"path": "clips/yorker_07.mp4", "expected_verdict": "OUT"})
    assert s.id == "yorker_07"


def test_manifest_from_dict_reads_defaults_and_clips():
    m = Manifest.from_dict(
        {
            "description": "d",
            "defaults": {"model_path": "m.pt"},
            "clips": [{"id": "a", "path": "a.mp4", "expected_verdict": "OUT"}],
        }
    )
    assert m.defaults["model_path"] == "m.pt"
    assert len(m.clips) == 1 and m.clips[0].id == "a"


def test_load_manifest_roundtrip(tmp_path: Path):
    p = tmp_path / "vs.json"
    p.write_text(
        json.dumps({"clips": [{"id": "a", "path": "a.mp4", "expected_verdict": "OUT"}]}),
        encoding="utf-8",
    )
    m = load_manifest(p)
    assert m.clips[0].expected_verdict == "OUT"


def test_load_manifest_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nope.json")


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def test_scores_correct_and_incorrect():
    manifest = Manifest(clips=[spec("c1", "OUT"), spec("c2", "NOT OUT")])
    runner = runner_from({"c1": fake_result("OUT"), "c2": fake_result("OUT")})
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)

    assert run.total == 2
    assert run.correct == 1
    assert run.incorrect == 1
    assert run.accuracy == 0.5
    by_id = {c.id: c for c in run.clips}
    assert by_id["c1"].match is True
    assert by_id["c2"].match is False
    assert by_id["c2"].actual_verdict == "OUT"


def test_verdict_formatting_variants_still_match():
    manifest = Manifest(clips=[spec("c1", "NOT OUT")])
    runner = runner_from({"c1": fake_result("not_out")})
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)
    assert run.clips[0].match is True


def test_error_clip_does_not_abort_run():
    manifest = Manifest(clips=[spec("c1", "OUT"), spec("c2", "OUT")])
    runner = runner_from({"c1": RuntimeError("boom"), "c2": fake_result("OUT")})
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)

    by_id = {c.id: c for c in run.clips}
    assert by_id["c1"].status == "error"
    assert "boom" in by_id["c1"].error
    # error clip is excluded from the accuracy denominator
    assert run.errors == 1
    assert run.scored == 1
    assert run.correct == 1
    assert run.accuracy == 1.0


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def test_diagnostics_explain_a_mismatch():
    manifest = Manifest(clips=[spec("c1", "NOT OUT")])
    runner = runner_from(
        {"c1": fake_result("OUT", det_conf=0.2, real=3, bounce=None, gates=["low_track"])}
    )
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)
    reason = run.clips[0].reason_for_failure
    assert "verdict mismatch" in reason
    assert "low detection confidence" in reason
    assert "few real detections" in reason
    assert "no bounce point detected" in reason
    assert "failed gates: low_track" in reason


def test_correct_clean_clip_has_no_reason():
    manifest = Manifest(clips=[spec("c1", "OUT")])
    runner = runner_from({"c1": fake_result("OUT")})
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)
    c = run.clips[0]
    assert c.status == "correct"
    assert c.reason_for_failure == ""
    assert c.diagnostics == []


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #
def test_replay_success_counts_existing_file(tmp_path: Path):
    video = tmp_path / "anim.mp4"
    video.write_bytes(b"x")
    manifest = Manifest(clips=[spec("c1", "OUT"), spec("c2", "OUT")])
    runner = runner_from(
        {
            "c1": fake_result("OUT", animation=str(video)),
            "c2": fake_result("OUT", animation=str(tmp_path / "missing.mp4")),
        }
    )
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)
    assert run.replay_success == 1
    by_id = {c.id: c for c in run.clips}
    assert by_id["c1"].replay_generated is True
    assert by_id["c2"].replay_generated is False


def test_avg_detection_confidence():
    manifest = Manifest(clips=[spec("c1", "OUT"), spec("c2", "OUT")])
    runner = runner_from(
        {"c1": fake_result("OUT", det_conf=0.8), "c2": fake_result("OUT", det_conf=0.6)}
    )
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)
    assert run.avg_detection_confidence == pytest.approx(0.7, abs=1e-6)


# --------------------------------------------------------------------------- #
# Regression / improvement diff
# --------------------------------------------------------------------------- #
def test_regressions_and_improvements_vs_previous():
    manifest = Manifest(clips=[spec("c1", "OUT"), spec("c2", "OUT")])
    # c1 was correct, now wrong -> regression; c2 was wrong, now correct -> improvement
    runner = runner_from({"c1": fake_result("NOT OUT"), "c2": fake_result("OUT")})
    previous = {
        "run_id": "prev",
        "clip_matches": {"c1": True, "c2": False},
        "clip_verdicts": {"c1": "OUT", "c2": "NOT OUT"},
    }
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest, previous=previous)

    assert run.previous_run_id == "prev"
    assert [r["id"] for r in run.regressions] == ["c1"]
    assert run.regressions[0]["was"] == "OUT" and run.regressions[0]["now"] == "NOT OUT"
    assert [r["id"] for r in run.improvements] == ["c2"]


def test_new_clip_not_treated_as_regression():
    manifest = Manifest(clips=[spec("c_new", "OUT")])
    runner = runner_from({"c_new": fake_result("NOT OUT")})  # wrong, but brand new
    previous = {"run_id": "prev", "clip_matches": {"c1": True}, "clip_verdicts": {"c1": "OUT"}}
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest, previous=previous)
    assert run.regressions == []
    assert run.improvements == []


# --------------------------------------------------------------------------- #
# Persistence + rendering
# --------------------------------------------------------------------------- #
def test_write_run_persists_report_and_history(tmp_path: Path):
    manifest = Manifest(clips=[spec("c1", "OUT")])
    runner = runner_from({"c1": fake_result("OUT")})
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)

    runs_dir = tmp_path / "runs"
    history = tmp_path / "history.json"
    paths = write_run(run, runs_dir=runs_dir, history_path=history)

    report = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert report["accuracy"] == 1.0
    assert report["clips"][0]["status"] == "correct"

    hist = load_history(history)
    assert len(hist) == 1
    assert hist[0]["run_id"] == run.run_id

    # a second write appends rather than overwrites
    write_run(run, runs_dir=runs_dir, history_path=history)
    assert len(load_history(history)) == 2


def test_render_markdown_contains_key_facts():
    manifest = Manifest(clips=[spec("c1", "OUT"), spec("c2", "NOT OUT")])
    runner = runner_from({"c1": fake_result("OUT"), "c2": fake_result("OUT")})
    run = LbwValidator(run_clip=runner, clock=FIXED_CLOCK).run(manifest)
    md = render_markdown(run)
    assert "LBW Validation Report" in md
    assert "50.0%" in md
    assert "| c1 |" in md and "| c2 |" in md
