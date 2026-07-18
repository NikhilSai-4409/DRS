"""Canonical DRS camera roles.

One shared vocabulary for the whole system: the calibration role-assignment UI,
each review module's ``required_role``, and the operator dashboard's camera strip
all speak these eight roles. A review module picks its camera *by capability*
(role), never by index, so adding cameras or review types never renumbers anything.

Older labels ("Wide Camera", "front_foot", "Edge", …) are mapped to the canonical
role by :func:`canonical_role`, so historical calibration profiles and appeals keep
matching after the vocabulary was tightened.
"""

from __future__ import annotations

# Canonical roles -------------------------------------------------------------
BALL_TRACKING = "Ball Tracking"
WIDE = "Wide"
FRONT_FOOT = "Front Foot"
ULTRA_EDGE = "UltraEdge"
STUMP = "Stump"
BROADCAST = "Broadcast"
REPLAY = "Replay"
RESERVE = "Reserve"

# Ordered tuple — the order the role picker / camera strip presents them.
CAMERA_ROLES: tuple[str, ...] = (
    BALL_TRACKING,
    WIDE,
    FRONT_FOOT,
    ULTRA_EDGE,
    STUMP,
    BROADCAST,
    REPLAY,
    RESERVE,
)

# Icons the operator UI scans instead of reading text (faster to recognise).
ROLE_ICONS: dict[str, str] = {
    BALL_TRACKING: "🎯",
    WIDE: "📏",
    FRONT_FOOT: "🦶",
    ULTRA_EDGE: "🥎",
    STUMP: "📍",
    BROADCAST: "🎥",
    REPLAY: "📹",
    RESERVE: "➕",
}

# Lower-cased legacy / shorthand labels -> canonical role.
ROLE_ALIASES: dict[str, str] = {
    "ball tracking": BALL_TRACKING,
    "ball_tracking": BALL_TRACKING,
    "tracking": BALL_TRACKING,
    "ball": BALL_TRACKING,
    "wide": WIDE,
    "wide camera": WIDE,
    "wide_camera": WIDE,
    "front foot": FRONT_FOOT,
    "front_foot": FRONT_FOOT,
    "frontfoot": FRONT_FOOT,
    "no ball": FRONT_FOOT,
    "no_ball": FRONT_FOOT,
    "noball": FRONT_FOOT,
    "ultraedge": ULTRA_EDGE,
    "ultra edge": ULTRA_EDGE,
    "ultra_edge": ULTRA_EDGE,
    "edge": ULTRA_EDGE,
    "snicko": ULTRA_EDGE,
    "stump": STUMP,
    "stumps": STUMP,
    "stump camera": STUMP,
    "broadcast": BROADCAST,
    "tv": BROADCAST,
    "main": BROADCAST,
    "replay": REPLAY,
    "slow motion": REPLAY,
    "reserve": RESERVE,
    "spare": RESERVE,
    "unassigned": RESERVE,
}


def canonical_role(value: str | None) -> str | None:
    """Map any role label to its canonical form, or ``None`` if unrecognised."""
    if not value:
        return None
    text = str(value).strip()
    if text in CAMERA_ROLES:
        return text
    return ROLE_ALIASES.get(text.lower())


def role_icon(role: str | None) -> str:
    canonical = canonical_role(role)
    return ROLE_ICONS.get(canonical, "•") if canonical else "•"


def normalize_roles(roles: dict | None) -> dict[int, str]:
    """Coerce a ``{camera_id: role}`` map to ``{int: canonical_role}``.

    Unparseable camera ids and unrecognised roles are dropped, so a review module
    only ever sees clean, canonical assignments.
    """
    result: dict[int, str] = {}
    for key, value in (roles or {}).items():
        try:
            camera_id = int(key)
        except (TypeError, ValueError):
            continue
        canonical = canonical_role(value)
        if canonical is not None:
            result[camera_id] = canonical
    return result


def role_catalog() -> list[dict[str, str]]:
    """Role list (with icons) for the calibration role-assignment UI."""
    return [{"role": role, "icon": ROLE_ICONS[role]} for role in CAMERA_ROLES]
