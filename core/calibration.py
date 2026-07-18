"""Multi-camera calibration and pixel-to-world mapping utilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from config.settings import (
    CALIBRATION_DIR,
    CHARUCO_DICTIONARY_ID,
    CHARUCO_MARKER_SIZE_MM,
    CHARUCO_SQUARE_SIZE_MM,
    CHARUCO_SQUARES_X,
    CHARUCO_SQUARES_Y,
)
from core.calibration_paths import get_intrinsics_path, get_legacy_path
from utils.helpers import save_json
from utils.helpers import load_json


PROFILE_DIR = Path("config/calibration_profiles")

# --- calibration storage paths (intrinsics separated from pitch pose) --------
# Historically both camera intrinsics AND the manual pitch profile were written to
# ``calibration_<id>.json``, so one calibration silently overwrote the other. They
# now live in dedicated files; ``calibration_<id>.json`` is read only for backward
# compatibility (with a one-time migration notice) and never written.
_INTRINSICS_MIGRATION_NOTIFIED: set[str] = set()


def intrinsics_path(camera_id: int | str) -> Path:
    return get_intrinsics_path(camera_id, CALIBRATION_DIR)


def legacy_calibration_path(camera_id: int | str) -> Path:
    return get_legacy_path(camera_id, CALIBRATION_DIR)


def _notify_intrinsics_migration(camera_id: int | str, legacy: Path, new: Path) -> None:
    key = str(camera_id)
    if key in _INTRINSICS_MIGRATION_NOTIFIED:
        return
    _INTRINSICS_MIGRATION_NOTIFIED.add(key)
    print(
        f"MIGRATION: camera {camera_id} intrinsics loaded from legacy {legacy.name}; "
        f"they will move to {new.name} on the next intrinsics save."
    )


def load_intrinsics_data(camera_id: int | str) -> Optional[dict]:
    """Return the intrinsics JSON for a camera, or None.

    Prefers ``intrinsics_<id>.json``; falls back to a legacy ``calibration_<id>.json``
    that actually holds intrinsics (has a ``camera_matrix``), emitting a one-time notice.
    """
    new = intrinsics_path(camera_id)
    if new.exists():
        try:
            return load_json(new)
        except Exception as exc:
            print(f"WARNING: could not read intrinsics from {new}: {exc}")
            return None
    legacy = legacy_calibration_path(camera_id)
    if legacy.exists():
        try:
            data = load_json(legacy)
        except Exception:
            return None
        if isinstance(data, dict) and data.get("camera_matrix") is not None:
            _notify_intrinsics_migration(camera_id, legacy, new)
            return data
    return None


def _camera_calibration_from_dict(data: dict) -> "CameraCalibration":
    known = {f.name for f in fields(CameraCalibration)}
    return CameraCalibration(**{k: v for k, v in data.items() if k in known})
PITCH_WORLD_POINTS = np.array(
    [
        [-1.32, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [1.32, 0.0, 0.0],
        [-1.32, 1.22, 0.0],
        [0.0, 1.22, 0.0],
        [1.32, 1.22, 0.0],
        [-0.1143, 20.12, 0.711],
        [0.0, 20.12, 0.711],
        [0.1143, 20.12, 0.711],
    ],
    dtype=np.float32,
)
PITCH_POINT_LABELS = [
    "Bowling crease - left edge",
    "Bowling crease - center",
    "Bowling crease - right edge",
    "Popping crease - left edge",
    "Popping crease - center",
    "Popping crease - right edge",
    "Striker stumps - left top",
    "Striker stumps - center top",
    "Striker stumps - right top",
]

# Along-pitch coordinate of the striker's stumps in this world frame (Y=0 at the bowling
# crease, +Y toward the striker; see PITCH_WORLD_POINTS).
STRIKER_STUMPS_ALONG_M = 20.12


def summarize_ground_trajectory(project, points_px, timestamps_s, bounce_px=None,
                                stump_along_m: float = STRIKER_STUMPS_ALONG_M) -> dict:
    """Physics sanity summary for a calibrated delivery — NOT a renderer, just numbers.

    ``project`` maps an image pixel to world GROUND metres (X lateral, Y along-pitch from
    the bowling crease, Z up). Projecting a flight pixel to the ground plane gives the
    ball's ground-shadow (parallax) — fine for a sanity check and exact at the bounce
    (which is on the ground). Returns ground-shadow speed (km/h) and the bounce location,
    so you can eyeball whether the canonical calibration produces physically sane values
    BEFORE wiring it into the pipeline."""
    world: list[tuple[float, float, float]] = []
    for (x, y), t in zip(points_px, timestamps_s):
        try:
            wx, wy, _ = project(float(x), float(y))
        except Exception:
            continue
        world.append((wx, wy, float(t)))
    speeds: list[float] = []
    for (ax, ay, ta), (bx, by, tb) in zip(world, world[1:]):
        dt = tb - ta
        if dt > 0:
            speeds.append(float(np.hypot(bx - ax, by - ay)) / dt * 3.6)
    speeds.sort()
    ground_speed = speeds[len(speeds) // 2] if speeds else 0.0
    bounce = None
    if bounce_px is not None:
        try:
            bx, by, _ = project(float(bounce_px[0]), float(bounce_px[1]))
            bounce = {"along_m": round(by, 2), "lateral_m": round(bx, 2),
                      "from_stumps_m": round(stump_along_m - by, 2)}
        except Exception:
            bounce = None
    return {"ground_speed_kmh": round(ground_speed, 1), "bounce": bounce,
            "points_projected": len(world), "points_total": len(points_px)}


@dataclass(slots=True)
class CameraCalibration:
    camera_id: int
    image_size: tuple[int, int]
    rms_error: float
    camera_matrix: list[list[float]]
    distortion_coeffs: list[list[float]]
    rotation_vectors: list[list[float]]
    translation_vectors: list[list[float]]
    homography: Optional[list[list[float]]] = None


class MultiCameraCalibrator:
    """Calibrates cameras from the canonical ChArUco board image set."""

    def __init__(
        self,
        squares_x: int = CHARUCO_SQUARES_X,
        squares_y: int = CHARUCO_SQUARES_Y,
        square_size_mm: float = CHARUCO_SQUARE_SIZE_MM,
        marker_size_mm: float = CHARUCO_MARKER_SIZE_MM,
        dictionary_name: str = CHARUCO_DICTIONARY_ID,
    ) -> None:
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("OpenCV ArUco is unavailable; install opencv-contrib-python>=4.9")

        self.squares_x = squares_x
        self.squares_y = squares_y
        self.square_size_mm = square_size_mm
        self.marker_size_mm = marker_size_mm
        self.dictionary_name = dictionary_name

        dictionary_id = getattr(cv2.aruco, dictionary_name)
        self.dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        self.board = cv2.aruco.CharucoBoard(
            (squares_x, squares_y),
            square_size_mm,
            marker_size_mm,
            self.dictionary,
        )
        self.detector = cv2.aruco.CharucoDetector(self.board)

    def calibrate_camera(self, camera_id: int, image_paths: list[Path]) -> CameraCalibration:
        all_charuco_corners: list[np.ndarray] = []
        all_charuco_ids: list[np.ndarray] = []
        image_size: Optional[tuple[int, int]] = None

        for image_path in image_paths:
            image = cv2.imread(str(image_path))
            if image is None:
                continue
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            image_size = (gray.shape[1], gray.shape[0])
            charuco_corners, charuco_ids, _, _ = self.detector.detectBoard(gray)

            # Four non-collinear ChArUco corners are the mathematical minimum
            # for a useful board pose; richer views improve calibration quality.
            if charuco_ids is None or charuco_corners is None or len(charuco_ids) < 4:
                continue
            all_charuco_corners.append(charuco_corners.astype(np.float32))
            all_charuco_ids.append(charuco_ids.astype(np.int32))

        if not all_charuco_corners or image_size is None:
            raise ValueError(f"No usable ChArUco detections found for camera {camera_id}")

        rms, camera_matrix, dist, rvecs, tvecs = cv2.aruco.calibrateCameraCharuco(
            all_charuco_corners,
            all_charuco_ids,
            self.board,
            image_size,
            None,
            None,
        )
        calibration = CameraCalibration(
            camera_id=camera_id,
            image_size=image_size,
            rms_error=float(rms),
            camera_matrix=camera_matrix.tolist(),
            distortion_coeffs=dist.tolist(),
            rotation_vectors=[item.tolist() for item in rvecs],
            translation_vectors=[item.tolist() for item in tvecs],
        )
        return calibration

    def set_pitch_homography(
        self,
        calibration: CameraCalibration,
        image_points: np.ndarray,
        world_points_m: np.ndarray,
    ) -> CameraCalibration:
        homography, _ = cv2.findHomography(image_points.astype(np.float32), world_points_m.astype(np.float32))
        calibration.homography = homography.tolist()
        return calibration

    def undistort(self, frame: np.ndarray, calibration: CameraCalibration) -> np.ndarray:
        return cv2.undistort(
            frame,
            np.asarray(calibration.camera_matrix, dtype=np.float32),
            np.asarray(calibration.distortion_coeffs, dtype=np.float32),
        )

    def pixel_to_world(self, x: float, y: float, calibration: CameraCalibration) -> tuple[float, float]:
        if calibration.homography is None:
            raise ValueError("Calibration has no pitch-plane homography")
        point = np.array([[[x, y]]], dtype=np.float32)
        mapped = cv2.perspectiveTransform(point, np.asarray(calibration.homography, dtype=np.float32))
        return float(mapped[0, 0, 0]), float(mapped[0, 0, 1])

    def pixel_to_pitch_mm(self, camera_id: int, px: float, py: float, calibration: CameraCalibration) -> tuple[float, float]:
        if calibration.camera_id != camera_id:
            raise ValueError(f"Calibration belongs to camera {calibration.camera_id}, not {camera_id}")
        x, y = self.pixel_to_world(px, py, calibration)
        return x * 1000.0, y * 1000.0

    def triangulate_3d(
        self,
        observations: dict[int, tuple[float, float]],
        calibrations: dict[int, CameraCalibration],
    ) -> tuple[float, float, float]:
        if len(observations) < 2:
            raise ValueError("At least two calibrated camera observations are required")
        camera_ids = list(observations.keys())[:2]
        projections = []
        points = []
        for camera_id in camera_ids:
            calibration = calibrations[camera_id]
            camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
            rvec = np.asarray(calibration.rotation_vectors[0], dtype=np.float64)
            tvec = np.asarray(calibration.translation_vectors[0], dtype=np.float64)
            rotation, _ = cv2.Rodrigues(rvec)
            projection = camera_matrix @ np.hstack([rotation, tvec.reshape(3, 1)])
            projections.append(projection)
            points.append(np.asarray(observations[camera_id], dtype=np.float64).reshape(2, 1))
        point_h = cv2.triangulatePoints(projections[0], projections[1], points[0], points[1])
        denom = float(point_h[3, 0]) if abs(float(point_h[3, 0])) > 1e-9 else 1e-9
        point = point_h[:3, 0] / denom
        return float(point[0]), float(point[1]), float(point[2])

    def homography_validation_error_cm(
        self,
        calibration: CameraCalibration,
        image_points: np.ndarray,
        world_points_m: np.ndarray,
    ) -> float:
        if calibration.homography is None:
            raise ValueError("Calibration has no pitch-plane homography")
        projected = cv2.perspectiveTransform(
            image_points.reshape(-1, 1, 2).astype(np.float32),
            np.asarray(calibration.homography, dtype=np.float32),
        ).reshape(-1, 2)
        error_m = np.linalg.norm(projected - world_points_m.reshape(-1, 2), axis=1)
        return float(np.mean(error_m) * 100.0)

    def save(self, calibrations: list[CameraCalibration], path: Path | None = None) -> Path:
        path = path or CALIBRATION_DIR / "camera_calibration.json"
        return save_json([asdict(item) for item in calibrations], path)

    def save_per_camera(self, calibration: CameraCalibration) -> Path:
        payload = {
            "type": "intrinsics",
            "schema_version": 1,
            "camera_id": calibration.camera_id,
            # UTC, matching pose_<id>.json so timestamps are comparable across both files.
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            **asdict(calibration),
        }
        return save_json(payload, intrinsics_path(calibration.camera_id))

    def load_per_camera(self, camera_id: int) -> CameraCalibration:
        data = load_intrinsics_data(camera_id)
        if data is None:
            raise FileNotFoundError(f"No intrinsics calibration for camera {camera_id}")
        return _camera_calibration_from_dict(data)

    def draw_reprojection(
        self,
        frame: np.ndarray,
        calibration: CameraCalibration,
        object_points: np.ndarray,
        rvec: np.ndarray,
        tvec: np.ndarray,
    ) -> np.ndarray:
        projected, _ = cv2.projectPoints(
            object_points,
            rvec,
            tvec,
            np.asarray(calibration.camera_matrix, dtype=np.float32),
            np.asarray(calibration.distortion_coeffs, dtype=np.float32),
        )
        for point in projected.reshape(-1, 2):
            cv2.circle(frame, tuple(point.astype(int)), 4, (0, 255, 255), -1, cv2.LINE_AA)
        return frame

class PitchCalibrator:
    """Single-camera pitch calibration using nine clicked pitch landmarks."""

    def __init__(self, profile_dir: Path = PROFILE_DIR) -> None:
        self.profile_dir = profile_dir
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.profile: dict | None = None
        self.image_points: list[list[float]] = []
        self.world_points = PITCH_WORLD_POINTS.copy()

    def calibrate_interactive(
        self,
        camera_frame: np.ndarray,
        camera_id: int,
        profile_name: str,
        ground_name: str,
    ) -> dict:
        """Click nine landmarks on a frame, solve camera pose, and save on ENTER."""
        if camera_frame is None or camera_frame.size == 0:
            raise ValueError("camera_frame is empty")

        self.image_points = []
        display = camera_frame.copy()
        window_name = "DRS pitch calibration"

        def redraw() -> None:
            nonlocal display
            display = camera_frame.copy()
            instruction_index = min(len(self.image_points), len(PITCH_POINT_LABELS) - 1)
            cv2.putText(
                display,
                f"Click {len(self.image_points) + 1}/9: {PITCH_POINT_LABELS[instruction_index]}",
                (20, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            for idx, (x, y) in enumerate(self.image_points, start=1):
                cv2.circle(display, (int(x), int(y)), 7, (0, 255, 255), -1, cv2.LINE_AA)
                cv2.putText(display, str(idx), (int(x) + 9, int(y) - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
            cv2.imshow(window_name, display)

        def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
            if event == cv2.EVENT_LBUTTONDOWN and len(self.image_points) < 9:
                self.image_points.append([float(x), float(y)])
                redraw()

        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(window_name, on_mouse)
        redraw()

        while True:
            key = cv2.waitKey(30) & 0xFF
            if key in {13, 10} and len(self.image_points) == 9:
                profile = self._solve_profile(camera_frame, camera_id, profile_name, ground_name, self.image_points)
                overlay = self.verify_calibration(camera_frame, profile)["overlay_frame"]
                cv2.imshow(window_name, overlay)
                print(f"Calibration RMS error: {profile['rms_error_px']:.2f}px - {self._quality_label(profile['rms_error_px'])}")
                confirm_key = cv2.waitKey(0) & 0xFF
                if confirm_key in {13, 10}:
                    self.profile = profile
                    self.save_profile(profile_name, ground_name, camera_id)
                    cv2.destroyWindow(window_name)
                    return profile
                if confirm_key in {ord("r"), ord("R")}:
                    self.image_points = []
                    redraw()
            elif key in {ord("r"), ord("R")}:
                self.image_points = []
                redraw()
            elif key == 27:
                cv2.destroyWindow(window_name)
                raise KeyboardInterrupt("Calibration cancelled")

    def save_profile(self, profile_name: str, ground_name: str, camera_id: int | str) -> Path:
        """Save the most recently solved calibration profile."""
        if self.profile is None:
            raise ValueError("No calibration profile has been solved")
        safe_ground = self._safe_name(ground_name)
        path = self.profile_dir / f"{camera_id}_{safe_ground}.json"
        return save_json(self.profile, path)

    def load_profile(self, camera_id: int | str, ground_name: str | None = None) -> dict | None:
        """Load a calibration profile for a camera, newest first if ground is omitted."""
        if ground_name:
            path = self.profile_dir / f"{camera_id}_{self._safe_name(ground_name)}.json"
            if not path.exists():
                return None
            self.profile = load_json(path)
            return self.profile
        matches = sorted(self.profile_dir.glob(f"{camera_id}_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not matches:
            return None
        self.profile = load_json(matches[0])
        return self.profile

    def pixel_to_world(self, pixel_x: float, pixel_y: float, ground_z: float = 0.0) -> tuple[float, float, float]:
        """Project a pixel ray to the configured world Z plane."""
        profile = self._require_profile()
        camera_matrix = np.asarray(profile["camera_matrix"], dtype=np.float64)
        rvec = np.asarray(profile["rvec"], dtype=np.float64).reshape(3, 1)
        tvec = np.asarray(profile["tvec"], dtype=np.float64).reshape(3, 1)
        rotation, _ = cv2.Rodrigues(rvec)
        camera_center = -rotation.T @ tvec
        pixel = np.array([pixel_x, pixel_y, 1.0], dtype=np.float64).reshape(3, 1)
        ray_camera = np.linalg.inv(camera_matrix) @ pixel
        ray_world = rotation.T @ ray_camera
        denom = float(ray_world[2, 0])
        if abs(denom) < 1e-9:
            raise ValueError("Pixel ray is parallel to requested world plane")
        scale = (ground_z - float(camera_center[2, 0])) / denom
        world = camera_center + scale * ray_world
        return float(world[0, 0]), float(world[1, 0]), float(ground_z)

    def world_to_pixel(self, world_x: float, world_y: float, world_z: float) -> tuple[int, int]:
        """Project a 3D world coordinate back to image pixels."""
        profile = self._require_profile()
        projected, _ = cv2.projectPoints(
            np.array([[world_x, world_y, world_z]], dtype=np.float32),
            np.asarray(profile["rvec"], dtype=np.float64).reshape(3, 1),
            np.asarray(profile["tvec"], dtype=np.float64).reshape(3, 1),
            np.asarray(profile["camera_matrix"], dtype=np.float64),
            np.asarray(profile["dist_coeffs"], dtype=np.float64),
        )
        x, y = projected.reshape(-1, 2)[0]
        return int(round(float(x))), int(round(float(y)))

    def verify_calibration(self, frame: np.ndarray, profile: dict | None = None) -> dict:
        """Draw reprojected landmarks and return RMS validity metadata."""
        profile = profile or self._require_profile()
        overlay = frame.copy()
        image_points = np.asarray(profile["image_points"], dtype=np.float32)
        projected, _ = cv2.projectPoints(
            np.asarray(profile["world_points"], dtype=np.float32),
            np.asarray(profile["rvec"], dtype=np.float64).reshape(3, 1),
            np.asarray(profile["tvec"], dtype=np.float64).reshape(3, 1),
            np.asarray(profile["camera_matrix"], dtype=np.float64),
            np.asarray(profile["dist_coeffs"], dtype=np.float64),
        )
        projected_points = projected.reshape(-1, 2)
        for idx, (clicked, reproj) in enumerate(zip(image_points, projected_points), start=1):
            cv2.circle(overlay, tuple(clicked.astype(int)), 6, (0, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(overlay, tuple(reproj.astype(int)), 8, (0, 180, 0), 2, cv2.LINE_AA)
            cv2.putText(overlay, str(idx), tuple(reproj.astype(int) + np.array([9, -9])), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 180, 0), 2)
        rms = float(profile.get("rms_error_px", 999.0))
        return {"rms_error": rms, "is_valid": rms < 6.0, "overlay_frame": overlay}

    def _solve_profile(
        self,
        frame: np.ndarray,
        camera_id: int | str,
        profile_name: str,
        ground_name: str,
        image_points: list[list[float]],
    ) -> dict:
        image_size = (int(frame.shape[1]), int(frame.shape[0]))
        # Use REAL ChArUco intrinsics when they exist; only guess as a loud fallback.
        camera_matrix, dist_coeffs, intrinsics_source, warnings = self._load_intrinsics(camera_id, image_size)
        ok, rvec, tvec = cv2.solvePnP(
            self.world_points.astype(np.float32),
            np.asarray(image_points, dtype=np.float32),
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok:
            raise ValueError("cv2.solvePnP failed for clicked pitch points")
        projected, _ = cv2.projectPoints(self.world_points, rvec, tvec, camera_matrix, dist_coeffs)
        errors = np.linalg.norm(projected.reshape(-1, 2) - np.asarray(image_points, dtype=np.float32), axis=1)
        rms = float(np.sqrt(np.mean(errors**2)))
        return {
            "profile_name": profile_name,
            "ground": ground_name,
            "camera_id": str(camera_id),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "rms_error_px": rms,
            "intrinsics_source": intrinsics_source,   # "charuco" | "estimated"
            "warnings": warnings,                     # persisted so the profile self-declares
            "image_size": list(image_size),
            "image_points": [[float(x), float(y)] for x, y in image_points],
            "world_points": self.world_points.astype(float).tolist(),
            "rvec": rvec.reshape(-1).astype(float).tolist(),
            "tvec": tvec.reshape(-1).astype(float).tolist(),
            "camera_matrix": camera_matrix.astype(float).tolist(),
            "dist_coeffs": dist_coeffs.reshape(-1).astype(float).tolist(),
        }

    def _load_intrinsics(self, camera_id: int | str, image_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, str, list[str]]:
        """Prefer REAL ChArUco intrinsics (from scripts/calibrate.py →
        data/calibration/intrinsics_<id>.json) over a guessed pinhole. Reads a legacy
        calibration_<id>.json when the new file is absent. Falls back to an estimate with
        a loud warning (printed AND persisted into the profile) so nobody — now or in six
        months — mistakes a fabricated pinhole for a measured camera."""
        data = load_intrinsics_data(camera_id)
        if data is not None:
            try:
                camera_matrix = np.asarray(data["camera_matrix"], dtype=np.float64)
                raw_dist = data.get("distortion_coeffs")
                if raw_dist is None:
                    raw_dist = data.get("dist_coeffs", [[0.0, 0.0, 0.0, 0.0, 0.0]])
                dist_coeffs = np.asarray(raw_dist, dtype=np.float64).reshape(-1, 1)
                if camera_matrix.shape == (3, 3):
                    return camera_matrix, dist_coeffs, "charuco", []
            except Exception as exc:
                print(f"WARNING: could not read intrinsics for camera {camera_id}: {exc}")
        warning = ("No ChArUco calibration found for this camera. Pose solved using estimated "
                   "pinhole intrinsics (focal=max(w,h), no distortion). Ground geometry is approximate.")
        print(f"WARNING: no intrinsic calibration for camera {camera_id} ({intrinsics_path(camera_id)}).")
        print(f"         {warning}")
        return self._initial_camera_matrix(image_size), np.zeros((5, 1), dtype=np.float64), "estimated", [warning]

    def _require_profile(self) -> dict:
        if self.profile is None:
            raise ValueError("No calibration profile loaded")
        return self.profile

    def _initial_camera_matrix(self, image_size: tuple[int, int]) -> np.ndarray:
        width, height = image_size
        focal = float(max(width, height))
        return np.array([[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]], dtype=np.float64)

    def _safe_name(self, value: str) -> str:
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip()) or "default"

    def _quality_label(self, rms_error_px: float) -> str:
        if rms_error_px < 3.0:
            return "GOOD"
        if rms_error_px < 6.0:
            return "ACCEPTABLE"
        return "POOR"
