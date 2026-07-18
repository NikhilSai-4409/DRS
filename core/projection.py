"""Projection engine — 3D pitch coordinates → 2D broadcast-image pixels.

One interface, swappable backends, so ReplayBuilder and the live dashboard consume
the *same* overlay payload and render identically:

    3D ball coords ─► ProjectionModel ─► overlay payload ─► { ReplayBuilder, Dashboard }

Backends (drop-in via :func:`resolve_projection`):
  * HomographyProjection — the 5-marker ground-plane homography we already have.
    Ground points project exactly; the elevated arc is approximated by lifting the
    ground projection with the local vertical scale (Phase 1).
  * PoseProjection — full camera pose (ChArUco: cameraMatrix + rvec/tvec) for
    geometrically-exact 3D projection (Phase 2). Same interface, so nothing
    downstream changes when a camera gains a pose calibration.

Pitch coordinates everywhere: ``lateral_mm`` (signed, from the middle stump),
``along_mm`` (down the pitch, popping crease negative), ``height_mm`` (above ground).
"""

from __future__ import annotations

from typing import Optional, Protocol

from config.settings import STUMP_WIDTH_M
from utils.logger import get_logger

log = get_logger("projection")

# Half the stump span, in mm — off/leg stumps sit at ±this from middle.
STUMP_HALF_WIDTH_MM = (STUMP_WIDTH_M / 2.0) * 1000.0


class ProjectionModel(Protocol):
    """Projects a pitch-world point to broadcast-image pixels."""

    available: bool
    kind: str

    def world_to_pixel(self, lateral_mm: float, along_mm: float, height_mm: float = 0.0) -> Optional[tuple[float, float]]:
        ...


class HomographyProjection:
    """Ground-plane homography + a perspective-aware height lift (Phase 1).

    The homography maps the pitch *ground* plane exactly. A ball at ``height_mm``
    cannot be projected by a plane homography alone, so we lift the ground
    projection upward in the image by the locally-measured vertical scale times a
    height ratio. It is an approximation (documented as such) that gives a
    convincing arc and a geometrically-exact ground shadow; PoseProjection replaces
    it with true 3D when a camera is pose-calibrated.
    """

    kind = "homography"

    def __init__(self, calibrator, camera_id: int, height_lift_ratio: float = 0.7):
        self.calibrator = calibrator
        self.camera_id = camera_id
        self.height_lift_ratio = float(height_lift_ratio)
        self.available = calibrator is not None

    def _ground(self, lateral_mm: float, along_mm: float) -> Optional[tuple[float, float]]:
        return self.calibrator.pitch_mm_to_pixel(self.camera_id, lateral_mm, along_mm)

    def world_to_pixel(self, lateral_mm: float, along_mm: float, height_mm: float = 0.0) -> Optional[tuple[float, float]]:
        ground = self._ground(lateral_mm, along_mm)
        if ground is None:
            return None
        px, py = ground
        if height_mm:
            # Local vertical scale: how many pixels 100 mm of down-pitch distance
            # spans near this point. Nearer the camera → larger scale → more lift,
            # which matches perspective foreshortening.
            near = self._ground(lateral_mm, along_mm + 100.0)
            scale = abs(py - near[1]) / 100.0 if near is not None else 0.05
            py -= height_mm * scale * self.height_lift_ratio
        return px, py


class PoseProjection:
    """Full camera-pose projection (ChArUco intrinsics + extrinsics) — exact 3D.

    Drop-in replacement for HomographyProjection: same ``world_to_pixel`` contract,
    so ReplayBuilder / the dashboard are unchanged. ``pose_projector`` is anything
    exposing ``world_to_pixel(x_m, y_m, z_m)`` (e.g. core.calibration's camera
    calibration). Pitch mm are converted to the pose calibration's metre world
    frame here.
    """

    kind = "pose"

    def __init__(self, pose_projector):
        self.pose_projector = pose_projector
        self.available = pose_projector is not None

    def world_to_pixel(self, lateral_mm: float, along_mm: float, height_mm: float = 0.0) -> Optional[tuple[float, float]]:
        if self.pose_projector is None:
            return None
        try:
            # Pitch frame → pose world frame (metres). Axis mapping mirrors how the
            # ground homography defines pitch coordinates: x = lateral, y = along.
            world_x = (lateral_mm / 1000.0) + (STUMP_WIDTH_M / 2.0)
            world_y = along_mm / 1000.0
            world_z = height_mm / 1000.0
            px, py = self.pose_projector.world_to_pixel(world_x, world_y, world_z)
            return float(px), float(py)
        except Exception as exc:  # projection must never break a review
            log.debug("PoseProjection failed: {}", exc)
            return None


def resolve_projection(
    camera_id: int,
    calibrators: dict | None = None,
    pose_projectors: dict | None = None,
) -> Optional[ProjectionModel]:
    """Pick the best available projection backend for a camera.

    Prefers a full camera-pose calibration; falls back to the ground homography;
    returns ``None`` when the camera has no calibration at all.
    """
    if pose_projectors and camera_id in pose_projectors and pose_projectors[camera_id] is not None:
        model = PoseProjection(pose_projectors[camera_id])
        if model.available:
            return model
    calibrator = (calibrators or {}).get(camera_id)
    if calibrator is not None:
        return HomographyProjection(calibrator, camera_id)
    return None
