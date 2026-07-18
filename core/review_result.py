"""Canonical DRS review artifact — the single object every surface consumes.

The system had trajectory scattered across three incompatible shapes (the physics
``TrajectoryPredictor``, an ad-hoc ``decision.trajectory`` list, and a baked
``trajectory_svg`` string) and none was authoritative — so the renderer always fell
back to a template. This module collapses that into one spine:

    ObservedTrajectory   — what the camera(s) actually saw, in image pixels. No
                           physics, no bounce, no LBW. Just observations.
    PredictedTrajectory  — a physics estimate built FROM an ObservedTrajectory by a
                           swappable ``TrajectoryProducer``. It never knows its own
                           source, so the producer (physics today, EKF or multi-camera
                           triangulation later) can be replaced without touching
                           anything downstream.
    ReviewResult         — the canonical artifact: trajectory + decision + diagnostics.
                           Formalizes the dict ``testing_pipeline`` already returns.

Validity is deliberately separate from confidence. A trajectory can be *valid* (the
data is structurally sound) yet *low confidence* (heuristic geometry, imperfect
tracking) — that renders, labelled LOW CONFIDENCE. Fallback is reserved for
*structural* failure (too few real detections, NaNs, impossible speed, no prediction),
so we never smooth garbage into something that looks authoritative.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from core.trajectory import predict_with_physics

# --- Structural validity thresholds -----------------------------------------
# These gate VALIDITY (is the data sound enough to trust at all), not confidence.
# The real gates are detection count, finite values, and a produced prediction; the
# speed bounds are only a wide backstop for a fully stalled or exploded track, because
# a single-camera speed estimate is itself too rough to reject a real delivery on.
# --- Observation / tracking quality (geometry-INDEPENDENT) ------------------
# These judge the observation stream itself and hold on ANY camera, calibrated or
# not. They are the "is this a real, clean track" checks — separate from physics.
MIN_REAL_DETECTIONS = 6          # real YOLO detections, not Kalman gap-fills
MIN_REAL_RATIO = 0.35            # a track that is mostly Kalman gap-fill is hallucinated
MAX_GAP_MS = 600.0              # a longer dropout between detections = broken track
MIN_MEDIAN_CONF = 0.25           # median detector confidence floor
MAX_REVERSAL_RATIO = 0.6         # fraction of frame-to-frame >120° direction flips (jitter)
MIN_MOTION_PX = 1.0              # displacements below this are noise, ignored for direction

# --- Physical-speed bounds (CALIBRATED geometry only) -----------------------
# On a heuristic (single, uncalibrated) camera looking down the pitch the ball moves in
# depth, so pixel speed is not real velocity and must never gate validity.
MIN_SPEED_KMH = 3.0
MAX_SPEED_KMH = 260.0


@dataclass(slots=True)
class ObservedPoint:
    """One tracked ball position in image pixels. ``real`` distinguishes a genuine
    detection from a Kalman gap-fill — a path that is mostly gap-fill is low-validity
    even when it looks smooth."""

    frame_id: int
    t_ms: float
    x_px: float
    y_px: float
    confidence: float
    real: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ObservedTrajectory:
    """Pure observation layer — what the cameras saw, before any physics.

    Holds the COMPLETE track (`points`) and never drops anything — the full observation
    is kept for debugging (e.g. inspecting the post-impact tail for an edge review).
    `display_end_frame` is a DISPLAY policy: consumers that only want the delivery
    (renderer, physics fit) read `display_points()`; the raw data stays intact."""

    points: list[ObservedPoint]
    camera_id: int
    fps: float
    display_end_frame: int | None = None      # display/analysis cutoff; None = use all
    end_reason: str = ""                        # pipeline state that ended the trajectory

    @property
    def real_count(self) -> int:
        return sum(1 for p in self.points if p.real)

    @property
    def tracked_count(self) -> int:
        return len(self.points)

    def display_points(self) -> list[ObservedPoint]:
        """The points a renderer/fit should use — the delivery up to the display cutoff.
        Complete data is still in `points`; this only chooses what to show/analyse."""
        if self.display_end_frame is None:
            return self.points
        return [p for p in self.points if p.frame_id <= self.display_end_frame]

    @classmethod
    def from_tracks(cls, tracks: list[dict[str, Any]], camera_id: int, fps: float) -> "ObservedTrajectory":
        """Build from the tracker's exported point dicts (AssociatedTrackPoint.to_dict)."""
        points = [
            ObservedPoint(
                frame_id=int(t.get("frame_id", 0)),
                t_ms=float(t.get("timestamp_ms", 0.0)),
                x_px=float(t.get("x", 0.0)),
                y_px=float(t.get("y", 0.0)),
                confidence=float(t.get("confidence", 0.0)),
                real=bool(t.get("real_detection", not t.get("predicted", False))),
            )
            for t in tracks
        ]
        return cls(points=points, camera_id=int(camera_id), fps=float(fps))

    def to_dict(self) -> dict[str, Any]:
        # The COMPLETE track is exposed; the renderer slices to display_end_frame itself.
        return {
            "camera_id": self.camera_id,
            "fps": self.fps,
            "real_count": self.real_count,
            "tracked_count": self.tracked_count,
            "display_end_frame": self.display_end_frame,
            "display_point_count": len(self.display_points()),
            "end_reason": self.end_reason,
            "points": [p.to_dict() for p in self.points],
        }


