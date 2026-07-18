"""Replay reconstruction — the DECISION-SERVICE side of the broadcast replay package.

Turns the canonical observed trajectory (core/review_result.py) into everything the replay
renderers need, so the renderers ONLY render:

  - temporally smoothed image-space track (broadcast-stable, no per-frame jitter)
  - bounce detection (ground touch = global y-max with a confirmed rise after it)
  - stump-anchored lateral mapping -> PITCHING / IMPACT / WICKETS gates + verdict
  - scene-space 3D points (uncalibrated heuristic depth/height, honestly labeled)
  - prediction as a natural rebound arc (never bent toward the stumps)

Modes: "yorker_pad" (sparse post-bounce samples = pad deflection), "normal" (real
post-bounce flight), "full_toss"/"unknown" (no confirmed ground touch — no bounce marker,
PITCHING never overclaimed).

The stump anchor is a per-camera measurement (image x of the stump line + px-per-metre).
Defaults are measured from the fixed 0_MP4 fixture; a calibration profile supersedes them.
Validated against Delivery001/002/006/007 (2026-07-17). This logic is the prototype for the
pipeline's real bounce/gate detectors, replacing the placeholder pad-rectangle heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

OFF_STUMP_M = 0.114                 # middle-of-off-stump lateral offset
ZONE_TOLERANCE_M = 0.036            # half ball width grace on the line call
STUMP_HALF_SPAN_M = 0.18            # outer-edge corridor half width used for HITTING
BAIL_HEIGHT_M = 0.78
Z_RELEASE_M, Z_END_M = 14.0, 1.1    # heuristic depth range (uncalibrated)

TONE = {"IN LINE": "red", "OUTSIDE OFF": "red", "OUTSIDE LEG": "red",
        "HITTING": "red", "MISSING": "red", "FULL TOSS": "grey", "UNKNOWN": "grey"}


@dataclass(slots=True)
class CameraAnchor:
    """Image-space stump anchor for one camera fixture (measured, not detected)."""
    stump_x_px: float = 891.0
    px_per_m_lateral: float = 127.0
    px_per_m_vertical: float = 60.0


def _zone(lateral_m: float) -> str:
    if abs(lateral_m) <= OFF_STUMP_M + ZONE_TOLERANCE_M:
        return "IN LINE"
    return "OUTSIDE OFF" if lateral_m > 0 else "OUTSIDE LEG"


def build_replay_reconstruction(
    trajectory: dict[str, Any],
    decision: dict[str, Any],
    anchor: CameraAnchor | None = None,
) -> dict[str, Any] | None:
    """trajectory = the canonical ReviewResult trajectory dict (observed.points etc.).
    Returns the reconstruction payload, or None when the track is invalid (no replay)."""
    if not trajectory.get("valid"):
        return None
    obs = trajectory.get("observed") or {}
    end_frame = obs.get("display_end_frame")
    pts = sorted(
        (p for p in obs.get("points", []) if end_frame is None or p["frame_id"] <= end_frame),
        key=lambda p: p["frame_id"],
    )
    if len(pts) < 12:
        return None
    a = anchor or CameraAnchor()
    F = np.array([p["frame_id"] for p in pts], float)
    X = np.array([p["x_px"] for p in pts], float)
    Y = np.array([p["y_px"] for p in pts], float)

    # temporal smoothing: low-order polynomial fits over frame index
    Fs = np.linspace(F[0], F[-1], 64)
    Xf = np.polyval(np.polyfit(F, X, 2), Fs)
    Yf = np.polyval(np.polyfit(F, Y, min(4, len(pts) - 2)), Fs)

    ib_raw = int(np.argmax(Y))
    rise_after = float(Y[ib_raw] - Y[-1]) if ib_raw < len(Y) - 1 else 0.0
    post_n = len(Y) - 1 - ib_raw
    bounced = 0 < ib_raw < len(Y) - 1 and rise_after > 30
    mode = ("yorker_pad" if post_n <= 3 else "normal") if bounced else "full_toss"

    lat = (Xf - a.stump_x_px) / a.px_per_m_lateral
    seg = np.sqrt(np.diff(Xf) ** 2 + np.diff(Yf) ** 2)
    s = np.concatenate([[0], np.cumsum(seg)])
    s /= max(s[-1], 1e-6)
    z = Z_RELEASE_M - s * (Z_RELEASE_M - Z_END_M)

    lat_b = None
    if bounced:
        ib_fit = int(np.argmax(Yf))
        yb = float(Yf[ib_fit])
        hgt = np.clip((yb - Yf) / a.px_per_m_vertical, 0.0, None)
        z_b, lat_b = float(z[ib_fit]), float(lat[ib_fit])
        pitching = _zone(lat_b)
        lat_i = float(lat[-1])
        if mode == "yorker_pad":
            h_pad, z_pad = 0.35, max(0.6, z_b - 0.25)
            obs3d = [[round(float(q), 3) for q in row]
                     for row in zip(lat[: ib_fit + 1], hgt[: ib_fit + 1], z[: ib_fit + 1])]
            obs3d.append([round(lat_i, 3), h_pad, round(z_pad, 3)])
            d_pad = max(z_b - z_pad, 1e-6)
            B_ = 0.714
            A_ = (h_pad + B_ * d_pad ** 2) / d_pad
            k_lat = (lat_i - lat_b) / d_pad
        else:
            obs3d = [[round(float(q), 3) for q in row] for row in zip(lat, hgt, z)]
            d_end = max(z_b - float(z[-1]), 1e-6)
            dd = z_b - z[ib_fit:]
            try:
                A_, B_ = np.linalg.lstsq(np.stack([dd, -dd ** 2], 1), hgt[ib_fit:], rcond=None)[0]
                if A_ <= 0:
                    raise ValueError
            except Exception:
                B_ = 0.5
                A_ = (float(hgt[-1]) + B_ * d_end ** 2) / d_end
            k_lat = (lat_i - lat_b) / d_end
        impact = _zone(lat_i)
        pred = []
        for ddp in np.linspace(max(0.3, z_b - Z_END_M) + 0.25, z_b + 0.35, 5):
            pred.append([round(float(lat_b + k_lat * ddp), 3),
                         round(float(max(0.02, A_ * ddp - B_ * ddp * ddp)), 3),
                         round(float(z_b - ddp), 3)])
        L0 = float(lat_b + k_lat * z_b)
        H0 = float(max(0.02, A_ * z_b - B_ * z_b * z_b))
        bounce_index = ib_fit
        bounce_px = [float(X[ib_raw]), float(Y[ib_raw])]
        bounce_frame = float(F[ib_raw])
    else:
        yb = float(np.max(Yf))
        hgt = np.clip((yb - Yf) / a.px_per_m_vertical, 0.05, None)
        obs3d = [[round(float(q), 3) for q in row] for row in zip(lat, hgt, z)]
        lat_i = float(lat[-1])
        impact = _zone(lat_i)
        # no post-ground samples => unknowable; weak rise => ambiguous. Never overclaim.
        pitching = "FULL TOSS" if (rise_after <= 8 and post_n > 0) else "UNKNOWN"
        kL = (lat[-1] - lat[-8]) / max(1e-6, (z[-8] - z[-1]))
        kH = (hgt[-1] - hgt[-8]) / max(1e-6, (z[-8] - z[-1]))
        pred = [[round(float(lat_i + kL * (float(z[-1]) - zp)), 3),
                 round(float(max(0.05, hgt[-1] + kH * (float(z[-1]) - zp))), 3), round(zp, 3)]
                for zp in (0.6, 0.2, -0.35)]
        L0 = float(lat_i + kL * float(z[-1]))
        H0 = float(max(0.05, hgt[-1] + kH * float(z[-1])))
        bounce_index = len(obs3d) - 1
        bounce_px = None
        bounce_frame = None

    wickets = "HITTING" if (abs(L0) < STUMP_HALF_SPAN_M and 0.0 < H0 < BAIL_HEIGHT_M) else "MISSING"
    verdict = "OUT" if (wickets == "HITTING" and impact == "IN LINE" and pitching != "OUTSIDE LEG") else "NOT OUT"

    points = obs3d + pred
    arr = np.array(points)
    cl = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(arr, axis=0), axis=1))])
    cl /= max(cl[-1], 1e-6)
    return {
        "points": points,
        "observed_n": len(obs3d),
        "bounce_index": bounce_index,
        "bounce_frac": round(float(cl[bounce_index]), 4) if bounced else None,
        "bounce_px": bounce_px,
        "bounce_frame": bounce_frame,
        "image_fit": [[round(float(f), 1), round(float(x), 1), round(float(y), 1)]
                      for f, x, y in zip(Fs, Xf, Yf)],
        "impact_px": [float(Xf[-1]), float(Yf[-1])],
        "cards": {
            "original": "NOT OUT",
            "pitching": pitching, "pitching_tone": TONE.get(pitching, "grey"),
            "impact": impact, "impact_tone": TONE.get(impact, "grey"),
            "wickets": wickets, "wickets_tone": TONE.get(wickets, "grey"),
            "decision": verdict,
        },
        "gates": {
            "pitching": pitching, "pitching_lateral_m": (round(lat_b, 3) if lat_b is not None else None),
            "impact": impact, "impact_lateral_m": round(lat_i, 3),
            "wickets": wickets, "stump_plane_lateral_m": round(L0, 3), "stump_plane_height_m": round(H0, 3),
            "verdict": verdict,
        },
        "meta": {
            "mode": mode,
            "tag": "gates from stump-anchored track (uncalibrated) | mode: %s" % mode,
            "anchor": {"stump_x_px": a.stump_x_px, "px_per_m_lateral": a.px_per_m_lateral,
                       "px_per_m_vertical": a.px_per_m_vertical},
            "pipeline_gates": {
                "pitching": decision.get("pitching_status"),
                "impact": decision.get("impact_status"),
                "wickets": decision.get("wicket_status"),
            },
        },
    }
