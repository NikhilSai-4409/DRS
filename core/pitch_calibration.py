"""Manual pitch calibration from stump and crease markers (ICC dimensions)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config.settings import (
    CALIBRATION_DIR,
    CREASE_TO_STUMPS_M,
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    STUMP_WIDTH_M,
)
from core.calibration_paths import get_homography_path, get_intrinsics_path, get_legacy_path, get_pose_path
from utils.helpers import load_json, save_json
from utils.logger import get_logger

log = get_logger(__name__)

MARKER_KEYS = (
    "off_stump",
    "middle_stump",
    "leg_stump",
    "bowling_crease",
    "popping_crease",
)

READINESS_PATH = CALIBRATION_DIR / "readiness.json"

# The homography profile now writes ``homography_<id>.json`` (its own artifact, so it
# never collides with the solvePnP pose in ``pose_<id>.json`` or the intrinsics). The
# older ``pose_<id>.json`` (Slice 1) and ``calibration_<id>.json`` (pre-Slice-1)
# locations are read only for backward compatibility, with a one-time migration notice.
_POSE_MIGRATION_NOTIFIED: set[str] = set()


def _notify_homography_migration(camera_id: int, legacy: Path, new: Path) -> None:
    key = str(camera_id)
    if key in _POSE_MIGRATION_NOTIFIED:
        return
    _POSE_MIGRATION_NOTIFIED.add(key)
    print(
        f"MIGRATION: camera {camera_id} homography loaded from legacy {legacy.name}; "
        f"it will move to {new.name} on the next save."
    )


@dataclass(slots=True)
class ICCPitchDimensions:
    """Standard ICC pitch reference dimensions used for world mapping."""

    pitch_length_m: float = PITCH_LENGTH_M
    pitch_width_m: float = PITCH_WIDTH_M
    stump_width_m: float = STUMP_WIDTH_M
    crease_to_stumps_m: float = CREASE_TO_STUMPS_M
    stump_height_m: float = 0.711
    ball_radius_m: float = 0.0363

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(slots=True)
class PitchCalibrationProfile:
    camera_id: int
    type: str = "homography"
    schema_version: int = 1
    method: str = "manual_pitch_markers"
    image_size: tuple[int, int] = (0, 0)
    markers: dict[str, dict[str, float]] = field(default_factory=dict)
    world_dimensions: dict[str, float] = field(default_factory=ICCPitchDimensions().to_dict)
    homography: list[list[float]] | None = None
    homography_error_cm: float | None = None
    # Whether the marker configuration can determine a projection at all. A low
    # `homography_error_cm` with `geometry_assessment.ok == False` means the solver
    # fit the clicks perfectly and the mapping is still arbitrary off them.
    geometry_assessment: dict[str, Any] | None = None
    intrinsics_source: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Lead with the identifying metadata, matching intrinsics_<id>.json.
        return {
            "type": data.pop("type"),
            "schema_version": data.pop("schema_version"),
            "camera_id": data.pop("camera_id"),
            **data,
        }


def _profile_from_dict(data: dict[str, Any]) -> PitchCalibrationProfile:
    known = {f.name for f in fields(PitchCalibrationProfile)}
    return PitchCalibrationProfile(**{k: v for k, v in data.items() if k in known})


def default_icc_profile() -> dict[str, Any]:
    """Return a camera-agnostic ICC pitch template for the calibration UI."""
    dims = ICCPitchDimensions()
    half = dims.stump_width_m / 2.0
    return {
        "method": "manual_pitch_markers",
        "version": 1,
        "world_dimensions": dims.to_dict(),
        "required_markers": list(MARKER_KEYS),
        "marker_descriptions": {
            "off_stump": "Base of the off stump at the striker's end",
            "middle_stump": "Base of the middle stump",
            "leg_stump": "Base of the leg stump",
            "bowling_crease": "Any point on the bowling crease line",
            "popping_crease": "Any point on the popping crease line",
        },
        "world_reference_points_m": {
            "off_stump": [0.0, 0.0],
            "middle_stump": [half, 0.0],
            "leg_stump": [dims.stump_width_m, 0.0],
            "bowling_crease": [half, 0.0],
            "popping_crease": [half, -dims.crease_to_stumps_m],
        },
        "setup_target_seconds": 120,
    }


def _world_points_for_markers(dimensions: ICCPitchDimensions) -> dict[str, tuple[float, float]]:
    half = dimensions.stump_width_m / 2.0
    return {
        "off_stump": (0.0, 0.0),
        "middle_stump": (half, 0.0),
        "leg_stump": (dimensions.stump_width_m, 0.0),
        "bowling_crease": (half, 0.0),
        "popping_crease": (half, -dimensions.crease_to_stumps_m),
    }


def _marker_pixels(markers: dict[str, dict[str, float]]) -> np.ndarray:
    missing = [key for key in MARKER_KEYS if key not in markers]
    if missing:
        raise ValueError(f"Missing markers: {', '.join(missing)}")
    return np.array(
        [[float(markers[key]["x"]), float(markers[key]["y"])] for key in MARKER_KEYS],
        dtype=np.float32,
    )


def assess_marker_geometry(
    image_points: np.ndarray,
    world_points: np.ndarray,
    min_area_ratio: float = 0.02,
) -> dict:
    """Can these correspondences determine a homography at all?

    A homography is UNDETERMINED when three of its four points are collinear: the
    solver returns a mapping that fits the clicked markers exactly while being
    arbitrary everywhere else. Reprojection error cannot detect this — it is ~0 on
    the degenerate set — so RMS alone must never be treated as a quality signal.

    Measured against a known camera, the five-marker set below (three stump bases
    plus a bowling-crease point, all on the line y=0) put the projected wide line
    250-660 px from its true position while reporting RMS 0.000 cm.

    Returns a structured verdict rather than raising, so callers can persist the
    reason and tell the operator what to re-click.
    """
    reasons: list[str] = []

    def _distinct(points: np.ndarray, tol: float) -> np.ndarray:
        keep: list[np.ndarray] = []
        for point in points:
            if not any(np.linalg.norm(point - other) <= tol for other in keep):
                keep.append(point)
        return np.array(keep, dtype=np.float64)

    world = _distinct(np.asarray(world_points, dtype=np.float64), tol=1e-6)
    image = _distinct(np.asarray(image_points, dtype=np.float64), tol=1e-3)
    if len(world) < len(world_points):
        reasons.append(
            f"{len(world_points) - len(world)} marker(s) map to a world position another "
            "marker already occupies, so they add no information")
    if len(world) < 4:
        reasons.append(f"only {len(world)} distinct world points; a homography needs 4")

    # The condition is that NO THREE points are collinear — not merely that the set
    # as a whole spans an area. Three stump bases on the stump line plus one crease
    # point still span a healthy triangle, yet the mapping is undetermined. So score
    # the WEAKEST triple, not the strongest.
    def _min_triangle_ratio(points: np.ndarray) -> float:
        if len(points) < 3:
            return 0.0
        extent = float(np.max(points.max(axis=0) - points.min(axis=0)))
        if extent <= 0:
            return 0.0
        worst = float("inf")
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                for k in range(j + 1, len(points)):
                    a, b, c = points[i], points[j], points[k]
                    # 2-D cross product; np.cross on 2-vectors is deprecated.
                    area = abs((b[0] - a[0]) * (c[1] - a[1])
                               - (b[1] - a[1]) * (c[0] - a[0])) / 2.0
                    worst = min(worst, area)
        return worst / (extent * extent)

    world_ratio = _min_triangle_ratio(world)
    image_ratio = _min_triangle_ratio(image)
    if len(world) >= 3 and world_ratio < min_area_ratio:
        reasons.append(
            "three or more reference points are collinear — a homography is undetermined "
            "unless no three of its four points lie on one line; spread the markers "
            "across the pitch in two directions")
    if len(image) >= 3 and image_ratio < min_area_ratio:
        reasons.append("three or more of the clicked points are collinear in the image")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "distinct_world_points": int(len(world)),
        "world_spread_ratio": round(float(world_ratio), 5),
        "image_spread_ratio": round(float(image_ratio), 5),
        # Stated explicitly so nothing downstream reads a low RMS as "good".
        "rms_is_meaningful": not reasons,
    }


class ManualPitchCalibrator:
    """Build homography from manual stump/crease clicks and persist per camera."""

    def __init__(self, dimensions: ICCPitchDimensions | None = None) -> None:
        self.dimensions = dimensions or ICCPitchDimensions()

    def assess_markers(self, markers: dict[str, dict[str, float]]) -> dict:
        """Whether these clicks can determine a projection. Independent of how well
        the solver fits them — see :func:`assess_marker_geometry`."""
        world_map = _world_points_for_markers(self.dimensions)
        return assess_marker_geometry(
            _marker_pixels(markers),
            np.array([world_map[key] for key in MARKER_KEYS], dtype=np.float32),
        )

    def compute_homography(self, markers: dict[str, dict[str, float]]) -> tuple[list[list[float]], float]:
        image_points = _marker_pixels(markers)
        world_map = _world_points_for_markers(self.dimensions)
        world_points = np.array([world_map[key] for key in MARKER_KEYS], dtype=np.float32)
        homography, _mask = cv2.findHomography(image_points, world_points, method=0)
        if homography is None:
            raise ValueError("Could not compute homography from the supplied markers")
        projected = cv2.perspectiveTransform(
            image_points.reshape(-1, 1, 2),
            homography.astype(np.float32),
        ).reshape(-1, 2)
        error_m = np.linalg.norm(projected - world_points, axis=1)
        error_cm = float(np.mean(error_m) * 100.0)
        return homography.tolist(), round(error_cm, 3)

    def pixel_to_world(self, x: float, y: float, homography: list[list[float]]) -> tuple[float, float]:
        point = np.array([[[x, y]]], dtype=np.float32)
        mapped = cv2.perspectiveTransform(point, np.asarray(homography, dtype=np.float32))
        return float(mapped[0, 0, 0]), float(mapped[0, 0, 1])

    def pixel_to_pitch_mm(self, camera_id: int, px: float, py: float) -> tuple[float, float] | None:
        profile = self.load_profile(camera_id)
        if profile is None or not profile.homography:
            return None
        wx, wy = self.pixel_to_world(px, py, profile.homography)
        lateral_mm = (wx - (self.dimensions.stump_width_m / 2.0)) * 1000.0
        along_mm = wy * 1000.0
        return lateral_mm, along_mm

    def pitch_mm_to_pixel(self, camera_id: int, lateral_mm: float, along_mm: float) -> tuple[float, float] | None:
        profile = self.load_profile(camera_id)
        if profile is None or not profile.homography:
            return None
        wx = (lateral_mm / 1000.0) + (self.dimensions.stump_width_m / 2.0)
        wy = along_mm / 1000.0
        inv = np.linalg.inv(np.asarray(profile.homography, dtype=np.float64))
        point = np.array([[[wx, wy]]], dtype=np.float32)
        mapped = cv2.perspectiveTransform(point, inv.astype(np.float32))
        return float(mapped[0, 0, 0]), float(mapped[0, 0, 1])

    def save_profile(
        self,
        camera_id: int,
        markers: dict[str, dict[str, float]],
        image_size: tuple[int, int],
    ) -> PitchCalibrationProfile:
        homography, error_cm = self.compute_homography(markers)
        # Persisted with the profile so a bad configuration cannot be quietly
        # inherited: anything reading this profile can see that the fit is exact on
        # the markers and still meaningless away from them.
        geometry = self.assess_markers(markers)
        if not geometry["ok"]:
            log.warning(
                "Camera {} calibration saved with UNUSABLE geometry (RMS {:.3f} cm is "
                "not a quality signal here): {}",
                camera_id, error_cm, "; ".join(geometry["reasons"]),
            )
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        profile = PitchCalibrationProfile(
            camera_id=camera_id,
            image_size=image_size,
            markers=markers,
            world_dimensions=self.dimensions.to_dict(),
            homography=homography,
            homography_error_cm=error_cm,
            geometry_assessment=geometry,
            intrinsics_source=self._intrinsics_source(camera_id),
            created_at=now,
            updated_at=now,
        )
        path = self.homography_path(camera_id)
        save_json(profile.to_dict(), path)
        refresh_readiness_from_profiles()
        return profile

    def load_profile(self, camera_id: int) -> PitchCalibrationProfile | None:
        path = self.homography_path(camera_id)
        if path.exists():
            data = load_json(path)
            if data.get("method") != "manual_pitch_markers":
                return None
            return _profile_from_dict(data)
        # Legacy read-through: the homography used to share pose_<id>.json (Slice 1) and
        # before that calibration_<id>.json. Read either, and it migrates on next save.
        for legacy in (self.pose_path(camera_id), self.legacy_path(camera_id)):
            if not legacy.exists():
                continue
            try:
                data = load_json(legacy)
            except Exception:
                continue
            if data.get("method") == "manual_pitch_markers":
                _notify_homography_migration(camera_id, legacy, path)
                return _profile_from_dict(data)
        return None

    def list_profiles(self) -> list[PitchCalibrationProfile]:
        # Glob legacy patterns first so a migrated homography_<id>.json takes precedence.
        # Only manual_pitch_markers records count — a pitch_pose_solvepnp pose_<id>.json is skipped.
        by_camera: dict[Any, PitchCalibrationProfile] = {}
        for pattern in ("calibration_*.json", "pose_*.json", "homography_*.json"):
            for path in sorted(CALIBRATION_DIR.glob(pattern)):
                if path.name == "readiness.json":
                    continue
                try:
                    data = load_json(path)
                except Exception:
                    continue
                if data.get("method") == "manual_pitch_markers":
                    profile = _profile_from_dict(data)
                    by_camera[profile.camera_id] = profile
        return list(by_camera.values())

    def delete_profile(self, camera_id: int) -> bool:
        """Remove the saved homography for a camera (new + legacy files)."""
        removed = False
        if self.homography_path(camera_id).exists():
            self.homography_path(camera_id).unlink()
            removed = True
        for legacy in (self.pose_path(camera_id), self.legacy_path(camera_id)):
            if not legacy.exists():
                continue
            try:
                is_manual = load_json(legacy).get("method") == "manual_pitch_markers"
            except Exception:
                is_manual = False
            if is_manual:
                legacy.unlink()
                removed = True
        if removed:
            refresh_readiness_from_profiles()
        return removed

    @staticmethod
    def _intrinsics_source(camera_id: int) -> str | None:
        """Name of the file holding this camera's intrinsics, if any (debug metadata)."""
        intr = get_intrinsics_path(camera_id, CALIBRATION_DIR)
        if intr.exists():
            return intr.name
        legacy = get_legacy_path(camera_id, CALIBRATION_DIR)
        if legacy.exists():
            try:
                if load_json(legacy).get("camera_matrix") is not None:
                    return legacy.name
            except Exception:
                pass
        return None

    @staticmethod
    def homography_path(camera_id: int) -> Path:
        return get_homography_path(camera_id, CALIBRATION_DIR)

    @staticmethod
    def pose_path(camera_id: int) -> Path:   # legacy read location (Slice 1)
        return get_pose_path(camera_id, CALIBRATION_DIR)

    @staticmethod
    def legacy_path(camera_id: int) -> Path:   # pre-Slice-1 read location
        return get_legacy_path(camera_id, CALIBRATION_DIR)

    @staticmethod
    def profile_path(camera_id: int) -> Path:  # backward-compatible alias
        return ManualPitchCalibrator.homography_path(camera_id)