@dataclass(slots=True)
class PredictedTrajectory:
    """Physics layer — an estimate of what happened, built from an ObservedTrajectory.

    Carries the validity/confidence split so every consumer reads the same judgement
    instead of re-deciding whether the path is trustworthy.
    """

    observed: ObservedTrajectory
    fitted_points: list[dict[str, float]]      # world metres {x,y,z}
    predicted_path: list[dict[str, float]]     # forward extension {x,y,z}
    bounce: dict[str, float] | None
    impact: dict[str, float] | None
    wicket: dict[str, Any] | None
    release_speed_kmh: float | None      # None when geometry is heuristic — pixel speed
    model_used: str                      # is not real velocity on an uncalibrated view
    geometry_source: str                       # "heuristic" | "calibration"
    source: str                                # which producer built this
    valid: bool
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "valid": self.valid,
            "reasons": self.reasons,
            "confidence": self.confidence,
            "geometry_source": self.geometry_source,
            "release_speed_kmh": self.release_speed_kmh,
            "model_used": self.model_used,
            "observed": self.observed.to_dict(),
            "fitted_points": self.fitted_points,
            "predicted_path": self.predicted_path,
            "bounce_point": self.bounce,
            "impact_point": self.impact,
            "wicket": self.wicket,
            # Alias so the diagnostics point-count reads the tracked observations.
            "points": [p.to_dict() for p in self.observed.points],
        }


@dataclass(slots=True)
class ReviewResult:
    """The one artifact. Animation, replay, export, diagnostics and validation all
    read this — nobody reconstructs part of the analysis independently."""

    job_id: str
    trajectory: PredictedTrajectory
    decision: dict[str, Any]
    geometry_source: str
    confidence: float
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "trajectory": self.trajectory.to_dict(),
            "decision": self.decision,
            "geometry_source": self.geometry_source,
            "confidence": self.confidence,
            "diagnostics": self.diagnostics,
        }


# --- Producer abstraction ----------------------------------------------------
# PredictedTrajectory must not know where it came from. Everything downstream sees
# only PredictedTrajectory, so swapping the physics for an EKF or multi-camera
# triangulation later is a one-class change with zero downstream churn.


@runtime_checkable
class TrajectoryProducer(Protocol):
    def predict(self, observed: ObservedTrajectory) -> PredictedTrajectory: ...


