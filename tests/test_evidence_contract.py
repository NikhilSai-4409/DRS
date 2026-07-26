"""Evidence Contract v1 — cross-language parity.

The contract is only real if the JavaScript renderers and the Python pipeline
cannot disagree. These tests read the actual JS sources and compare them with the
Python definitions, so a hand-edit on either side fails CI rather than surfacing
as a wrong overlay in front of an umpire.

This is the check that would have caught the existing BGR/RGB inversion, where
the frame counter is peach in Python and pale blue in JavaScript despite a
comment claiming the two renderers "speak one visual language".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.frame_ref import FrameSpace
from core.observation import BailsState, Observation
from core.overlay_tokens import CONTRACT_VERSION, tokens

ROOT = Path(__file__).resolve().parent.parent
OVERLAY_JS = ROOT / "dashboard" / "electron" / "renderer" / "overlay"
OBSERVATION_JS = OVERLAY_JS / "observation.js"
TOKENS_JS = OVERLAY_JS / "tokens.js"


def _js_object_literals(source: str, name: str) -> dict[str, str]:
    """Pull `KEY: "value"` pairs out of a frozen JS object literal."""
    match = re.search(rf"export const {name} = Object\.freeze\(\{{(.*?)\}}\);", source, re.S)
    assert match, f"{name} not found in the JavaScript twin"
    return dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", match.group(1)))


def test_observation_literals_match_python() -> None:
    js = _js_object_literals(OBSERVATION_JS.read_text(encoding="utf-8"), "Observation")
    assert js == {m.name: m.value for m in Observation}


def test_bails_literals_match_python() -> None:
    js = _js_object_literals(OBSERVATION_JS.read_text(encoding="utf-8"), "BailsState")
    assert js == {m.name: m.value for m in BailsState}


def test_frame_space_literals_match_python() -> None:
    js = _js_object_literals(OBSERVATION_JS.read_text(encoding="utf-8"), "FrameSpace")
    assert js == {m.name: m.value for m in FrameSpace}


def test_unknown_is_a_truthy_string_on_both_sides() -> None:
    """The reason the contract forbids truthiness tests: every literal is truthy,
    so `if (payload.hitting)` is true even when nothing was observed."""
    assert bool(Observation.UNKNOWN.value) is True
    assert bool(BailsState.NOT_OBSERVED.value) is True


def test_token_twin_is_regenerated_from_the_canonical_json() -> None:
    """The JS token file must be exactly what the generator produces. Editing it by
    hand is how a shared palette silently becomes two palettes."""
    from tools.gen_overlay_tokens import render

    assert TOKENS_JS.read_text(encoding="utf-8") == render(), (
        "tokens.js is stale — run `python tools/gen_overlay_tokens.py`"
    )


def test_token_roles_and_version_agree_across_languages() -> None:
    source = TOKENS_JS.read_text(encoding="utf-8")
    version = int(re.search(r"export const CONTRACT_VERSION = (\d+);", source).group(1))
    assert version == CONTRACT_VERSION

    js_roles = set(re.findall(r"^  \"([\w.]+)\": Object\.freeze", source, re.M))
    assert js_roles == set(tokens())


def test_every_role_declares_what_it_means() -> None:
    """A role without a stated meaning is just a colour with extra steps — and a
    renderer author will pick it for how it looks rather than what it asserts."""
    raw = json.loads((ROOT / "core" / "overlay_tokens_v1.json").read_text(encoding="utf-8"))
    missing = [name for name, spec in raw["roles"].items() if not spec.get("means")]
    assert missing == []


def test_measured_and_predicted_are_visually_distinct() -> None:
    """The whole two-tone rule depends on this: if these ever collapse to the same
    hue, an umpire can no longer see where observation stops and inference begins."""
    assert tokens()["path.measured"]["color"] != tokens()["path.predicted"]["color"]


def test_transition_marker_core_reuses_the_measured_role() -> None:
    """Derived, not duplicated — the marker's core colour must follow the measured
    path's colour automatically if that ever changes."""
    assert tokens()["path.transition"]["core_role"] == "path.measured"
