"""Self-validation for the overlay payload contract.

The contract is only worth having if violations are caught at the boundary rather
than surfacing as a wrong overlay in front of an umpire.

**Severity answers one question: could this mislead an umpire?**

* ``WARNING`` — optional metadata missing. Render normally.
* ``ERROR``   — the overlay is degraded but not untruthful. Render what is
  possible and surface it prominently.
* ``FATAL``   — this would draw *misleading evidence*. Suppress the affected
  element only, never the whole review.

Nothing here raises in production. A contract violation must not cost the
operator a review: the renderer still draws everything that is safe, the
violation is logged loudly, and the test suite asserts the list is empty.
Failing hard would turn a cosmetic bug into a lost appeal, which is the wrong
trade for a match-day tool.

The checks encode mistakes that have actually happened in this codebase:

* a tri-state written as a bare ``None`` or a legacy ``"dislodged"`` placeholder;
* ``hitting`` carried as a *string*, which is truthy — an "unknown" literal in
  that slot lights the stumps red and asserts the ball hit the wicket;
* a marker placed off the measured path (the transition marker must be derived
  from the path, never stored, or the two drift apart);
* a frame reference with no space or source named — capture-counter indices,
  replay-clip indices, and indices from different cameras are all incomparable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.frame_ref import FrameSpace
from core.observation import BailsState, Observation
from utils.logger import get_logger

logger = get_logger(__name__)

_TRISTATE_FIELDS = ("gloves_detected", "ball_collected", "ball_possession")


class Severity(str, Enum):
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class Violation:
    severity: Severity
    field: str
    message: str
    # Payload keys the renderer must NOT draw because drawing them would mislead.
    suppress: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.field}: {self.message}"


def validate_overlay_payload(payload: dict) -> list[Violation]:
    """Return every contract violation found (empty list = valid)."""
    if not isinstance(payload, dict):
        return [Violation(Severity.FATAL, "payload", "payload is not a dict")]

    problems: list[Violation] = []

    # --- tri-states must be explicit wire literals ---------------------------
    bails = payload.get("bails_status")
    if bails is not None and bails not in {s.value for s in BailsState}:
        # Renderers route an unrecognised value to their "unknown" branch, so the
        # result is safe — degraded, not untruthful.
        problems.append(Violation(
            Severity.ERROR, "bails_status",
            f"{bails!r} is not a BailsState literal"))

    for field in _TRISTATE_FIELDS:
        if field in payload and payload[field] not in {o.value for o in Observation}:
            # A bare False here loses the "we never looked" distinction, which a
            # reader will take as a negative finding.
            problems.append(Violation(
                Severity.ERROR, field,
                f"{payload[field]!r} is not an Observation literal — "
                "'not observed' and 'observed false' must stay distinguishable"))

    # --- `hitting` is a RENDER flag: Optional[bool] only ---------------------
    hitting = payload.get("hitting")
    if hitting is not None and not isinstance(hitting, bool):
        problems.append(Violation(
            Severity.FATAL, "hitting",
            f"must be True/False/None, got {hitting!r} — a string here is truthy "
            "and would assert a wicket strike",
            suppress=("hitting",)))

    if bails == BailsState.NOT_OBSERVED.value and hitting is True:
        problems.append(Violation(
            Severity.FATAL, "hitting",
            "claims a wicket strike while bails_status is not_observed",
            suppress=("hitting",)))

    # --- the transition marker is derived, so it must lie ON the path --------
    path = payload.get("measured_px") or payload.get("ball_path") or []
    marker = payload.get("transition_px")
    if marker is not None:
        if not path:
            problems.append(Violation(
                Severity.FATAL, "transition_px",
                "present with no measured path to derive it from",
                suppress=("transition_px",)))
        elif not _same_point(path[-1], marker):
            problems.append(Violation(
                Severity.FATAL, "transition_px",
                "is not the last measured point — it must be derived, not stored",
                suppress=("transition_px",)))

    problems.extend(_validate_frame(payload.get("frame"), "frame"))
    return problems


def _same_point(a, b, tol: float = 0.5) -> bool:
    ax, ay = (a[0], a[1]) if isinstance(a, (list, tuple)) else (a.get("x"), a.get("y"))
    bx, by = (b[0], b[1]) if isinstance(b, (list, tuple)) else (b.get("x"), b.get("y"))
    if None in (ax, ay, bx, by):
        return False
    return abs(ax - bx) <= tol and abs(ay - by) <= tol


def _validate_frame(frame, where: str) -> list[Violation]:
    if frame is None:
        return []
    if not isinstance(frame, dict):
        return [Violation(Severity.FATAL, where,
                          f"must be an object carrying its space, got {type(frame).__name__}",
                          suppress=(where,))]
    problems: list[Violation] = []
    space = frame.get("space")
    if space not in {s.value for s in FrameSpace}:
        # Without a space the index cannot be placed, so anything drawn from it
        # may land on the wrong moment.
        problems.append(Violation(
            Severity.FATAL, f"{where}.space",
            f"{space!r} must be one of {[s.value for s in FrameSpace]}",
            suppress=(where,)))
    if frame.get("index") is None:
        problems.append(Violation(
            Severity.FATAL, f"{where}.index", "is required", suppress=(where,)))
    if space == FrameSpace.CAPTURE.value and frame.get("timestamp_ms") is None:
        # Capture indices are per-camera; timestamp is the only cross-surface key.
        problems.append(Violation(
            Severity.ERROR, f"{where}.timestamp_ms",
            "is required for capture-space frames — it is the only key that can "
            "join a camera frame to a replay clip or a waveform"))
    if space == FrameSpace.CAPTURE.value and not frame.get("source"):
        problems.append(Violation(
            Severity.WARNING, f"{where}.source",
            "unset — capture indices from different cameras are not interchangeable"))
    return problems


def suppressed_keys(violations: list[Violation]) -> set[str]:
    """Payload keys a renderer must skip. FATAL suppresses ONLY its own element —
    the rest of the review still renders."""
    keys: set[str] = set()
    for violation in violations:
        if violation.severity is Severity.FATAL:
            keys.update(violation.suppress)
    return keys


def check_overlay_payload(payload: dict, context: str = "") -> list[Violation]:
    """Validate and log by severity. Returns the violations so callers can decide
    what to suppress and tests can assert on them."""
    problems = validate_overlay_payload(payload)
    tag = f" [{context}]" if context else ""
    for violation in problems:
        log = logger.error if violation.severity is not Severity.WARNING else logger.warning
        log("Overlay contract %s%s: %s", violation.severity.value, tag,
            f"{violation.field}: {violation.message}")
    return problems
