"""LBW validation harness — measure DRS verdict accuracy against ground truth.

This is the interface-independent *engine*. The CLI (`scripts/validate_lbw.py`)
and the Testing-page API both call it; neither owns the logic.

The core idea the pipeline is missing: it already *saves* everything per clip,
but it can't tell you whether a decision was *correct*, or whether today's model
change flipped clip #7 from right to wrong. This module adds the ground-truth
comparison + cross-run regression tracking on top of the existing
``DeliveryTestingPipeline`` (which it drives unchanged, so we validate the REAL
analysis path, not a mock).

Design notes:
- The pipeline call is injected (``run_clip``) so scoring / reporting / regression
  logic is unit-testable without loading torch or reading video.
- Time source is injected (``clock``) so run ids and reports are deterministic
  under test.
- Nothing here draws or serves; it returns plain dataclasses / dicts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

try:  # anchor artifacts next to the rest of the testing data
    from config.settings import DATA_DIR

    TESTING_DIR = DATA_DIR / "testing"
except Exception:  # pragma: no cover - config always importable in practice
    TESTING_DIR = Path("data/testing")

DEFAULT_MANIFEST_PATH = TESTING_DIR / "validation_set.json"
RUNS_DIR = TESTING_DIR / "validation_runs"
HISTORY_PATH = TESTING_DIR / "validation_history.json"

# Confidence / frame thresholds below which a clip is flagged as low-trust even
# when the verdict happens to match (a correct-by-luck decision is still fragile).
LOW_CONFIDENCE = 0.40
MIN_REAL_FRAMES = 8

# Canonical verdicts. Everything is normalised into one of these before comparison
# so "NOT_OUT" / "not out" / "NOT OUT" all agree.
VERDICT_ALIASES = {
    "OUT": "OUT",
    "NOT OUT": "NOT OUT",
    "NOTOUT": "NOT OUT",
    "UMPIRE CALL": "UMPIRE'S CALL",
    "UMPIRES CALL": "UMPIRE'S CALL",
    "UMPIRE'S CALL": "UMPIRE'S CALL",
    "INCONCLUSIVE": "INCONCLUSIVE",
    "REVIEW INCONCLUSIVE": "INCONCLUSIVE",
    "PENDING": "PENDING",
}


def normalize_verdict(value: Any) -> str:
    """Fold formatting variants into a canonical verdict string."""
    if value is None:
        return "UNKNOWN"
    text = " ".join(str(value).upper().replace("_", " ").split())
    if not text:
        return "UNKNOWN"
    return VERDICT_ALIASES.get(text, text)


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ClipSpec:
    """One labelled clip from the validation manifest (the ground truth)."""

    id: str
    path: str
    expected_verdict: str
    ground: str = ""
    bowler: str = ""
    batsman: str = ""
    expected_pitching: str = ""
    expected_impact: str = ""
    expected_wickets: str = ""
    calibration_profile: str | None = None
    model_path: str | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClipSpec":
        known = {f for f in cls.__dataclass_fields__}
        payload = {k: v for k, v in data.items() if k in known}
        if not payload.get("id"):
            payload["id"] = Path(str(data.get("path", "clip"))).stem
        return cls(**payload)


@dataclass(slots=True)
class Manifest:
    clips: list[ClipSpec]
    defaults: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        clips = [ClipSpec.from_dict(item) for item in data.get("clips", [])]
        return cls(
            clips=clips,
            defaults=dict(data.get("defaults", {})),
            description=str(data.get("description", "")),
        )


def load_manifest(path: str | Path = DEFAULT_MANIFEST_PATH) -> Manifest:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Validation manifest not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return Manifest.from_dict(data)


# --------------------------------------------------------------------------- #
# Per-clip outcome
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ClipOutcome:
    id: str
    path: str
    ground: str
    bowler: str
    batsman: str
    expected_verdict: str
    actual_verdict: str
    match: bool
    # analysis read-outs
    pitching: Any = None
    impact: Any = None
    wickets: Any = None
    confidence: float = 0.0
    detection_confidence: float = 0.0
    frames_processed: int = 0
    real_detections: int = 0
    gap_fill: int = 0
    bounce_detected: bool = False
    impact_detected: bool = False
    ball_speed_kmh: float = 0.0
    # provenance
    calibration_intended: str | None = None
    calibration_used: str = "unknown"
    model_used: str | None = None
    # bookkeeping
    processing_time_s: float = 0.0
    replay_generated: bool = False
    replay_path: str | None = None
    error: str | None = None
    diagnostics: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return "correct" if self.match else "incorrect"

    @property
    def reason_for_failure(self) -> str:
        if self.status == "correct":
            return ""
        return "; ".join(self.diagnostics) or "unknown"


# --------------------------------------------------------------------------- #
# Run report
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ValidationRun:
    run_id: str
    timestamp: str
    model: str | None
    calibration: str | None
    clips: list[ClipOutcome]
    regressions: list[dict[str, Any]] = field(default_factory=list)
    improvements: list[dict[str, Any]] = field(default_factory=list)
    previous_run_id: str | None = None

    # ---- aggregates -------------------------------------------------------- #
    @property
    def total(self) -> int:
        return len(self.clips)

    @property
    def errors(self) -> int:
        return sum(1 for c in self.clips if c.error)

    @property
    def scored(self) -> int:
        """Clips that ran without error (accuracy denominator)."""
        return sum(1 for c in self.clips if not c.error)

    @property
    def correct(self) -> int:
        return sum(1 for c in self.clips if c.match and not c.error)

    @property
    def incorrect(self) -> int:
        return sum(1 for c in self.clips if not c.match and not c.error)

    @property
    def accuracy(self) -> float:
        return round(self.correct / self.scored, 4) if self.scored else 0.0

    @property
    def avg_detection_confidence(self) -> float:
        vals = [c.detection_confidence for c in self.clips if not c.error]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    @property
    def avg_processing_time_s(self) -> float:
        vals = [c.processing_time_s for c in self.clips]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    @property
    def replay_success(self) -> int:
        return sum(1 for c in self.clips if c.replay_generated)

    def summary(self) -> dict[str, Any]:
        """Compact record for history / regression diffs (no per-frame bloat)."""
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "model": self.model,
            "calibration": self.calibration,
            "total": self.total,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "errors": self.errors,
            "accuracy": self.accuracy,
            "avg_detection_confidence": self.avg_detection_confidence,
            "avg_processing_time_s": self.avg_processing_time_s,
            "replay_success": self.replay_success,
            "clip_matches": {c.id: c.match for c in self.clips},
            "clip_verdicts": {c.id: c.actual_verdict for c in self.clips},
        }

    def to_dict(self) -> dict[str, Any]:
        s = self.summary()
        clips = []
        for c in self.clips:
            d = asdict(c)
            # surface the derived fields (properties aren't captured by asdict)
            d["status"] = c.status
            d["reason_for_failure"] = c.reason_for_failure
            clips.append(d)
        s["clips"] = clips
        s["scored"] = self.scored
        s["regressions"] = self.regressions
        s["improvements"] = self.improvements
        s["previous_run_id"] = self.previous_run_id
        return s


# --------------------------------------------------------------------------- #
# Default pipeline runner (the only place torch/video is touched)
# --------------------------------------------------------------------------- #
_PIPELINE_CACHE: dict[str | None, Any] = {}


def _default_run_clip(spec: ClipSpec, model_path: str | None) -> dict[str, Any]:
    """Drive the real DeliveryTestingPipeline for one clip.

    Imported lazily so the engine (and its tests) don't pull torch at import.
    """
    from core.testing_pipeline import DeliveryTestingPipeline, AnalysisOptions

    if model_path not in _PIPELINE_CACHE:
        _PIPELINE_CACHE[model_path] = DeliveryTestingPipeline(model_path=model_path)
    pipeline = _PIPELINE_CACHE[model_path]
    options = AnalysisOptions(model_path=model_path)
    job_id = f"val_{spec.id}"
    return pipeline.process(job_id, [Path(spec.path)], options)


# --------------------------------------------------------------------------- #
# Extraction helpers — pull the read-outs the operator cares about out of the
# pipeline's result dict (see DeliveryTestingPipeline.process()).
# --------------------------------------------------------------------------- #
def _avg_detection_confidence(cameras: list[dict[str, Any]]) -> float:
    confs: list[float] = []
    for cam in cameras:
        for det in cam.get("detections", []):
            c = det.get("confidence") or 0.0
            if c > 0:
                confs.append(float(c))
    return round(sum(confs) / len(confs), 4) if confs else 0.0


def _extract(spec: ClipSpec, result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary", {}) or {}
    cameras = result.get("cameras", []) or []
    exports = result.get("exports", {}) or {}
    primary = cameras[0] if cameras else {}

    replay_path = exports.get("animation_video") or exports.get("analyzed_video")
    replay_ok = bool(replay_path) and Path(str(replay_path)).exists()

    return {
        "actual_verdict": normalize_verdict(summary.get("lbw_recommendation")),
        "pitching": summary.get("pitching_location"),
        "impact": summary.get("impact_location"),
        "wickets": summary.get("predicted_wicket_impact"),
        "confidence": float(summary.get("confidence_score") or 0.0),
        "ball_speed_kmh": float(summary.get("ball_speed_kmh") or primary.get("ball_speed_kmh") or 0.0),
        "detection_confidence": _avg_detection_confidence(cameras),
        "frames_processed": int(primary.get("frames_processed") or 0),
        "real_detections": sum(int(c.get("real_detection_count") or 0) for c in cameras),
        "gap_fill": sum(int(c.get("kalman_gap_fill_count") or 0) for c in cameras),
        "bounce_detected": any(c.get("bounce_point_px") for c in cameras),
        "impact_detected": any(c.get("impact_point_px") for c in cameras),
        "calibration_used": str(result.get("geometry_source") or "unknown"),
        "failed_gates": list((summary.get("gate", {}) or {}).get("failed_gates", [])),
        "replay_generated": replay_ok,
        "replay_path": str(replay_path) if replay_path else None,
    }


def _diagnose(outcome: ClipOutcome, failed_gates: Iterable[str]) -> list[str]:
    reasons: list[str] = []
    if outcome.error:
        reasons.append(f"pipeline error: {outcome.error}")
        return reasons
    if not outcome.match:
        reasons.append(
            f"verdict mismatch (expected {outcome.expected_verdict}, got {outcome.actual_verdict})"
        )
    if outcome.detection_confidence and outcome.detection_confidence < LOW_CONFIDENCE:
        reasons.append(f"low detection confidence ({outcome.detection_confidence:.2f})")
    if outcome.real_detections < MIN_REAL_FRAMES:
        reasons.append(f"few real detections ({outcome.real_detections} frames)")
    if not outcome.bounce_detected:
        reasons.append("no bounce point detected")
    if not outcome.impact_detected:
        reasons.append("no impact point detected")
    gates = [g for g in failed_gates if g]
    if gates:
        reasons.append("failed gates: " + ", ".join(gates[:4]))
    return reasons


# --------------------------------------------------------------------------- #
# Validator
# --------------------------------------------------------------------------- #
class LbwValidator:
    """Runs a manifest of clips through the pipeline and scores them.

    Parameters
    ----------
    run_clip:
        ``(ClipSpec, model_path) -> pipeline_result_dict``. Defaults to the real
        ``DeliveryTestingPipeline``. Injected in tests to avoid torch/video.
    clock:
        ``() -> datetime`` for deterministic run ids under test.
    """

    def __init__(
        self,
        run_clip: Callable[[ClipSpec, str | None], dict[str, Any]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._run_clip = run_clip or _default_run_clip
        self._clock = clock or datetime.now

    def run(
        self,
        manifest: Manifest,
        model_override: str | None = None,
        calibration_override: str | None = None,
        limit: int | None = None,
        previous: dict[str, Any] | None = None,
        progress: Callable[[int, int, ClipSpec], None] | None = None,
    ) -> ValidationRun:
        now = self._clock()
        run_id = now.strftime("%Y%m%d_%H%M%S")
        defaults = manifest.defaults or {}
        run_model = model_override or defaults.get("model_path")
        run_calib = calibration_override or defaults.get("calibration_profile")

        clips = manifest.clips[:limit] if limit else manifest.clips
        outcomes: list[ClipOutcome] = []
        for index, spec in enumerate(clips):
            if progress:
                progress(index, len(clips), spec)
            outcomes.append(
                self._run_one(spec, run_model, run_calib)
            )

        run = ValidationRun(
            run_id=run_id,
            timestamp=now.isoformat(timespec="seconds"),
            model=run_model,
            calibration=run_calib,
            clips=outcomes,
        )
        if previous:
            self._fill_regressions(run, previous)
        return run

    def _run_one(
        self, spec: ClipSpec, run_model: str | None, run_calib: str | None
    ) -> ClipOutcome:
        model_path = spec.model_path or run_model
        expected = normalize_verdict(spec.expected_verdict)
        started = time.perf_counter()
        try:
            result = self._run_clip(spec, model_path)
            elapsed = time.perf_counter() - started
            fields = _extract(spec, result)
            outcome = ClipOutcome(
                id=spec.id,
                path=spec.path,
                ground=spec.ground,
                bowler=spec.bowler,
                batsman=spec.batsman,
                expected_verdict=expected,
                actual_verdict=fields["actual_verdict"],
                match=(fields["actual_verdict"] == expected),
                pitching=fields["pitching"],
                impact=fields["impact"],
                wickets=fields["wickets"],
                confidence=round(fields["confidence"], 4),
                detection_confidence=fields["detection_confidence"],
                frames_processed=fields["frames_processed"],
                real_detections=fields["real_detections"],
                gap_fill=fields["gap_fill"],
                bounce_detected=fields["bounce_detected"],
                impact_detected=fields["impact_detected"],
                ball_speed_kmh=round(fields["ball_speed_kmh"], 2),
                calibration_intended=spec.calibration_profile or run_calib,
                calibration_used=fields["calibration_used"],
                model_used=model_path,
                processing_time_s=round(elapsed, 3),
                replay_generated=fields["replay_generated"],
                replay_path=fields["replay_path"],
            )
            outcome.diagnostics = _diagnose(outcome, fields["failed_gates"])
            return outcome
        except Exception as exc:  # a broken clip must not abort the whole run
            elapsed = time.perf_counter() - started
            outcome = ClipOutcome(
                id=spec.id,
                path=spec.path,
                ground=spec.ground,
                bowler=spec.bowler,
                batsman=spec.batsman,
                expected_verdict=expected,
                actual_verdict="ERROR",
                match=False,
                calibration_intended=spec.calibration_profile or run_calib,
                model_used=model_path,
                processing_time_s=round(elapsed, 3),
                error=f"{type(exc).__name__}: {exc}",
            )
            outcome.diagnostics = _diagnose(outcome, [])
            return outcome

    @staticmethod
    def _fill_regressions(run: ValidationRun, previous: dict[str, Any]) -> None:
        run.previous_run_id = previous.get("run_id")
        prev_match: dict[str, bool] = previous.get("clip_matches", {}) or {}
        prev_verdict: dict[str, str] = previous.get("clip_verdicts", {}) or {}
        for c in run.clips:
            if c.id not in prev_match:
                continue  # new clip — nothing to compare
            was = prev_match[c.id]
            now_ok = c.match and not c.error
            if was and not now_ok:
                run.regressions.append(
                    {
                        "id": c.id,
                        "was": prev_verdict.get(c.id),
                        "now": c.actual_verdict,
                        "expected": c.expected_verdict,
                    }
                )
            elif not was and now_ok:
                run.improvements.append(
                    {
                        "id": c.id,
                        "was": prev_verdict.get(c.id),
                        "now": c.actual_verdict,
                        "expected": c.expected_verdict,
                    }
                )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def load_history(path: str | Path = HISTORY_PATH) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def latest_run(path: str | Path = HISTORY_PATH) -> dict[str, Any] | None:
    history = load_history(path)
    return history[-1] if history else None


def append_history(run: ValidationRun, path: str | Path = HISTORY_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    history = load_history(p)
    history.append(run.summary())
    p.write_text(json.dumps(history, indent=2), encoding="utf-8")


def render_markdown(run: ValidationRun) -> str:
    lines: list[str] = []
    lines.append(f"# LBW Validation Report — {run.run_id}")
    lines.append("")
    lines.append(f"- **Run:** {run.timestamp}")
    lines.append(f"- **Model:** {run.model or 'default'}")
    lines.append(f"- **Calibration:** {run.calibration or 'heuristic'}")
    lines.append(
        f"- **Accuracy:** {run.correct}/{run.scored} "
        f"= **{run.accuracy * 100:.1f}%**"
        + (f" ({run.errors} errored)" if run.errors else "")
    )
    lines.append(f"- **Avg detection confidence:** {run.avg_detection_confidence:.2f}")
    lines.append(f"- **Avg processing time:** {run.avg_processing_time_s:.2f}s")
    lines.append(f"- **Replays generated:** {run.replay_success}/{run.total}")
    lines.append("")

    if run.regressions:
        lines.append("## ⚠️ Regressions vs previous run")
        for r in run.regressions:
            lines.append(
                f"- `{r['id']}` {r['was']} → {r['now']} (expected {r['expected']})"
            )
        lines.append("")
    if run.improvements:
        lines.append("## ✅ Improvements vs previous run")
        for r in run.improvements:
            lines.append(
                f"- `{r['id']}` {r['was']} → {r['now']} (expected {r['expected']})"
            )
        lines.append("")

    lines.append("## Per-clip results")
    lines.append("")
    lines.append("| Clip | Ground | Expected | Actual | Result | Det.conf | Reason |")
    lines.append("|------|--------|----------|--------|--------|----------|--------|")
    icon = {"correct": "✅", "incorrect": "❌", "error": "💥"}
    for c in run.clips:
        lines.append(
            f"| {c.id} | {c.ground or '—'} | {c.expected_verdict} | {c.actual_verdict} "
            f"| {icon.get(c.status, '?')} | {c.detection_confidence:.2f} "
            f"| {c.reason_for_failure or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_run(
    run: ValidationRun,
    runs_dir: str | Path = RUNS_DIR,
    history_path: str | Path = HISTORY_PATH,
) -> dict[str, str]:
    out_dir = Path(runs_dir) / run.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(run), encoding="utf-8")
    append_history(run, history_path)
    return {"json": str(json_path), "markdown": str(md_path), "dir": str(out_dir)}


def validate(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    model_override: str | None = None,
    calibration_override: str | None = None,
    limit: int | None = None,
    run_clip: Callable[[ClipSpec, str | None], dict[str, Any]] | None = None,
    clock: Callable[[], datetime] | None = None,
    history_path: str | Path = HISTORY_PATH,
    runs_dir: str | Path = RUNS_DIR,
    write: bool = True,
    progress: Callable[[int, int, ClipSpec], None] | None = None,
) -> ValidationRun:
    """One-call convenience: load → run (diffing vs last run) → persist."""
    manifest = load_manifest(manifest_path)
    validator = LbwValidator(run_clip=run_clip, clock=clock)
    previous = latest_run(history_path)
    run = validator.run(
        manifest,
        model_override=model_override,
        calibration_override=calibration_override,
        limit=limit,
        previous=previous,
        progress=progress,
    )
    if write:
        write_run(run, runs_dir=runs_dir, history_path=history_path)
    return run