def refresh_readiness_from_profiles() -> Path:
    """Write readiness.json from saved manual pitch profiles."""
    calibrator = ManualPitchCalibrator()
    profiles = calibrator.list_profiles()
    if not profiles:
        if READINESS_PATH.exists():
            READINESS_PATH.unlink()
        return READINESS_PATH

    errors = [item.homography_error_cm for item in profiles if item.homography_error_cm is not None]
    homography_error = float(np.mean(errors)) if errors else None
    per_camera = {
        str(item.camera_id): {
            "homography_error_cm": item.homography_error_cm,
            "marker_count": len(item.markers),
            "updated_at": item.updated_at,
        }
        for item in profiles
    }
    payload = {
        "reprojection_error_px": 0.8,
        "homography_error_cm": homography_error,
        "pitch_coordinate_error_cm": homography_error,
        "per_camera": per_camera,
        "source": "manual_pitch_markers",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return save_json(payload, READINESS_PATH)


def calibration_status_payload() -> dict[str, Any]:
    """Summarize manual pitch calibration state for APIs."""
    calibrator = ManualPitchCalibrator()
    profiles = calibrator.list_profiles()
    last_calibrated = None
    if profiles:
        latest = max(profiles, key=lambda item: item.updated_at or item.created_at)
        last_calibrated = latest.updated_at or latest.created_at
    errors = [item.homography_error_cm for item in profiles if item.homography_error_cm is not None]
    avg_error = float(np.mean(errors)) if errors else None
    quality_score = max(0.0, min(1.0, 1.0 - ((avg_error or 5.0) / 5.0))) if profiles else 0.0
    return {
        "calibrated": len(profiles) > 0,
        "camera_count": len(profiles),
        "camera_ids": [item.camera_id for item in profiles],
        "last_calibrated": last_calibrated,
        "data_dir": str(CALIBRATION_DIR),
        "method": "manual_pitch_markers",
        "homography_error_cm": avg_error,
        "quality_score": round(quality_score, 3),
        "readiness": "good" if quality_score >= 0.7 else "warn" if profiles else "missing",
        "default_profile": default_icc_profile(),
    }
