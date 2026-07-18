"""Projectile trajectory prediction for cricket DRS."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import yaml
from scipy.integrate import solve_ivp

from config.settings import BOUNCE_RESTITUTION, GRAVITY_MPS2
from core.ball_tracker import TrackPoint

# ---------------------------------------------------------------------------
# Optional config overlay (added for the live-pipeline milestone).
# config/drs_config.yaml is a tunables file; if it is missing, the helper
# below falls back to the hardcoded physics constants in config/settings.py
# so the existing 95-test suite is unaffected.
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "drs_config.yaml"

_PHYSICS_DEFAULTS = {
    "gravity": 9.81,
    "air_density": 1.225,
    "drag_coefficient": 0.47,
    "ball_mass_kg": 0.156,
    "ball_diameter_m": 0.072,
    "magnus_coefficient": 0.000041,
}

_PITCH_DEFAULTS = {
    "stump_height_m": 0.711,
    "length_m": 20.12,
    "stump_width_m": 0.2286,
}


@lru_cache(maxsize=1)
def _load_physics_config() -> dict:
    """Load physics + pitch sections from config/drs_config.yaml.

    Falls back to config.settings.py constants if the YAML is missing.
    Cached after first call.
    """
    if not _CONFIG_PATH.exists():
        return {"physics": _PHYSICS_DEFAULTS, "pitch": _PITCH_DEFAULTS}
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    physics = {**_PHYSICS_DEFAULTS, **(data.get("physics") or {})}
    pitch = {**_PITCH_DEFAULTS, **(data.get("pitch") or {})}
    return {"physics": physics, "pitch": pitch}


@dataclass(slots=True)
class TrajectoryPoint:
    t: float
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float


@dataclass(slots=True)
class TrajectoryPrediction:
    points: list[TrajectoryPoint]
    bounce_index: int | None
    wicket_collision: bool
    wicket_point: TrajectoryPoint | None

    def to_dict(self) -> dict:
        return {
            "points": [asdict(point) for point in self.points],
            "bounce_index": self.bounce_index,
            "wicket_collision": self.wicket_collision,
            "wicket_point": asdict(self.wicket_point) if self.wicket_point else None,
        }


class TrajectoryPredictor:
    """Predicts future ball path from tracked world coordinates."""

    def __init__(self, restitution: float = BOUNCE_RESTITUTION) -> None:
        self.restitution = restitution

    def predict_from_world_points(
        self,
        positions_m: list[tuple[float, float, float]],
        timestamps_s: list[float],
        horizon_s: float = 1.2,
        dt: float = 0.01,
        wicket_x_m: float | None = None,
        stump_half_width_m: float = 0.1143,
        stump_height_m: float = 0.711,
    ) -> TrajectoryPrediction:
        if len(positions_m) < 2:
            raise ValueError("At least two tracked positions are required")

        p0 = np.asarray(positions_m[-1], dtype=float)
        p1 = np.asarray(positions_m[-2], dtype=float)
        delta_t = max(1e-3, timestamps_s[-1] - timestamps_s[-2])
        velocity = (p0 - p1) / delta_t
        state0 = np.r_[p0, velocity]

        def ode(_t: float, state: np.ndarray) -> np.ndarray:
            return np.array([state[3], state[4], state[5], 0.0, 0.0, -GRAVITY_MPS2])

        t_eval = np.arange(0.0, horizon_s + dt, dt)
        solution = solve_ivp(ode, (0.0, horizon_s), state0, t_eval=t_eval, max_step=dt)

        points: list[TrajectoryPoint] = []
        bounce_index = None
        bounced = False
        for idx, state in enumerate(solution.y.T):
            x, y, z, vx, vy, vz = state
            if z <= 0.0 and not bounced and idx > 0:
                bounce_index = idx
                bounced = True
                vz = abs(vz) * self.restitution
                z = 0.0
            points.append(TrajectoryPoint(float(solution.t[idx]), float(x), float(y), max(0.0, float(z)), float(vx), float(vy), float(vz)))

        wicket_point = self._find_wicket_collision(points, wicket_x_m, stump_half_width_m, stump_height_m)
        return TrajectoryPrediction(points, bounce_index, wicket_point is not None, wicket_point)

    def approximate_world_from_track(
        self,
        points: list[TrackPoint],
        pixels_per_meter: float,
    ) -> tuple[list[tuple[float, float, float]], list[float]]:
        positions = [(point.x / pixels_per_meter, point.y / pixels_per_meter, 0.12) for point in points]
        t0 = points[0].timestamp_ms / 1000.0
        times = [(point.timestamp_ms / 1000.0) - t0 for point in points]
        return positions, times

    def overlay(self, frame: np.ndarray, image_points: list[tuple[int, int]]) -> np.ndarray:
        if len(image_points) >= 2:
            cv2.polylines(frame, [np.asarray(image_points, dtype=np.int32)], False, (255, 60, 40), 2, cv2.LINE_AA)
        return frame

    def _find_wicket_collision(
        self,
        points: list[TrajectoryPoint],
        wicket_x_m: float | None,
        stump_half_width_m: float,
        stump_height_m: float,
    ) -> TrajectoryPoint | None:
        if wicket_x_m is None:
            return None
        for previous, current in zip(points, points[1:]):
            prev_delta = previous.x - wicket_x_m
            curr_delta = current.x - wicket_x_m
            crossed_plane = prev_delta == 0.0 or curr_delta == 0.0 or (prev_delta < 0 < curr_delta) or (curr_delta < 0 < prev_delta)
            if not crossed_plane:
                continue

            span = current.x - previous.x
            ratio = 0.0 if abs(span) < 1e-9 else (wicket_x_m - previous.x) / span
            ratio = max(0.0, min(1.0, ratio))
            y = previous.y + ((current.y - previous.y) * ratio)
            z = previous.z + ((current.z - previous.z) * ratio)
            if abs(y) <= stump_half_width_m and 0.0 <= z <= stump_height_m:
                return TrajectoryPoint(
                    t=previous.t + ((current.t - previous.t) * ratio),
                    x=wicket_x_m,
                    y=y,
                    z=z,
                    vx=previous.vx + ((current.vx - previous.vx) * ratio),
                    vy=previous.vy + ((current.vy - previous.vy) * ratio),
                    vz=previous.vz + ((current.vz - previous.vz) * ratio),
                )

        nearest = min(points, key=lambda point: abs(point.x - wicket_x_m), default=None)
        if nearest and abs(nearest.x - wicket_x_m) <= 0.03 and abs(nearest.y) <= stump_half_width_m and 0.0 <= nearest.z <= stump_height_m:
            return nearest
        return None


# ---------------------------------------------------------------------------
# predict_with_physics
# ---------------------------------------------------------------------------
# Drag- and Magnus-augmented trajectory predictor. Lives alongside the
# existing TrajectoryPredictor (gravity-only + bounce restitution) so older
# code paths are unaffected. The live pipeline calls this when it has at
# least 4 tracked 3D positions and wants a more accurate end-state.
# ---------------------------------------------------------------------------


def predict_with_physics(
    positions: list[tuple[float, float, float]],
    fps: float = 30.0,
    steps: int = 120,
) -> dict:
    """Physics-based trajectory prediction using drag and Magnus effect.

    Args:
        positions: (x, y, z) world coordinates in meters, ordered from
                   release to most recent.
        fps: camera frames per second.
        steps: how many future frames to predict.

    Returns dict with keys:
        predicted_path: list of (x, y, z) tuples
        impact_point: (x, y, z) where the ball would cross the pad plane,
                      or None
        would_hit_stumps: bool
        stump_intersection: (x, y, z) or None
        model_used: "magnus" | "drag_only" | "gravity_only"
        confidence: float 0.0-1.0
    """
    cfg = _load_physics_config()
    p = cfg.get("physics", _PHYSICS_DEFAULTS)

    rho = p.get("air_density", 1.2)
    Cd = p.get("drag_coefficient", 0.47)
    m = p.get("ball_mass_kg", 0.156)
    r = p.get("ball_diameter_m", 0.072) / 2.0
    S = p.get("magnus_coefficient", 0.000041)
    g = p.get("gravity", 9.81)
    A = 3.14159 * r * r

    pitch_cfg = cfg.get("pitch", _PITCH_DEFAULTS)
    stump_h = pitch_cfg.get("stump_height_m", 0.711)
    stump_y = pitch_cfg.get("length_m", 20.12)
    stump_x_range = pitch_cfg.get("stump_width_m", 0.2286) / 2.0

    dt = 1.0 / max(1.0, float(fps))

    empty = {
        "predicted_path": [],
        "impact_point": None,
        "would_hit_stumps": False,
        "stump_intersection": None,
        "model_used": "gravity_only",
        "confidence": 0.1,
    }
    if len(positions) < 2:
        return empty

    pos = tuple(float(v) for v in positions[-1])
    prev = tuple(float(v) for v in positions[-2])
    vx = (pos[0] - prev[0]) * float(fps)
    vy = (pos[1] - prev[1]) * float(fps)
    vz = (pos[2] - prev[2]) * float(fps)

    # Estimate spin from lateral deviation over the last 4 positions.
    omega = [0.0, 0.0, 0.0]
    model_used = "drag_only"
    confidence = 0.6

    if len(positions) >= 4:
        try:
            p0 = positions[-4]
            p3 = positions[-1]
            straight = [
                (
                    p0[0] + i * (p3[0] - p0[0]) / 3.0,
                    p0[1] + i * (p3[1] - p0[1]) / 3.0,
                    p0[2] + i * (p3[2] - p0[2]) / 3.0,
                )
                for i in range(4)
            ]
            x_devs = [positions[-4 + i][0] - straight[i][0] for i in range(4)]
            avg_x_dev = sum(x_devs) / 4.0
            omega[2] = avg_x_dev * 50.0
            model_used = "magnus"
            confidence = 0.75
        except Exception:
            pass

    state = [pos[0], pos[1], pos[2], vx, vy, vz]

    def derivatives(s):
        px_, py_, pz_, vx_, vy_, vz_ = s
        speed = (vx_ * vx_ + vy_ * vy_ + vz_ * vz_) ** 0.5
        drag_scale = -0.5 * rho * Cd * A * speed / m
        ax = drag_scale * vx_
        ay = drag_scale * vy_
        az = drag_scale * vz_ - g
        ox, oy, oz = omega
        # Magnus force per unit mass: F = (S / m) * (omega x v)
        mx = (oy * vz_ - oz * vy_) * S / m
        my = (oz * vx_ - ox * vz_) * S / m
        mz = (ox * vy_ - oy * vx_) * S / m
        return [vx_, vy_, vz_, ax + mx, ay + my, az + mz]

    def rk4_step(s, h):
        k1 = derivatives(s)
        k2 = derivatives([s[i] + 0.5 * h * k1[i] for i in range(6)])
        k3 = derivatives([s[i] + 0.5 * h * k2[i] for i in range(6)])
        k4 = derivatives([s[i] + h * k3[i] for i in range(6)])
        return [s[i] + h * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) / 6.0 for i in range(6)]

    predicted_path: list[tuple[float, float, float]] = []
    impact_point: tuple[float, float, float] | None = None
    stump_intersection: tuple[float, float, float] | None = None
    would_hit_stumps = False
    PAD_HEIGHT = 0.9  # metres — typical pad impact check

    for _ in range(max(1, int(steps))):
        state = rk4_step(state, dt)
        pt = (state[0], state[1], state[2])
        predicted_path.append(pt)

        if stump_intersection is None and abs(state[1] - stump_y) < 0.1:
            if abs(state[0]) <= stump_x_range and 0.0 <= state[2] <= stump_h:
                stump_intersection = pt
                would_hit_stumps = True

        if impact_point is None and state[2] <= PAD_HEIGHT and state[1] > 18.0:
            impact_point = pt

        if state[2] < 0.0:
            break

    return {
        "predicted_path": predicted_path,
        "impact_point": impact_point,
        "would_hit_stumps": would_hit_stumps,
        "stump_intersection": stump_intersection,
        "model_used": model_used,
        "confidence": confidence,
    }
