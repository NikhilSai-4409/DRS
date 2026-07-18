"""Modular DRS review engine.

A registry of review modules keyed by review type. The API resolves the active
module for an appeal and calls :func:`run_review`; the frontend simply renders
whatever analysis the active module returns. New review types (Run Out, Stumping,
High Full Toss, Custom) plug in by adding a module here — no API or UI redesign.
"""

from __future__ import annotations

from core.review_modules.base import (
    BallSample,
    ReviewContext,
    ReviewModule,
    ReviewResult,
    build_review_result,
)
from core.review_modules.edge import EdgeReviewModule
from core.review_modules.lbw import LbwReviewModule
from core.review_modules.no_ball import NoBallReviewModule
from core.review_modules.run_out import RunOutReviewModule
from core.review_modules.stumping import StumpingReviewModule
from core.review_modules.wide import WideReviewModule

# Instantiated singletons (stateless analysis).
_MODULES: dict[str, ReviewModule] = {}


def _register(module: ReviewModule, *aliases: str) -> None:
    _MODULES[module.key] = module
    for alias in aliases:
        _MODULES[alias] = module


_register(LbwReviewModule())
_register(WideReviewModule())
_register(NoBallReviewModule(), "no_ball", "front_foot", "frontfoot")
_register(EdgeReviewModule(), "ultraedge", "ultra_edge", "snicko")
_register(RunOutReviewModule(), "run_out")
_register(StumpingReviewModule(), "stump")


def describe_types() -> list[dict]:
    """Capability contracts for every distinct module — served by /api/review-types
    so the dashboard renders whatever each module declares."""
    seen: dict[int, dict] = {}
    for module in _MODULES.values():
        seen.setdefault(id(module), module.describe())
    return sorted(seen.values(), key=lambda item: item["key"])


def get_module(review_type: str) -> ReviewModule | None:
    return _MODULES.get(str(review_type or "").lower())


def supported_types() -> list[str]:
    # Distinct module keys (skip aliases that point at the same instance).
    seen: dict[int, str] = {}
    for key, module in _MODULES.items():
        seen.setdefault(id(module), module.key)
    return sorted(set(seen.values()))


def run_review(review_type: str, ctx: ReviewContext) -> dict | None:
    """Run the module for ``review_type``.

    Returns its type-specific analysis dict, additionally normalised with a
    unified ``review_result`` block so the dashboard renders every review the
    same way. Returns ``None`` when no module handles ``review_type``.
    """
    module = get_module(review_type)
    if module is None:
        return None
    decision = module.analyze(ctx)
    if isinstance(decision, dict):
        decision.setdefault("review_result", build_review_result(review_type, decision))
    return decision


__all__ = [
    "BallSample",
    "ReviewContext",
    "ReviewModule",
    "ReviewResult",
    "LbwReviewModule",
    "WideReviewModule",
    "NoBallReviewModule",
    "EdgeReviewModule",
    "RunOutReviewModule",
    "build_review_result",
    "get_module",
    "run_review",
    "supported_types",
]
