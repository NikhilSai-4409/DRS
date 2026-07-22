"""Base classes for the modular DRS review engine.

Each review type (LBW, Edge, Wide, Front Foot No Ball, and future modules such as
Run Out / Stumping / High Full Toss) is a :class:`ReviewModule`. A module declares
the camera role it wants, the timeline stages it reports, and an :meth:`analyze`
method that turns a :class:`ReviewContext` (buffered frames + detector +
calibration) into an analysis payload merged into the decision returned to the UI.

The geometry all modules share lives here: detection over the replay buffer and
projection from image pixels to pitch-world millimetres via the manual pitch
homography (off stump = 0, leg stump = stump width, popping crease = ``-crease``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from config.settings import REVIEW_ANALYSIS_MAX_FRAMES
from core.camera_roles import BALL_TRACKING, canonical_role
from core.pitch_calibration import ICCPitchDimensions
from utils.logger import get_logger

log = get_logger("review_engine")


@dataclass(slots=True)
class BallSample:
    """A single ball detection projected into pitch coordinates."""

    frame_id: int
    timestamp_ms: float
    cx: float
    cy: float
    confidence: float
    radius_px: float
    lateral_mm: Optional[float] = None  # signed offset from the middle stump
    along_mm: Optional[float] = None    # signed distance down the pitch (crease < 0)


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """Everything a review module needs to analyse one appeal.

    Immutable by design: it is built once from a single synchronized
    :class:`~core.frame_buffer.SynchronizedFrames` snapshot, so two modules (and a
    re-run of the same appeal) always see the exact same frames, timestamps and
    calibration. Every review is therefore deterministic.
    """

    review_type: str
    frames: dict[int, list]            # camera_id -> list[VideoFrame] (replay buffer)
    detector: Any                      # BallDetector (or None)
    calibrators: dict[int, Any]        # camera_id -> ManualPitchCalibrator with a loaded profile
    camera_roles: dict[int, str] = field(default_factory=dict)
    primary_camera_id: Optional[int] = None
    timestamps: dict[int, list] = field(default_factory=dict)   # camera_id -> [timestamp_ms ...]
    telemetry: dict[int, Any] = field(default_factory=dict)     # camera_id -> CameraTelemetry-like dict
    reference_timestamp_ms: Optional[float] = None
    dimensions: ICCPitchDimensions = field(default_factory=ICCPitchDimensions)
    max_frames: int = REVIEW_ANALYSIS_MAX_FRAMES

    @property
    def crease_along_mm(self) -> float:
        """along_mm of the popping crease line in this calibration's frame."""
        return -self.dimensions.crease_to_stumps_m * 1000.0


