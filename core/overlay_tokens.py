"""Overlay design tokens — Python side of the Evidence Contract.

Renderers request a **role**, never a colour. ``token("path.measured")["color"]``
rather than ``"#ff4fa3"`` at a draw site. The role carries the meaning, and the
meaning is what must not drift between the animation, the frame stepper, the
exported video and any future TV output.

The canonical values live in ``core/overlay_tokens_v1.json``, which this module
reads and the JavaScript twin mirrors. ``tests/test_overlay_tokens.py`` fails if
the two ever disagree — a shared hex table is worthless if nothing enforces it.

**Versioning.** ``CONTRACT_VERSION`` is stamped into every exported review so an
archived decision can be re-rendered exactly as it was originally presented. A
redesign bumps the version and adds a new file; it never edits v1 in place,
because that would silently change what a stored review looks like.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_TOKENS_PATH = Path(__file__).with_name("overlay_tokens_v1.json")


@lru_cache(maxsize=1)
def _load() -> dict:
    with _TOKENS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


CONTRACT_VERSION: int = _load()["contract_version"]


def tokens() -> dict:
    """The whole role table (read-only by convention)."""
    return _load()["roles"]


def token(role: str) -> dict:
    """One role's values. Raises on an unknown role — a typo'd role name must not
    silently fall back to a default colour."""
    try:
        return _load()["roles"][role]
    except KeyError:
        raise KeyError(
            f"unknown overlay role {role!r}; known roles: {sorted(_load()['roles'])}"
        ) from None


def color(role: str) -> str:
    return token(role)["color"]


def hex_to_bgr(value: str) -> tuple[int, int, int]:
    """OpenCV draws in BGR. Converting here, once, is deliberate: hand-porting hex
    values into BGR tuples is exactly how the frame counter ended up peach in
    Python and pale blue in JavaScript."""
    text = value.lstrip("#")
    r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    return (b, g, r)


def bgr(role: str) -> tuple[int, int, int]:
    return hex_to_bgr(color(role))
