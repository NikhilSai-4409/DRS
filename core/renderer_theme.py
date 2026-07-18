"""RendererTheme — colours/sizes/glow for the overlay, so the renderer logic never
hardcodes look. Swap themes (Broadcast / Dark / ICC / IPL) without touching drawing.

Colours are BGR (OpenCV). The live dashboard's JS renderer mirrors these fields.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RendererTheme:
    name: str = "broadcast"
    # trajectory (three phases: measured / transition / predicted)
    measured_color: tuple = (240, 240, 240)     # bright white tracked spheres
    transition_color: tuple = (0, 165, 255)     # orange glow around bounce/impact
    predicted_color: tuple = (175, 120, 195)    # grey-purple predicted spheres
    ribbon_color: tuple = (150, 80, 140)        # translucent ground ribbon
    bounce_color: tuple = (0, 165, 255)         # orange
    impact_color: tuple = (60, 60, 235)         # red
    stump_hit_color: tuple = (60, 60, 235)
    stump_idle_color: tuple = (200, 200, 200)
    # sizing / intensity
    sphere_min_r: int = 4
    sphere_max_r: int = 12
    glow_strength: float = 0.30
    ribbon_alpha: float = 0.20
    predicted_alpha: float = 0.55
    # chrome
    banner_bg: tuple = (24, 24, 24)
    card_bg: tuple = (20, 20, 20)
    card_status: tuple = ((60, 60, 235), (90, 200, 90), (170, 170, 170))  # out / not-out / info


BROADCAST_THEME = RendererTheme()
DARK_THEME = RendererTheme(
    name="dark", measured_color=(230, 230, 230), predicted_color=(150, 120, 150),
    ribbon_color=(120, 90, 60), banner_bg=(12, 12, 12), card_bg=(10, 10, 10), glow_strength=0.22,
)
ICC_THEME = RendererTheme(
    name="icc", measured_color=(255, 255, 255), predicted_color=(200, 160, 120),
    bounce_color=(0, 140, 255), impact_color=(40, 40, 220), ribbon_color=(150, 110, 80),
)
IPL_THEME = RendererTheme(
    name="ipl", measured_color=(255, 255, 255), predicted_color=(200, 120, 60),
    ribbon_color=(180, 60, 140), bounce_color=(0, 200, 255), impact_color=(40, 40, 235), glow_strength=0.38,
)

# Per-review colour identity — the operator recognises the review by colour, no text.
# (Colours are BGR.)
LBW_THEME = RendererTheme(
    name="lbw", measured_color=(240, 240, 240), predicted_color=(235, 170, 70),   # white track, blue prediction
    transition_color=(0, 165, 255), ribbon_color=(150, 110, 60), bounce_color=(0, 165, 255), impact_color=(60, 60, 235),
)
WIDE_THEME = RendererTheme(
    name="wide", measured_color=(245, 245, 245), predicted_color=(205, 120, 190),   # purple
    transition_color=(205, 120, 190), ribbon_color=(160, 90, 175), bounce_color=(205, 120, 190), impact_color=(205, 120, 190),
)
NOBALL_THEME = RendererTheme(
    name="noball", measured_color=(245, 245, 245), predicted_color=(70, 70, 235),   # red
    transition_color=(60, 60, 235), ribbon_color=(60, 60, 175), bounce_color=(70, 70, 235), impact_color=(50, 50, 230),
)
RUNOUT_THEME = RendererTheme(
    name="runout", measured_color=(245, 245, 245), predicted_color=(90, 200, 90),   # green + amber
    transition_color=(0, 200, 255), ribbon_color=(80, 150, 90), bounce_color=(0, 200, 255),
    impact_color=(0, 200, 255), stump_hit_color=(0, 200, 255),
)
STUMPING_THEME = RendererTheme(
    name="stumping", measured_color=(245, 245, 245), predicted_color=(235, 220, 70),   # cyan + gold
    transition_color=(0, 200, 255), ribbon_color=(150, 170, 60), bounce_color=(0, 200, 255),
    impact_color=(0, 200, 255), stump_hit_color=(0, 200, 255),
)
EDGE_THEME = RendererTheme(
    name="edge", measured_color=(245, 245, 245), predicted_color=(200, 160, 90),
    transition_color=(0, 200, 255), ribbon_color=(150, 120, 70), impact_color=(40, 40, 220),
)

THEMES = {theme.name: theme for theme in (
    BROADCAST_THEME, DARK_THEME, ICC_THEME, IPL_THEME,
    LBW_THEME, WIDE_THEME, NOBALL_THEME, RUNOUT_THEME, STUMPING_THEME, EDGE_THEME,
)}

_THEME_BY_TYPE = {
    "lbw": "lbw", "wide": "wide", "noball": "noball", "no_ball": "noball",
    "front_foot": "noball", "frontfoot": "noball", "runout": "runout", "run_out": "runout",
    "stumping": "stumping", "edge": "edge", "ultraedge": "edge", "ultra_edge": "edge", "snicko": "edge",
}


def get_theme(name: str | None) -> RendererTheme:
    return THEMES.get((name or "broadcast").lower(), BROADCAST_THEME)


def theme_for(review_type: str | None) -> RendererTheme:
    """The colour identity for a review type (LBW blue, Wide purple, Run Out green…)."""
    return THEMES.get(_THEME_BY_TYPE.get(str(review_type or "").lower(), "broadcast"), BROADCAST_THEME)