class ReviewModule:
    """Base review module. Subclasses implement :meth:`analyze`.

    Each module also declares its full CAPABILITY CONTRACT — required camera role,
    the evidence it produces, how its replay should render, and the rows its
    decision card shows. The dashboard renders whatever the active module declares
    (served by ``/api/review-types``), so a new review type is a new module here,
    not a UI redesign. Decision-card VALUES come from ``summary.measurements`` at
    review time; ``decision_card`` lists the row labels the card shows while waiting.
    """

    key: str = "base"
    label: str = "Review"
    required_role: str = BALL_TRACKING
    timeline: tuple[str, ...] = ("Release", "Decision")
    evidence: tuple[str, ...] = ()               # evidence keys this module produces
    replay_mode: str = "generic"                 # trajectory | wide_line | freeze_frame | frame_stepping | audio_sync | generic
    decision_card: tuple[str, ...] = ("Decision",)
    export_format: str = "review_json"
    # Which replay/UI capabilities this review type uses. The dashboard enables or
    # disables whole UI sections from this map (replay overlays, layer toggles, jump
    # actions) instead of scattering per-type conditionals through the frontend.
    supports: dict = {
        "trajectory": False,    # ball-path overlay on the replay
        "guideline": False,     # wide-line guide overlay
        "crease": False,        # popping-crease guide overlay
        "audio": False,         # audio/spike timeline strip
        "freeze_frame": False,  # decision-frame freeze workflow
        "frame_step": True,     # fine frame stepping matters for this type
        "zoom": False,          # magnifier on the replay stage
        "measurement": True,    # produces numeric measurements
    }

    def describe(self) -> dict:
        """The capability contract served to the dashboard."""
        return {
            "key": self.key,
            "label": self.label,
            "required_role": self.required_role,
            "timeline": list(self.timeline),
            "evidence": list(self.evidence),
            "replay_mode": self.replay_mode,
            "decision_card": list(self.decision_card),
            "export_format": self.export_format,
            "supports": {**ReviewModule.supports, **self.supports},
        }

    # ----- camera selection by capability/role (not by number) -----
    def select_camera(self, ctx: ReviewContext) -> Optional[int]:
        required = canonical_role(self.required_role)
        for camera_id, role in ctx.camera_roles.items():
            if canonical_role(role) == required and ctx.frames.get(camera_id):
                return camera_id
        if ctx.primary_camera_id is not None and ctx.frames.get(ctx.primary_camera_id):
            return ctx.primary_camera_id
        for camera_id, frame_list in ctx.frames.items():
            if frame_list:
                return camera_id
        return None

    # ----- shared detection + projection helpers -----
    def detect_samples(self, ctx: ReviewContext, camera_id: int) -> list[BallSample]:
        """Run ball detection over the (capped) replay buffer for one camera."""
        frames = ctx.frames.get(camera_id, [])[-ctx.max_frames:]
        detector = ctx.detector
        samples: list[BallSample] = []
        if detector is None:
            return samples
        for vf in frames:
            try:
                result = detector.detect(vf.frame, vf.frame_id, vf.timestamp_ms, camera_id)
            except Exception as exc:  # detection must never break a review
                log.warning("Detector failed on cam {} frame {}: {}", camera_id, getattr(vf, "frame_id", "?"), exc)
                continue
            best = result.best
            if best is None:
                continue
            radius_px = max(best.x2 - best.x1, best.y2 - best.y1) / 2.0
            samples.append(
                BallSample(
                    frame_id=best.frame_id,
                    timestamp_ms=best.timestamp_ms,
                    cx=float(best.cx),
                    cy=float(best.cy),
                    confidence=float(best.confidence),
                    radius_px=float(radius_px),
                )
            )
        return samples

    def project(self, ctx: ReviewContext, camera_id: int, samples: list[BallSample]) -> list[BallSample]:
        """Fill lateral_mm/along_mm on each sample using the camera's homography."""
        calibrator = ctx.calibrators.get(camera_id)
        if calibrator is None:
            return [s for s in samples]
        for sample in samples:
            mapped = calibrator.pixel_to_pitch_mm(camera_id, sample.cx, sample.cy)
            if mapped is not None:
                sample.lateral_mm, sample.along_mm = mapped
        return samples

    # ----- result scaffolding -----
    def timeline_payload(self, complete: bool) -> list[dict]:
        status = "complete" if complete else "active"
        rows = [{"label": label, "status": "complete"} for label in self.timeline[:-1]]
        rows.append({"label": self.timeline[-1], "status": status})
        return rows

    def base_result(self, explanation: str, confidence: float | None = None) -> dict:
        """A clean decision overlay that clears LBW-only fields for non-LBW reviews."""
        return {
            "review_type": self.key,
            "overall_confidence": confidence,
            "ball_confidence": confidence,
            "trajectory": [],
            "predicted_extension": [],
            "impact_point": None,
            "impact_marker": None,
            "bounce_point": None,
            "wicket_zone_status": "--",
            "wicket_prediction": None,
            "ball_speed_kmh": None,
            "timeline": self.timeline_payload(complete=confidence is not None),
            "explanation": explanation,
        }

    def analyze(self, ctx: ReviewContext) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


def interpolate_lateral_at(samples: list[BallSample], target_along_mm: float) -> Optional[float]:
    """Lateral offset (mm) where the ball track crosses a given down-pitch line.

    Linearly interpolates between the two projected samples that bracket
    ``target_along_mm``; falls back to the nearest sample when the track does not
    straddle the line.
    """
    points = [s for s in samples if s.along_mm is not None and s.lateral_mm is not None]
    if not points:
        return None
    points.sort(key=lambda s: s.along_mm)
    for first, second in zip(points, points[1:]):
        lo, hi = first.along_mm, second.along_mm
        if lo == hi:
            continue
        if min(lo, hi) <= target_along_mm <= max(lo, hi):
            ratio = (target_along_mm - lo) / (hi - lo)
            return first.lateral_mm + ratio * (second.lateral_mm - first.lateral_mm)
    nearest = min(points, key=lambda s: abs(s.along_mm - target_along_mm))
    return nearest.lateral_mm


def confidence_score(avg_detection_conf: float, calibrator: Any, sample_count: int) -> float:
    """Blend detection confidence, calibration quality and track coverage."""
    coverage = max(0.0, min(1.0, sample_count / 8.0))
    calib_factor = 0.85
    profile = getattr(calibrator, "_active_profile", None)
    error_cm = None
    if profile is not None:
        error_cm = getattr(profile, "homography_error_cm", None)
    if error_cm is not None:
        calib_factor = max(0.3, min(1.0, 1.0 - (error_cm / 5.0)))
    score = avg_detection_conf * (0.55 + 0.45 * coverage) * calib_factor
    return max(0.0, min(1.0, score))