class PhysicsTrajectoryProducer:
    """Default producer: wraps ``core.trajectory`` (gravity + drag + Magnus).

    The image→world mapping here is deliberately crude (single-camera, heuristic
    pixels-per-metre) — which is precisely why ``geometry_source`` is surfaced and
    the confidence is graded down. Calibrated geometry replaces the mapping, not this
    class's contract.
    """

    def __init__(self, pixels_per_meter: float, geometry_source: str, source: str = "physics") -> None:
        self.pixels_per_meter = max(1.0, float(pixels_per_meter))
        self.geometry_source = geometry_source
        self.source = source

    def predict(self, observed: ObservedTrajectory) -> PredictedTrajectory:
        calibrated = self.geometry_source == "calibration"
        # Fit physics on the DISPLAY segment (release→impact); the post-impact drift in
        # the full track would corrupt the fit. The full observation is still preserved
        # on the returned PredictedTrajectory.observed.
        work = ObservedTrajectory(observed.display_points(), observed.camera_id, observed.fps)
        world = self._to_world(work)
        fitted = [{"x": x, "y": y, "z": z} for (x, y, z) in world]
        # Pixel-derived speed is only physically meaningful with a homography. On a
        # heuristic view we compute it (to gate nothing on it) but expose it as None so
        # the UI shows "Unavailable (camera not calibrated)" rather than a fake number.
        pixel_speed = self._release_speed_kmh(work)
        release_speed = round(pixel_speed, 2) if calibrated else None

        prediction: dict[str, Any] = {}
        try:
            if len(world) >= 2:
                prediction = predict_with_physics(world, fps=work.fps or 30.0)
        except Exception:  # a physics failure is a validity signal, not a crash
            prediction = {}

        predicted_path = [
            {"x": float(p[0]), "y": float(p[1]), "z": float(p[2])}
            for p in prediction.get("predicted_path", [])
        ]
        impact = _xyz(prediction.get("impact_point"))
        wicket = None
        if prediction.get("would_hit_stumps"):
            wicket = {"hitting": True, "point": _xyz(prediction.get("stump_intersection"))}
        bounce = self._bounce(fitted)
        model_used = prediction.get("model_used", "none")

        valid, reasons = self._validate(work, pixel_speed, predicted_path, calibrated)
        confidence = self._confidence(work, prediction, self.geometry_source)

        return PredictedTrajectory(
            observed=observed,
            fitted_points=fitted,
            predicted_path=predicted_path,
            bounce=bounce,
            impact=impact,
            wicket=wicket,
            release_speed_kmh=release_speed,
            model_used=model_used,
            geometry_source=self.geometry_source,
            source=self.source,
            valid=valid,
            reasons=reasons,
            confidence=round(confidence, 3),
        )

    # -- helpers --------------------------------------------------------------
    def _to_world(self, observed: ObservedTrajectory) -> list[tuple[float, float, float]]:
        """Crude single-camera image→world guess: x_px→lateral metres, delivery
        progression→downrange, image height→vertical. Good enough to run physics and
        prove the spine; honest about being heuristic via geometry_source."""
        pts = observed.points
        if not pts:
            return []
        ppm = self.pixels_per_meter
        x0 = pts[0].x_px
        y0 = pts[0].y_px
        n = max(1, len(pts) - 1)
        world: list[tuple[float, float, float]] = []
        for i, p in enumerate(pts):
            lateral = (p.x_px - x0) / ppm
            downrange = (i / n) * 20.12
            height = max(0.0, (y0 - p.y_px) / ppm)  # up in world = up in image
            world.append((lateral, downrange, height))
        return world

    def _release_speed_kmh(self, observed: ObservedTrajectory) -> float:
        """Median frame-to-frame pixel speed → km/h, mirroring the pipeline's own speed
        math (displacement / dt / pixels-per-metre). Robust to a few static frames, and
        physically grounded rather than derived from the crude world downrange guess."""
        pts = observed.points
        speeds: list[float] = []
        for a, b in zip(pts, pts[1:]):
            dt = (b.t_ms - a.t_ms) / 1000.0
            if dt <= 0:
                continue
            d_m = math.hypot(b.x_px - a.x_px, b.y_px - a.y_px) / self.pixels_per_meter
            speeds.append((d_m / dt) * 3.6)
        if not speeds:
            return 0.0
        speeds.sort()
        return speeds[len(speeds) // 2]

    def _bounce(self, fitted: list[dict[str, float]]) -> dict[str, float] | None:
        if len(fitted) < 4:
            return None
        idx = min(range(len(fitted)), key=lambda i: fitted[i]["z"])
        if idx in (0, len(fitted) - 1):
            return None
        return fitted[idx]

    def _validate(
        self,
        observed: ObservedTrajectory,
        pixel_speed_kmh: float,
        predicted_path: list[dict[str, float]],
        calibrated: bool,
    ) -> tuple[bool, list[str]]:
        # validity = observation quality  AND  (heuristic geometry OR calibrated physics)
        # Calibration ONLY affects the physics check, never the observation checks.
        reasons = observation_quality(observed)
        if not predicted_path:
            reasons.append("physics prediction produced no path")
        # Physical-speed check ONLY with calibrated geometry. Without a homography,
        # pixel speed is not real velocity (depth motion), so gating on it here would
        # reject good ball tracks — exactly the bug this replaces.
        if calibrated and not (MIN_SPEED_KMH <= pixel_speed_kmh <= MAX_SPEED_KMH):
            reasons.append(f"implausible release speed {pixel_speed_kmh:.1f} km/h")
        return (len(reasons) == 0, reasons)

    def _confidence(self, observed: ObservedTrajectory, prediction: dict[str, Any], geometry_source: str) -> float:
        real = [p.confidence for p in observed.points if p.real]
        track_conf = (sum(real) / len(real)) if real else 0.0
        real_ratio = observed.real_count / max(1, observed.tracked_count)
        pred_conf = float(prediction.get("confidence", 0.0))
        geom_factor = 1.0 if geometry_source == "calibration" else 0.7
        raw = 0.45 * track_conf + 0.25 * real_ratio + 0.30 * pred_conf
        return max(0.0, min(1.0, raw)) * geom_factor


@dataclass(slots=True)
class PitchGeometry:
    """Pitch dimensions (metres) for the calibrated pitch frame: origin at the striker's
    stumps, along-pitch negative toward the bowler."""
    length_m: float = 20.12
    stump_half_width_m: float = 0.1143      # half of 0.2286 m
    stump_height_m: float = 0.711


class CalibratedTrajectoryProducer:
    """Calibrated producer: maps observed image points to PITCH GROUND coordinates through
    a homography (``ManualPitchCalibrator.pixel_to_pitch_mm``), yielding physically real
    ground positions and a genuine horizontal release speed — the upgrade over the
    heuristic flat pixels-per-metre producer.

    LIMIT (single camera): the homography maps the GROUND plane only, so ball HEIGHT / full
    3D flight is not recovered. The bounce/pitching point (on the ground) is accurate,
    airborne positions are a ground-shadow approximation, and the wicket check is LINE-only
    (lateral), never height — that needs physics-constrained 3D or a second camera. Output
    is the same PredictedTrajectory contract, so nothing downstream changes.
    """

    def __init__(self, calibrator, camera_id: int = 0, pitch: "PitchGeometry | None" = None,
                 bounce_px=None, impact_px=None, homography_error_cm=None, source: str = "calibrated") -> None:
        self.calibrator = calibrator
        self.camera_id = camera_id
        self.pitch = pitch or PitchGeometry()
        self.bounce_px = bounce_px
        self.impact_px = impact_px
        self.homography_error_cm = homography_error_cm
        self.source = source
        self.geometry_source = "calibration"

    def predict(self, observed: ObservedTrajectory) -> PredictedTrajectory:
        work = ObservedTrajectory(observed.display_points(), observed.camera_id, observed.fps)
        world: list[dict[str, float]] = []
        timed: list[tuple[dict[str, float], float]] = []
        for p in work.points:
            mm = self.calibrator.pixel_to_pitch_mm(self.camera_id, p.x_px, p.y_px)
            if mm is None:
                continue
            wp = {"x": mm[0] / 1000.0, "y": mm[1] / 1000.0, "z": 0.0}  # z: ground plane; height not recovered
            world.append(wp)
            timed.append((wp, p.t_ms))
        release_speed = self._ground_speed(timed)
        valid, reasons = self._validate(work, world, release_speed)
        confidence = self._confidence(work)
        return PredictedTrajectory(
            observed=observed,
            fitted_points=world,
            predicted_path=[],                 # forward 3D prediction deferred (needs height)
            bounce=self._map_px(self.bounce_px),
            impact=self._map_px(self.impact_px),
            wicket=self._wicket_line(world),
            release_speed_kmh=round(release_speed, 2),
            model_used="calibrated_ground",
            geometry_source="calibration",
            source=self.source,
            valid=valid,
            reasons=reasons,
            confidence=round(confidence, 3),
        )

    def _map_px(self, px) -> dict[str, float] | None:
        if not px:
            return None
        mm = self.calibrator.pixel_to_pitch_mm(self.camera_id, float(px[0]), float(px[1]))
        return None if mm is None else {"x": mm[0] / 1000.0, "y": mm[1] / 1000.0, "z": 0.0}

    def _ground_speed(self, timed: list[tuple[dict[str, float], float]]) -> float:
        """Median frame-to-frame speed over the GROUND plane in real metres → km/h — the
        ball's horizontal speed, a genuine delivery-speed estimate (approximate for
        airborne points due to ground-shadow parallax)."""
        speeds: list[float] = []
        for (a, ta), (b, tb) in zip(timed, timed[1:]):
            dt = (tb - ta) / 1000.0
            if dt <= 0:
                continue
            speeds.append((math.hypot(b["x"] - a["x"], b["y"] - a["y"]) / dt) * 3.6)
        if not speeds:
            return 0.0
        speeds.sort()
        return speeds[len(speeds) // 2]

    def _wicket_line(self, world: list[dict[str, float]]) -> dict[str, Any] | None:
        """LINE-only wicket check: does the path cross the stump line (along=0) within the
        stump width laterally? Height is unknown here, so it's 'in line', never 'hitting'."""
        for a, b in zip(world, world[1:]):
            if (a["y"] <= 0.0 <= b["y"]) or (b["y"] <= 0.0 <= a["y"]):
                span = b["y"] - a["y"]
                r = 0.0 if abs(span) < 1e-9 else (0.0 - a["y"]) / span
                lateral = a["x"] + (b["x"] - a["x"]) * r
                return {"in_line": abs(lateral) <= self.pitch.stump_half_width_m,
                        "lateral_m": round(lateral, 3), "height_known": False}
        return None

    def _validate(self, observed: ObservedTrajectory, world: list[dict[str, float]], speed: float) -> tuple[bool, list[str]]:
        reasons = observation_quality(observed)
        if len(world) < MIN_REAL_DETECTIONS:
            reasons.append(f"only {len(world)} points mapped to the pitch (homography coverage)")
        # Calibrated geometry → speed IS physically meaningful, so it gates validity here.
        if not (MIN_SPEED_KMH <= speed <= MAX_SPEED_KMH):
            reasons.append(f"implausible release speed {speed:.1f} km/h")
        return (len(reasons) == 0, reasons)

    def _confidence(self, observed: ObservedTrajectory) -> float:
        real = [p.confidence for p in observed.points if p.real]
        track_conf = (sum(real) / len(real)) if real else 0.0
        real_ratio = observed.real_count / max(1, observed.tracked_count)
        # Calibration quality: lower homography reprojection error → higher confidence.
        err = self.homography_error_cm
        geom_conf = 1.0 if err is None else max(0.3, 1.0 - min(1.0, err / 10.0))
        return max(0.0, min(1.0, 0.4 * track_conf + 0.2 * real_ratio + 0.4 * geom_conf))


def _reversal_ratio(points: list[ObservedPoint]) -> float:
    """Fraction of consecutive real-motion steps that flip direction by >120°.
    A clean flight is smooth (near 0, plus at most a couple of legitimate reversals at
    bounce/impact); a tracker hopping between objects jitters (near 1)."""
    vecs = []
    for a, b in zip(points, points[1:]):
        dx, dy = b.x_px - a.x_px, b.y_px - a.y_px
        if math.hypot(dx, dy) >= MIN_MOTION_PX:  # ignore noise-direction of static points
            vecs.append((dx, dy))
    if len(vecs) < 2:
        return 0.0
    reversals = 0
    for (ax, ay), (bx, by) in zip(vecs, vecs[1:]):
        cos = (ax * bx + ay * by) / (math.hypot(ax, ay) * math.hypot(bx, by))
        if cos < -0.5:  # angle > 120°
            reversals += 1
    return reversals / (len(vecs) - 1)


def observation_quality(observed: ObservedTrajectory) -> list[str]:
    """Geometry-INDEPENDENT checks on the observation stream: enough real detections, a
    continuous (not mostly gap-filled) track, no huge dropouts, reasonable detector
    confidence, and no jittery direction reversals. Shared by every producer — they do
    NOT judge physical speed (that needs geometry)."""
    pts = observed.points
    if observed.tracked_count < 2:
        return ["fewer than 2 tracked points"]
    reasons: list[str] = []
    if observed.real_count < MIN_REAL_DETECTIONS:
        reasons.append(f"only {observed.real_count} real detections (need {MIN_REAL_DETECTIONS})")
    if any(not math.isfinite(p.x_px) or not math.isfinite(p.y_px) for p in pts):
        reasons.append("NaN or infinite value in tracked points")
    ratio = observed.real_count / max(1, observed.tracked_count)
    if ratio < MIN_REAL_RATIO:
        reasons.append(f"track is mostly gap-fill ({ratio:.0%} real detections)")
    reals = [p for p in pts if p.real]
    if len(reals) >= 2:
        max_gap = max(b.t_ms - a.t_ms for a, b in zip(reals, reals[1:]))
        if max_gap > MAX_GAP_MS:
            reasons.append(f"{max_gap:.0f} ms gap between detections (discontinuous track)")
        confs = sorted(p.confidence for p in reals)
        median_conf = confs[len(confs) // 2]
        if median_conf < MIN_MEDIAN_CONF:
            reasons.append(f"low detector confidence (median {median_conf:.2f})")
        reversal = _reversal_ratio(reals)
        if reversal > MAX_REVERSAL_RATIO:
            reasons.append(f"erratic track ({reversal:.0%} direction reversals)")
    return reasons


def _xyz(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return {"x": float(value.get("x", 0.0)), "y": float(value.get("y", 0.0)), "z": float(value.get("z", 0.0))}
    try:
        x, y, z = value
        return {"x": float(x), "y": float(y), "z": float(z)}
    except (TypeError, ValueError):
        return None


TRIM_CONFIRM_FRAMES = 8   # keep a few frames past impact for confirmation, then stop
# A ball can plausibly be occluded (behind the batter, bat swing) up to MAX_GAP_MS and
# still be the same delivery — that is exactly the validity gate's dropout limit, so the
# termination policy and the gate agree on what "continuous" means. Beyond
# UNRECOVERABLE_GAP_MS no single delivery spans it, so we terminate regardless of where
# the ball reappears (it is a throw / the next ball / a fielder).
UNRECOVERABLE_GAP_MS = 1200.0


class TerminationReason(Enum):
    """WHY an observation stopped being the current delivery. The event is never "a gap"
    per se — it is "these points can no longer plausibly be the same delivery". A long
    unrecoverable tracking gap is today's signal for LEFT_DELIVERY; reversals, a ball
    reappearing far away, a size/class collapse, or a second delivery can be folded into
    the same state later WITHOUT the renderer, physics, validity gate, or exports changing.
    Values are the human-facing end_reason strings (some are asserted by tests / the UI)."""
    TOO_FEW = "Too few points"
    IMPACT_CONFIRMED = "Impact confirmed"
    LEFT_DELIVERY = "Left delivery (tracking gap)"
    DETECTIONS_LOST = "Detections lost"
    CLIP_END = "Reached clip end (no impact)"
    TRACK_ENDED = "Track ended"


@dataclass(slots=True)
class Termination:
    """Where the delivery observation ends (display cutoff frame; None = keep all) and the
    STATE that ended it. Non-destructive: the full track stays on observed.points."""
    end_frame: int | None
    reason: TerminationReason


def _continuity_break_frame(observed: ObservedTrajectory) -> int | None:
    """Frame of the last in-delivery detection when the observation stops being ONE
    continuous delivery — else None. Today's only signal is a tracking gap the ball does
    not plausibly reconnect across: a gap longer than MAX_GAP_MS where the ball reappears
    far from where its pre-gap motion predicts (or a gap so long it can't be the same
    delivery at all). A brief occlusion the ball emerges from on-trajectory (e.g. a few
    frames behind the batter, common at high fps) is deliberately NOT a break."""
    reals = [p for p in observed.points if p.real]
    for i in range(1, len(reals)):
        a, b = reals[i - 1], reals[i]
        gap_ms = b.t_ms - a.t_ms
        if gap_ms <= MAX_GAP_MS:
            continue                                    # within the continuous-delivery window
        if gap_ms >= UNRECOVERABLE_GAP_MS:
            return a.frame_id                           # too long to be the same delivery
        # Ambiguous gap: keep it only if the ball reappears near where its motion predicts.
        if i >= 2:
            prev = reals[i - 2]
            step = (a.frame_id - prev.frame_id) or 1
            vx, vy = (a.x_px - prev.x_px) / step, (a.y_px - prev.y_px) / step
            span = b.frame_id - a.frame_id
            pred_x, pred_y = a.x_px + vx * span, a.y_px + vy * span
            drift = math.hypot(b.x_px - pred_x, b.y_px - pred_y)
            expected = math.hypot(vx * span, vy * span)
            if drift <= max(80.0, 1.5 * expected):
                continue                                # reconnected — same delivery
        return a.frame_id                               # reappeared implausibly far — new observation
    return None


def determine_termination(
    observed: ObservedTrajectory,
    impact_frame: int | None,
    last_frame: int | None,
    confirm: int = TRIM_CONFIRM_FRAMES,
) -> Termination:
    """The ONE place that decides where an observation stops belonging to the delivery,
    as a named state — never dropping data. Consumers (renderer, physics fit, validity
    gate, exports) read the display window this sets; they don't re-derive termination.
    The ladder is explicit so new termination signals slot in without touching them.

    Order: impact wins when confirmed; else a broken-continuity gap; else a trailing run
    of Kalman gap-fills; else the clip simply ran out."""
    pts = observed.points
    if len(pts) < 2:
        return Termination(None, TerminationReason.TOO_FEW)
    # 1) Impact confirmed: the ball reached the (heuristic) pad region.
    if impact_frame is not None and any(p.frame_id > impact_frame + confirm for p in pts):
        return Termination(impact_frame + confirm, TerminationReason.IMPACT_CONFIRMED)
    # 2) Left delivery: the track can no longer plausibly be the same delivery (gap today).
    break_frame = _continuity_break_frame(observed)
    if break_frame is not None:
        return Termination(break_frame, TerminationReason.LEFT_DELIVERY)
    # 3) Detections lost: a trailing run of Kalman gap-fills — the ball was lost at the end.
    end = len(pts)
    while end > 2 and not pts[end - 1].real:
        end -= 1
    if end < len(pts):
        return Termination(pts[end - 1].frame_id, TerminationReason.DETECTIONS_LOST)
    # 4) No end signal: the track ran to the clip's end and never terminated on its own.
    last = pts[-1].frame_id
    if last_frame is not None and last >= last_frame - 3:
        return Termination(None, TerminationReason.CLIP_END)
    return Termination(None, TerminationReason.TRACK_ENDED)


def _trajectory_quality(points: list[ObservedPoint], longest_gap: int) -> dict[str, Any]:
    """0–1 quality score (and 0–5 stars) from continuity, gaps, confidence, and length —
    the first thing an engineer should read when comparing models/clips."""
    if len(points) < 2:
        return {"score": 0.0, "stars": 0}
    reals = [p for p in points if p.real]
    real_ratio = len(reals) / len(points)
    mean_conf = (sum(p.confidence for p in reals) / len(reals)) if reals else 0.0
    gap_ok = max(0.0, 1.0 - longest_gap / 10.0)
    length_ok = min(1.0, len(reals) / 30.0)
    score = 0.30 * real_ratio + 0.30 * mean_conf + 0.20 * gap_ok + 0.20 * length_ok
    score = max(0.0, min(1.0, score))
    return {"score": round(score, 2), "stars": round(score * 5)}


def _observation_summary(observed: ObservedTrajectory) -> dict[str, Any]:
    """The trajectory-debugger line: where the usable track starts/ends, how clean it
    is, how much was dropped from display, and the STATE that ended it."""
    full = observed.points
    disp = observed.display_points()
    reals = [p for p in disp if p.real]
    confs = [p.confidence for p in reals]
    gaps = [b.frame_id - a.frame_id for a, b in zip(reals, reals[1:])]
    longest_gap = max(gaps) if gaps else 0
    quality = _trajectory_quality(disp, longest_gap)
    end_frame = observed.display_end_frame
    if end_frame is None and disp:
        end_frame = disp[-1].frame_id
    return {
        "start_frame": disp[0].frame_id if disp else None,
        "end_frame": end_frame,
        "length_frames": (disp[-1].frame_id - disp[0].frame_id) if disp else 0,
        "tracked_points": len(full),          # complete track (kept, not destroyed)
        "displayed_points": len(disp),        # what the renderer shows
        "dropped_points": len(full) - len(disp),
        "real_detections": len(reals),
        "mean_confidence": round(sum(confs) / len(confs), 2) if confs else 0.0,
        "longest_gap_frames": longest_gap,
        "end_reason": observed.end_reason,
        "quality": quality["score"],
        "quality_stars": quality["stars"],
    }


def _build_diagnostics(
    observed: ObservedTrajectory,
    predicted: PredictedTrajectory,
    geometry_source: str,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-stage pipeline status so a failure names exactly where it broke, instead of
    the whole thing collapsing to a single 'fallback' flag."""
    calibrated = geometry_source == "calibration"
    stages = [
        {"key": "detection", "label": "Ball detected", "ok": observed.real_count > 0,
         "detail": f"{observed.real_count} real detections"},
        {"key": "tracking", "label": "Tracker built", "ok": observed.tracked_count >= 2,
         "detail": f"{observed.tracked_count} tracked points"},
        {"key": "fit", "label": "Trajectory fit", "ok": observed.real_count >= MIN_REAL_DETECTIONS,
         "detail": f"needs {MIN_REAL_DETECTIONS} real detections"},
        {"key": "prediction", "label": "Prediction built", "ok": bool(predicted.predicted_path),
         "detail": predicted.model_used},
        {"key": "calibration", "label": "Calibration", "ok": calibrated,
         "detail": geometry_source if calibrated else "missing — measurements approximate"},
        {"key": "animation", "label": "Animation source", "ok": predicted.valid,
         "detail": "real trajectory" if predicted.valid else "fallback — " + "; ".join(predicted.reasons)},
    ]
    # Which physical measurements are trustworthy depends entirely on calibration.
    # Surface that explicitly instead of printing a fabricated number.
    measurements = {
        "speed": (f"{predicted.release_speed_kmh} km/h"
                  if calibrated and predicted.release_speed_kmh is not None
                  else "unavailable (camera not calibrated)"),
        "bounce": "calibrated" if calibrated else "approximate",
        "prediction": "calibrated" if calibrated else "heuristic",
    }
    return {
        "stages": stages,
        "measurements": measurements,
        "observation": summary or {},
        "calibrated": calibrated,
        # Which pipeline produced this trajectory — persisted so an old export self-declares
        # whether its world coordinates came from a calibrated homography or the heuristic.
        "producer": {
            "name": predicted.source,
            "calibrated": calibrated,
            "label": "Calibrated" if calibrated else "Heuristic (no profile)",
        },
        "overall_confidence": predicted.confidence,
        "valid": predicted.valid,
    }


def build_review_result(
    job_id: str,
    observed: ObservedTrajectory,
    decision: dict[str, Any],
    geometry_source: str,
    producer: TrajectoryProducer | None = None,
    pixels_per_meter: float = 60.0,
    impact_frame: int | None = None,
    last_frame: int | None = None,
) -> ReviewResult:
    """Single factory every path funnels through, so the live/websocket paths cannot
    invent a fourth trajectory representation later — they call this too.

    Sets a DISPLAY policy (display_end_frame + end_reason) on the observation without
    dropping any data — the full track is preserved on observed.points for debugging,
    while renderer and physics fit read observed.display_points() (release→impact)."""
    termination = determine_termination(observed, impact_frame, last_frame)
    observed.display_end_frame, observed.end_reason = termination.end_frame, termination.reason.value
    if producer is None:
        producer = PhysicsTrajectoryProducer(pixels_per_meter, geometry_source)
    predicted = producer.predict(observed)
    summary = _observation_summary(observed)
    diagnostics = _build_diagnostics(observed, predicted, geometry_source, summary)
    return ReviewResult(
        job_id=job_id,
        trajectory=predicted,
        decision=decision,
        geometry_source=geometry_source,
        confidence=predicted.confidence,
        diagnostics=diagnostics,
    )
