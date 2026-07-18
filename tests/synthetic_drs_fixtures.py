from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.ball_detector import BallDetection, DetectionResult
from core.model_selector import ModelReadiness
from core.pitch_calibration import ManualPitchCalibrator

SCENARIO_DIR = Path("tests/fixtures/scenarios")
FRAME_SIZE = (640, 360)
FPS = 30.0
FRAME_COUNT = 90
BALL_COLOR_BGR = (255, 0, 255)

CALIBRATION_MARKERS = {
    "off_stump": {"x": 260.0, "y": 250.0},
    "middle_stump": {"x": 320.0, "y": 250.0},
    "leg_stump": {"x": 380.0, "y": 250.0},
    "bowling_crease": {"x": 320.0, "y": 250.0},
    "popping_crease": {"x": 320.0, "y": 330.0},
}


@dataclass(frozen=True, slots=True)
class Scenario:
    filename: str
    start_lateral_mm: float
    end_lateral_mm: float
    start_along_mm: float
    end_along_mm: float
    visible_until: int = FRAME_COUNT


SCENARIOS = (
    Scenario("lbw_out_in_line.mp4", 0.0, 0.0, -1500.0, -120.0),
    Scenario("lbw_not_out_outside_leg.mp4", -260.0, -260.0, -1500.0, -120.0),
    Scenario("lbw_not_out_misses_stumps.mp4", 0.0, 430.0, -1500.0, -120.0),
    Scenario("lbw_inconclusive_tracking_lost.mp4", 0.0, 0.0, -1500.0, -120.0, visible_until=28),
)


def ensure_synthetic_drs_videos() -> list[Path]:
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    homography, _error = ManualPitchCalibrator().compute_homography(CALIBRATION_MARKERS)
    inverse_homography = np.linalg.inv(np.asarray(homography, dtype=np.float64))
    paths = []
    for scenario in SCENARIOS:
        path = SCENARIO_DIR / scenario.filename
        _write_scenario_video(path, scenario, inverse_homography)
        paths.append(path)
    return paths


def save_synthetic_calibration(calibration_dir: Path) -> Path:
    calibration_dir.mkdir(parents=True, exist_ok=True)
    ManualPitchCalibrator().save_profile(1, CALIBRATION_MARKERS, FRAME_SIZE)
    return calibration_dir / "readiness.json"


class SyntheticBallDetector:
    """Color-threshold detector for generated fixture videos only."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.model_readiness = ModelReadiness(
            selected_model="synthetic_fixture_detector",
            model_path="tests/fixtures/scenarios",
            detector_family="opencv_hsv_fixture",
            map50=0.99,
            map50_95=0.95,
            ball_recall=0.99,
            precision=0.99,
            inference_ms=1.0,
            usable=True,
            reason="Deterministic OpenCV detector for synthetic DRS fixture tests.",
        )

    def detect(
        self,
        frame: np.ndarray,
        frame_id: int,
        timestamp_ms: float,
        camera_id: int = 0,
        preprocess: bool = True,
        imgsz: int | None = None,
    ) -> DetectionResult:
        del preprocess, imgsz
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([135, 80, 80]), np.array([175, 255, 255]))
        contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[BallDetection] = []
        if contours:
            contour = max(contours, key=cv2.contourArea)
            if cv2.contourArea(contour) >= 20.0:
                x, y, w, h = cv2.boundingRect(contour)
                detections.append(BallDetection(frame_id, timestamp_ms, x, y, x + w, y + h, 0.96, camera_id))
        return DetectionResult(frame_id, timestamp_ms, camera_id, detections, 1.0)

    def annotate(self, frame: np.ndarray, result: DetectionResult) -> np.ndarray:
        for detection in result.detections:
            cv2.rectangle(frame, (detection.x1, detection.y1), (detection.x2, detection.y2), (0, 255, 255), 1)
        return frame


def _write_scenario_video(path: Path, scenario: Scenario, inverse_homography: np.ndarray) -> None:
    width, height = FRAME_SIZE
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Could not open fixture writer for {path}")

    for frame_id in range(FRAME_COUNT):
        frame = _pitch_frame(width, height)
        if frame_id < scenario.visible_until:
            progress = frame_id / float(FRAME_COUNT - 1)
            along_mm = _ease(scenario.start_along_mm, scenario.end_along_mm, progress)
            lateral_mm = _ease(scenario.start_lateral_mm, scenario.end_lateral_mm, progress)
            x, y = _pitch_mm_to_pixel(lateral_mm, along_mm, inverse_homography)
            bounce_lift = abs(progress - 0.45) * 22.0
            center = (int(round(x)), int(round(y - bounce_lift)))
            cv2.circle(frame, center, 7, BALL_COLOR_BGR, -1, cv2.LINE_AA)
            cv2.circle(frame, center, 8, (255, 255, 255), 1, cv2.LINE_AA)
        writer.write(frame)
    writer.release()


def _pitch_frame(width: int, height: int) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (42, 105, 54)
    cv2.rectangle(frame, (245, 70), (395, 340), (92, 139, 84), -1)
    cv2.rectangle(frame, (245, 70), (395, 340), (180, 205, 175), 1)
    cv2.line(frame, (250, 250), (390, 250), (235, 235, 220), 2)
    cv2.line(frame, (250, 330), (390, 330), (235, 235, 220), 2)
    for x in (296, 320, 344):
        cv2.line(frame, (x, 216), (x, 250), (240, 230, 200), 3, cv2.LINE_AA)
    cv2.rectangle(frame, (294, 151), (352, 309), (70, 120, 170), 1)
    return frame


def _pitch_mm_to_pixel(lateral_mm: float, along_mm: float, inverse_homography: np.ndarray) -> tuple[float, float]:
    world_x_m = 0.1143 + (lateral_mm / 1000.0)
    world_y_m = along_mm / 1000.0
    point = np.array([[[world_x_m, world_y_m]]], dtype=np.float32)
    pixel = cv2.perspectiveTransform(point, inverse_homography.astype(np.float32))[0, 0]
    return float(pixel[0]), float(pixel[1])


def _ease(start: float, end: float, progress: float) -> float:
    return start + ((end - start) * progress)