# --------------------------------------------------------------------------- #
# Unified review result
#
# Every review type — LBW, Wide, No Ball, Edge, and future ones — is normalised
# into this one shape, so the dashboard renders them all the same way. Modules
# keep emitting their type-specific ``*_analysis`` block (the detail panels still
# read it); ``build_review_result`` folds whatever the module produced into the
# common contract the generic UI consumes.
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ReviewResult:
    """The single structure every review module returns."""

    review_type: str
    verdict: str                       # OUT / NOT OUT / WIDE / NO BALL / LEGAL / AWAITING / ...
    confidence: Optional[float]
    measurements: list = field(default_factory=list)   # [{label, value, flag?}]
    summary: dict = field(default_factory=dict)        # {headline, measurements, confidence, warnings}
    replay: Optional[dict] = None
    overlays: dict = field(default_factory=dict)       # observed markers (ball centre, foot) — analytical
    geometry: dict = field(default_factory=dict)       # analytical world track + markers (no projection)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "review_type": self.review_type,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "measurements": self.measurements,
            "summary": self.summary,
            "replay": self.replay,
            "overlays": self.overlays,
            "geometry": self.geometry,
            "warnings": self.warnings,
        }


def _verdict_for(review_type: str, decision: dict) -> str:
    """Reduce a decision overlay to one verdict word. New review types add a
    branch here; the UI never changes."""
    wide = decision.get("wide_analysis") or {}
    no_ball = decision.get("no_ball_analysis") or {}
    # The DECLARED review type always wins; the *_analysis presence checks are a
    # fallback for legacy untyped decisions only. An LBW review legitimately carries
    # no_ball/edge analysis (protocol pre-checks) without becoming a No Ball review.
    known = {"lbw", "wide", "noball", "no_ball", "front_foot", "frontfoot",
             "runout", "run_out", "stumping", "stump", "edge", "ultraedge", "ultra_edge", "snicko"}
    untyped = review_type not in known
    if review_type == "wide" or (untyped and "wide_analysis" in decision):
        return {True: "WIDE", False: "NOT WIDE"}.get(wide.get("is_wide"), "AWAITING")
    if review_type in {"noball", "no_ball", "front_foot", "frontfoot"} or (untyped and "no_ball_analysis" in decision):
        return {True: "NO BALL", False: "LEGAL"}.get(no_ball.get("is_no_ball"), "AWAITING")
    if review_type in {"runout", "run_out"} or (untyped and "run_out_analysis" in decision):
        run_out = decision.get("run_out_analysis") or {}
        return {True: "OUT", False: "NOT OUT"}.get(run_out.get("is_out"), "AWAITING")
    if review_type in {"stumping", "stump"} or (untyped and "stumping_analysis" in decision):
        stumping = decision.get("stumping_analysis") or {}
        return {True: "OUT", False: "NOT OUT"}.get(stumping.get("is_out"), "AWAITING")
    if review_type in {"edge", "ultraedge", "ultra_edge", "snicko"}:
        edge = decision.get("edge_analysis") or {}
        if edge.get("inconclusive"):
            return "INCONCLUSIVE"
        hotspot = decision.get("hotspot_analysis") or {}
        if (edge.get("edge_probability") or 0.0) >= 0.5 or hotspot.get("contact_detected"):
            return "EDGE"
        return "NO EDGE"
    # LBW / generic: only a *string* verdict is meaningful — fields like
    # wicket_prediction hold geometry (a collision point), not a verdict word.
    for key in ("verdict", "wicket_prediction", "wicket_zone_status"):
        value = decision.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != "--":
            return value.upper().replace("_", " ")
    status = str(decision.get("status", "")).upper().replace("_", " ")
    return status or "AWAITING"


def _overlays_for(decision: dict) -> dict:
    """Collect whatever draw primitives the decision carries, keyed uniformly."""
    overlays: dict = {}
    for key in ("trajectory", "predicted_extension", "impact_point", "bounce_point", "impact_marker"):
        value = decision.get(key)
        if value:
            overlays[key] = value
    wide = decision.get("wide_analysis") or {}
    if wide.get("ball_centre") is not None:
        overlays["ball_centre"] = wide["ball_centre"]
        overlays["wide_line_cm"] = wide.get("wide_line_cm")
    no_ball = decision.get("no_ball_analysis") or {}
    if no_ball.get("toe_px") is not None:
        overlays["toe_px"] = no_ball["toe_px"]
        overlays["heel_px"] = no_ball.get("heel_px")
    return overlays


def build_review_result(review_type: str, decision: dict, replay: Optional[dict] = None) -> dict:
    """Normalise any review's decision into the unified, purely-analytical ReviewResult.

    No projection or drawing happens here — ``geometry`` is world-space analysis;
    turning it into a render payload is core.overlay_builder's job.
    """
    summary = decision.get("summary") or {}
    confidence = decision.get("overall_confidence")
    if confidence is None:
        confidence = summary.get("confidence")
    return ReviewResult(
        review_type=review_type,
        verdict=_verdict_for(review_type, decision),
        confidence=confidence,
        measurements=list(summary.get("measurements", [])),
        summary=summary,
        replay=replay if replay is not None else decision.get("replay"),
        overlays=_overlays_for(decision),
        geometry=decision.get("geometry") or {},
        warnings=list(summary.get("warnings", [])),
    ).to_dict()
