"""ByteTrack-style association for one fast cricket ball plus short Kalman gaps."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

import cv2
import numpy as np

from core.ball_detector import BallDetection, DetectionResult


@dataclass(slots=True)
class AssociatedTrackPoint:
    frame_id: int
    timestamp_ms: float
    camera_id: int
    x: float
    y: float
    vx: float
    vy: float
    speed_px_s: float
    direction_deg: float
    confidence: float
    predicted: bool
    real_detection: bool
    association_score: float
    rejected_jump: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class SingleBallByteTracker:
    """Associates detections using confidence, distance, IoU, and motion prediction."""

    def __init__(
        self,
        fps: float = 60.0,
        high_threshold: float = 0.45,
        low_threshold: float = 0.12,
        max_gap: int = 5,
        max_jump_px: float = 180.0,
    ) -> None:
        self.fps = fps
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.max_gap = max_gap
        self.max_jump_px = max_jump_px
        self.kalman = cv2.KalmanFilter(4, 2)
        self.kalman.transitionMatrix = np.array(
            [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32
        )
        self.kalman.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        self.kalman.processNoiseCov = np.eye(4, dtype=np.float32) * 0.025
        self.kalman.measurementNoiseCov = np.eye(2, dtype=np.float32) * 0.08
        self.kalman.errorCovPost = np.eye(4, dtype=np.float32)
        self.initialized = False
        self.missing_gap = 0
        self.last_bbox: tuple[int, int, int, int] | None = None
        self.history: list[AssociatedTrackPoint] = []
        self.rejected_jumps = 0

    def update(self, result: DetectionResult) -> Optional[AssociatedTrackPoint]:
        prediction = self._predict()
        detection, score, rejected = self._associate(result.detections, prediction)

        if detection is not None and not self.initialized:
            self._initialize(detection.cx, detection.cy)
            prediction = (float(detection.cx), float(detection.cy), 0.0, 0.0)

        if not self.initialized:
            return None

        if detection is not None:
            self.missing_gap = 0
            state = self.kalman.correct(np.array([[detection.cx], [detection.cy]], dtype=np.float32))
            self.last_bbox = detection.bbox
            confidence = detection.confidence
            predicted = False
            real_detection = True
        elif self.missing_gap < self.max_gap:
            self.missing_gap += 1
            state = np.array([[prediction[0]], [prediction[1]], [prediction[2]], [prediction[3]]], dtype=np.float32)
            confidence = 0.0
            predicted = True
            real_detection = False
        else:
            self.reset()
            return None

        x, y, vx, vy = [float(value) for value in state[:4, 0]]
        speed_px_s = math.hypot(vx, vy) * self.fps
        direction_deg = math.degrees(math.atan2(-vy, vx)) % 360.0
        point = AssociatedTrackPoint(
            result.frame_id,
            result.timestamp_ms,
            result.camera_id,
            x,
            y,
            vx * self.fps,
            vy * self.fps,
            speed_px_s,
            direction_deg,
            confidence,
            predicted,
            real_detection,
            score,
            rejected,
        )
        self.history.append(point)
        if rejected:
            self.rejected_jumps += 1
        return point

    def draw(self, frame: np.ndarray) -> np.ndarray:
        real_points = [(int(point.x), int(point.y)) for point in self.history if point.real_detection]
        predicted_points = [(int(point.x), int(point.y)) for point in self.history if point.predicted]
        if len(real_points) > 1:
            cv2.polylines(frame, [np.asarray(real_points, dtype=np.int32)], False, (0, 240, 120), 2, cv2.LINE_AA)
        for point in predicted_points[-self.max_gap:]:
            cv2.circle(frame, point, 4, (0, 140, 255), 1, cv2.LINE_AA)
        if self.history:
            latest = self.history[-1]
            color = (0, 140, 255) if latest.predicted else (0, 255, 170)
            center = (int(latest.x), int(latest.y))
            cv2.circle(frame, center, 8, color, 2, cv2.LINE_AA)
            cv2.putText(
                frame,
                f"{latest.speed_px_s:.0f}px/s assoc {latest.association_score:.2f}",
                (center[0] + 12, center[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (240, 240, 240),
                1,
            )
        return frame

    def reset(self) -> None:
        # Re-acquire a fresh track after a long gap, but KEEP the accumulated
        # trajectory so the full delivery path survives detection dropouts (the
        # ball is often detected in only ~half the frames). Without this the
        # history is wiped on every gap and the exported trajectory ends empty.
        saved_history = self.history
        self.__init__(self.fps, self.high_threshold, self.low_threshold, self.max_gap, self.max_jump_px)
        self.history = saved_history

    def _initialize(self, x: float, y: float) -> None:
        state = np.array([[x], [y], [0], [0]], dtype=np.float32)
        self.kalman.statePre = state
        self.kalman.statePost = state
        self.initialized = True

    def _predict(self) -> tuple[float, float, float, float]:
        if not self.initialized:
            return 0.0, 0.0, 0.0, 0.0
        state = self.kalman.predict()
        return float(state[0, 0]), float(state[1, 0]), float(state[2, 0]), float(state[3, 0])

    def _associate(
        self,
        detections: list[BallDetection],
        prediction: tuple[float, float, float, float],
    ) -> tuple[BallDetection | None, float, bool]:
        if not detections:
            return None, 0.0, False
        if not self.initialized:
            best = max(detections, key=lambda item: item.confidence)
            return (best, best.confidence, False) if best.confidence >= self.low_threshold else (None, 0.0, False)

        px, py, _, _ = prediction
        best_item: BallDetection | None = None
        best_score = -1.0
        rejected_jump = False
        for detection in detections:
            if detection.confidence < self.low_threshold:
                continue
            distance = math.hypot(detection.cx - px, detection.cy - py)
            if distance > self.max_jump_px and detection.confidence < self.high_threshold:
                rejected_jump = True
                continue
            distance_score = max(0.0, 1.0 - distance / max(1.0, self.max_jump_px))
            iou_score = _iou(self.last_bbox, detection.bbox)
            score = detection.confidence * 0.56 + distance_score * 0.32 + iou_score * 0.12
            if score > best_score:
                best_item = detection
                best_score = score
        if best_item is None:
            return None, 0.0, rejected_jump
        return best_item, float(best_score), rejected_jump


def _iou(a: tuple[int, int, int, int] | None, b: tuple[int, int, int, int]) -> float:
    if a is None:
        return 0.0
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    return inter / float(area_a + area_b - inter)
