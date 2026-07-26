"""Truthful tri-state values for review evidence.

A DRS check has THREE outcomes, never two:

    observed_true    the system looked and the thing was there
    observed_false   the system looked and the thing was not there
    not_observed     the system could not look at all

Collapsing that into a boolean is how a placeholder ``bails_status = "dislodged"``
ended up painted onto an evidence frame as a red "BAILS DISLODGED" for every
run-out review — asserting to an umpire something no detector had measured. The
failure mode is subtle in the other direction too: ``None`` looks like a safe
"unknown" until a consumer writes ``if analysis.get("gloves_detected")`` and
silently reads it as "no gloves".

So the tri-state is explicit, and every value is a plain string on the wire. The
enums give type safety inside Python; JSON stays human-readable; and the
JavaScript renderers switch on the identical literals (see
``dashboard/electron/renderer/overlay/observation.js``, the twin of this module).

Rule for consumers: never test one of these values for truthiness. Compare to a
member, or ask ``is_known`` first. ``UNKNOWN`` must render as its own visual
state — never as the negative one.
"""

from __future__ import annotations

from enum import Enum


class Observation(str, Enum):
    """A yes/no question the system may or may not have been able to answer."""

    TRUE = "observed_true"
    FALSE = "observed_false"
    UNKNOWN = "not_observed"

    @classmethod
    def of(cls, value: bool | None) -> "Observation":
        """Lift an ``Optional[bool]`` into the tri-state. ``None`` means the check
        did not run — it does NOT mean False."""
        if value is None:
            return cls.UNKNOWN
        return cls.TRUE if value else cls.FALSE

    @classmethod
    def coerce(cls, value) -> "Observation":
        """Accept a member, a wire string, or a legacy ``Optional[bool]``."""
        if isinstance(value, cls):
            return value
        if isinstance(value, bool) or value is None:
            return cls.of(value)
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_known(self) -> bool:
        return self is not Observation.UNKNOWN

    def as_optional_bool(self) -> bool | None:
        """For callers that still need ``Optional[bool]`` — UNKNOWN stays None."""
        if self is Observation.UNKNOWN:
            return None
        return self is Observation.TRUE

    def label(self, yes: str, no: str, unknown: str = "Not observed") -> str:
        """Operator-facing wording. `unknown` deliberately reads as a statement of
        what the camera saw, not as a detector failure ("not detected")."""
        return {Observation.TRUE: yes, Observation.FALSE: no}.get(self, unknown)


class BailsState(str, Enum):
    """Whether the wicket was broken. A domain-named tri-state: the vocabulary an
    umpire uses ("dislodged" / "intact") beats a generic true/false at the point
    of use, while carrying the same three-outcome guarantee."""

    DISLODGED = "dislodged"
    INTACT = "intact"
    NOT_OBSERVED = "not_observed"

    @classmethod
    def coerce(cls, value) -> "BailsState":
        """Legacy payloads used ``None`` for "no detector". That is UNKNOWN, and
        must never be read as INTACT."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.NOT_OBSERVED
        try:
            return cls(str(value).lower())
        except ValueError:
            return cls.NOT_OBSERVED

    @property
    def is_known(self) -> bool:
        return self is not BailsState.NOT_OBSERVED

    @property
    def wicket_broken(self) -> bool | None:
        """None when unobserved — callers must not treat that as "not broken"."""
        if self is BailsState.NOT_OBSERVED:
            return None
        return self is BailsState.DISLODGED

    def label(self) -> str:
        return {
            BailsState.DISLODGED: "Dislodged",
            BailsState.INTACT: "Intact",
        }.get(self, "Not observed")
