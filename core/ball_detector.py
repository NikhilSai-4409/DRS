"""YOLOv8 cricket ball detection with OpenCV annotation and exports."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from config.settings import (
    DETECTIONS_DIR,
    INFERENCE_DEVICE,
    USE_TENSORRT,
    YOLO_CONF_THRESH,
    YOLO_IMG_SIZE,
    YOLO_IOU_THRESH,
    YOLO_MODEL_PATH,
)
from core.model_selector import DetectorModelSelector, ModelReadiness
from utils.helpers import draw_bounding_box, save_csv, save_json, timestamp_str
from utils.logger import get_logger

log = get_logger("ball_detector")
CRICKET_MODEL_PATH = Path("models/cricket_ball_yolov8.pt")
REAL_MATCH_MODEL_PATH = Path("models/cricket_ball_yolo11l_real.pt")
GENERIC_YOLO_MODEL_PATH = Path("yolo11l.pt")
BALL_CLASS_IDS = {0}
SPORTS_BALL_CLASS_ID = 32


@dataclass(slots=True)
class BallDetection:
    frame_id: int
    timestamp_ms: float
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    camera_id: int = 0
    cx: int = field(init=False)
    cy: int = field(init=False)

    def __post_init__(self) -> None:
        self.cx = (self.x1 + self.x2) // 2
        self.cy = (self.y1 + self.y2) // 2

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.x2, self.y2


@dataclass(slots=True)
class DetectionResult:
    frame_id: int
    timestamp_ms: float
    camera_id: int
    detections: list[BallDetection]
    inference_ms: float

    @property
    def best(self) -> Optional[BallDetection]:
        return max(self.detections, key=lambda item: item.confidence) if self.detections else None


class FramePreprocessor:
    """Contrast and sharpening tuned for outdoor cricket footage."""

    def __init__(self) -> None:
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        lightness, a, b = cv2.split(lab)
        lightness = self._clahe.apply(lightness)
        enhanced = cv2.cvtColor(cv2.merge((lightness, a, b)), cv2.COLOR_LAB2BGR)
        return cv2.filter2D(enhanced, -1, self._kernel)


class BallDetector:
    """Detect red or white cricket balls using a fine-tuned YOLOv8 model."""

    def __init__(
        self,
        model_path: Path | str = YOLO_MODEL_PATH,
        device: str = INFERENCE_DEVICE,
        export_results: bool = True,
    ) -> None:
        self.device = device
        self.export_results = export_results
        self.imgsz = YOLO_IMG_SIZE  # inference resolution; callers may override per-detect
        self.preprocessor = FramePreprocessor()
        self.model: Any = None
        self.model_readiness: ModelReadiness | None = None
        self.active_model_name = "none"
        self.ball_class_ids = set(BALL_CLASS_IDS)
        self.using_coco_fallback = False
        self._gpu_failed = False
        self.results_log: list[dict[str, Any]] = []
        default_path = Path(YOLO_MODEL_PATH)
        requested_path = Path(model_path) if model_path else None
        self._load_model(None if requested_path == default_path else requested_path)

    def _load_model(self, model_path: Path | None) -> None:
        try:
            from ultralytics import YOLO
        except ImportError:
            log.warning("ultralytics is not installed; detector will return empty results")
            return

        global BALL_CLASS_IDS
        if model_path is None:
            from core.model_registry import ModelRegistry

            production = ModelRegistry().production_model_path()
            if production is not None and production.exists():
                # The registry is the single source of truth for the live model.
                # select(None) now resolves to this same production model.
                selected_path, readiness = DetectorModelSelector().select(None)
                BALL_CLASS_IDS = {0}
                self.using_coco_fallback = False
                log.info("[DRS] Using production model from registry: {} -- class filter: {}", selected_path.name, BALL_CLASS_IDS)
            elif REAL_MATCH_MODEL_PATH.exists():
                selected_path, readiness = DetectorModelSelector().select(REAL_MATCH_MODEL_PATH)
                BALL_CLASS_IDS = {0}
                self.using_coco_fallback = False
                log.info("[DRS] Using real-match cricket model -- class filter: {}", BALL_CLASS_IDS)
            elif CRICKET_MODEL_PATH.exists():
                selected_path, readiness = DetectorModelSelector().select(CRICKET_MODEL_PATH)
                BALL_CLASS_IDS = {0}
                self.using_coco_fallback = False
                log.info("[DRS] Using cricket-specific model -- class filter: {}", BALL_CLASS_IDS)
            elif GENERIC_YOLO_MODEL_PATH.exists():
                selected_path, readiness = DetectorModelSelector().select(GENERIC_YOLO_MODEL_PATH)
                BALL_CLASS_IDS = {SPORTS_BALL_CLASS_ID}
                self.using_coco_fallback = True
                log.warning(
                    "[DRS] Cricket model not found -- falling back to generic YOLO. Detection quality may be lower."
                )
            else:
                selected_path, readiness = DetectorModelSelector().select(None)
                BALL_CLASS_IDS = {0}
                self.using_coco_fallback = False
        else:
            selected_path, readiness = DetectorModelSelector().select(model_path)
            BALL_CLASS_IDS = {0}
            self.using_coco_fallback = False
        self.model_readiness = readiness
        self.ball_class_ids = set(BALL_CLASS_IDS)
        self.active_model_name = selected_path.name
        if selected_path.exists():
            self.model = YOLO(str(selected_path))
            log.info("[DRS] Loaded {} detector from {}", readiness.detector_family, selected_path)
        else:
            self.model = None
            log.warning("[DRS] No local detector model found; add a cricket model under models/")

        if USE_TENSORRT and self.model is not None:
            try:
                self.model.export(format="engine", half=True, simplify=True)
            except Exception as exc:
                log.warning("TensorRT export failed: {}", exc)

    def detect(
        self,
        frame: np.ndarray,
        frame_id: int,
        timestamp_ms: float,
        camera_id: int = 0,
        preprocess: bool = True,
        imgsz: int | None = None,
    ) -> DetectionResult:
        if self.model is None:
            return DetectionResult(frame_id, timestamp_ms, camera_id, [], 0.0)

        input_frame = self.preprocessor(frame) if preprocess else frame
        started = time.perf_counter()

        # Larger imgsz preserves a small/fast ball in wide 1080p/4K footage that
        # 640 would downscale away. Falls back to the detector default.
        size = int(imgsz) if imgsz else self.imgsz

        device = self.device
        if self._gpu_failed:
            device = "cpu"

        try:
            raw_results = self.model.predict(
                source=input_frame,
                conf=YOLO_CONF_THRESH,
                iou=YOLO_IOU_THRESH,
                imgsz=size,
                device=device,
                verbose=False,
            )
        except (RuntimeError, Exception) as exc:
            exc_name = type(exc).__name__
            if device != "cpu" and exc_name in {"RuntimeError", "AcceleratorError", "CudaError"}:
                log.warning("[DRS] GPU inference failed ({}), retrying on CPU", exc)
                self._gpu_failed = True
                try:
                    raw_results = self.model.predict(
                        source=input_frame,
                        conf=YOLO_CONF_THRESH,
                        iou=YOLO_IOU_THRESH,
                        imgsz=size,
                        device="cpu",
                        verbose=False,
                    )
                except Exception:
                    return DetectionResult(frame_id, timestamp_ms, camera_id, [], 0.0)
            else:
                log.error("[DRS] CPU inference also failed: {}", exc)
                return DetectionResult(frame_id, timestamp_ms, camera_id, [], 0.0)

        inference_ms = (time.perf_counter() - started) * 1000.0

        detections: list[BallDetection] = []
        for result in raw_results:
            for box in result.boxes:
                if not self._is_ball_class(box):
                    continue
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                detections.append(
                    BallDetection(frame_id, timestamp_ms, x1, y1, x2, y2, float(box.conf[0]), camera_id)
                )
        if raw_results and not detections:
            log.warning("[DRS] No valid ball-class detections found after filtering classes {}", self.ball_class_ids)

        detections.sort(key=lambda item: item.confidence, reverse=True)
        output = DetectionResult(frame_id, timestamp_ms, camera_id, detections, inference_ms)
        if self.export_results:
            self.results_log.extend(self._rows(output))
        return output

    def _is_ball_class(self, box: Any) -> bool:
        return int(box.cls[0]) in self.ball_class_ids

    def annotate(self, frame: np.ndarray, result: DetectionResult) -> np.ndarray:
        for detection in result.detections:
            color = (0, 245, 255) if detection is result.best else (80, 220, 120)
            draw_bounding_box(frame, detection.bbox, f"ball {detection.confidence:.2f}", color)
            cv2.circle(frame, (detection.cx, detection.cy), 4, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.putText(frame, f"{result.inference_ms:.1f} ms", (12, frame.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
        return frame

    def flush(self, fmt: str = "json", stem: str | None = None) -> Path:
        stem = stem or timestamp_str()
        path = DETECTIONS_DIR / f"detections_{stem}.{fmt.lower()}"
        if fmt.lower() == "csv":
            save_csv(self.results_log, path)
        else:
            save_json(self.results_log, path)
        self.results_log.clear()
        return path

    def _rows(self, result: DetectionResult) -> list[dict[str, Any]]:
        rows = []
        for detection in result.detections:
            row = asdict(detection)
            row["inference_ms"] = result.inference_ms
            rows.append(row)
        if not rows:
            rows.append(
                {
                    "frame_id": result.frame_id,
                    "timestamp_ms": result.timestamp_ms,
                    "camera_id": result.camera_id,
                    "x1": None,
                    "y1": None,
                    "x2": None,
                    "y2": None,
                    "confidence": 0.0,
                    "cx": None,
                    "cy": None,
                    "inference_ms": result.inference_ms,
                }
            )
        return rows
