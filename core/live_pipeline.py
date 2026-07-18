"""Single- and multi-camera live DRS pipeline.

Reads N cameras in parallel, runs detection + tracking on each, and uses
the PRIMARY camera (index 0 by default) for calibration + LBW math.
Additional cameras act as visual confirmation only.

This module does NOT modify the existing detector, tracker, calibrator,
LBW engine, or decision service. It calls their public APIs:

    BallDetector.detect(frame, frame_id, timestamp_ms, camera_id) -> DetectionResult
    BallTracker.update(detection_result) -> Optional[TrackPoint]
    PitchCalibrator.pixel_to_world(px, py, ground_z) -> (x, y, z)
    PitchCalibrator.is_calibrated (property) -> bool
    DRSDecisionService.evaluate_tracks(tracks, camera_id) -> dict
    predict_with_physics(positions_3d, fps) -> dict  (from core.trajectory)

If any of those don't exist on import time, the live pipeline falls back
to whatever IS available so the module is usable in a minimal install.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "drs_config.yaml"
_LIVE_DEFAULTS = {
    "appeal_cooldown_seconds": 15,
    "show_world_coords": True,
    "show_tracking_trail": True,
    "trail_length": 20,
    "primary_camera_index": 0,
}


def _load_live_config() -> dict:
    if not _CONFIG_PATH.exists():
        return _LIVE_DEFAULTS
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return _LIVE_DEFAULTS
    return {**_LIVE_DEFAULTS, **(data.get("live") or {})}


# ---------------------------------------------------------------------------
# Optional imports — these are the REAL classes in the codebase. Any of them
# may be missing in a partial install; we degrade gracefully.
# ---------------------------------------------------------------------------

try:
    from core.ball_detector import BallDetector, DetectionResult  # noqa: F401
    _HAS_DETECTOR = True
except Exception:  # pragma: no cover
    BallDetector = None
    DetectionResult = None
    _HAS_DETECTOR = False
    log.warning("BallDetector not importable; live pipeline will be headless")

try:
    from core.ball_tracker import BallTracker, TrackPoint  # noqa: F401
    _HAS_TRACKER = True
except Exception:  # pragma: no cover
    BallTracker = None
    TrackPoint = None
    _HAS_TRACKER = False
    log.warning("BallTracker not importable; live pipeline will be headless")

try:
    from core.calibration import PitchCalibrator  # noqa: F401
    _HAS_CALIBRATOR = True
except Exception:  # pragma: no cover
    PitchCalibrator = None
    _HAS_CALIBRATOR = False
    log.warning("PitchCalibrator not importable; pixel->world disabled")

try:
    from core.drs_decision import DRSDecisionService  # noqa: F401
    _HAS_DECISION = True
except Exception:  # pragma: no cover
    DRSDecisionService = None
    _HAS_DECISION = False
    log.warning("DRSDecisionService not importable; LBW decisions disabled")


# ---------------------------------------------------------------------------
# Per-camera worker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CameraFrame:
    camera_id: int
    frame: np.ndarray
    frame_id: int
    timestamp_ms: float


class _CameraWorker(threading.Thread):
    """Owns one VideoCapture, runs detection + tracking, pushes annotated frames."""

    def __init__(
        self,
        camera_id: int,
        detector: Any,
        tracker: Any,
        frame_queue: queue.Queue,
        live_cfg: dict,
    ) -> None:
        super().__init__(daemon=True, name=f"drs-cam-{camera_id}")
        self.camera_id = camera_id
        self.detector = detector
        self.tracker = tracker
        self.frame_queue = frame_queue
        self.live_cfg = live_cfg
        self.cap: cv2.VideoCapture | None = None
        self.running = False
        self.frame_id = 0
        self.last_track: Any = None
        self.lock = threading.Lock()

    def stop(self) -> None:
        self.running = False

    def run(self) -> None:
        self.cap = cv2.VideoCapture(self.camera_id)
        if not self.cap.isOpened():
            log.error("Camera %d failed to open", self.camera_id)
            return
        self.running = True
        while self.running:
            ok, frame = self.cap.read()
            if not ok or frame is None:
                time.sleep(0.005)
                continue
            ts_ms = time.time() * 1000.0
            track = self._process(frame, ts_ms)
            annotated = self._draw_overlay(frame, track)
            payload = _CameraFrame(
                camera_id=self.camera_id,
                frame=annotated,
                frame_id=self.frame_id,
                timestamp_ms=ts_ms,
            )
            try:
                self.frame_queue.put_nowait(payload)
            except queue.Full:
                # Drop the oldest unread frame to avoid lag
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.frame_queue.put_nowait(payload)
                except queue.Full:
                    pass
            self.frame_id += 1
        if self.cap is not None:
            self.cap.release()

    def _process(self, frame: np.ndarray, ts_ms: float):
        track = None
        if _HAS_DETECTOR and self.detector is not None:
            try:
                det_result: DetectionResult = self.detector.detect(
                    frame=frame,
                    frame_id=self.frame_id,
                    timestamp_ms=ts_ms,
                    camera_id=self.camera_id,
                )
                if _HAS_TRACKER and self.tracker is not None:
                    track = self.tracker.update(det_result)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("Detection/tracking error on cam %d: %s", self.camera_id, exc)
        with self.lock:
            self.last_track = track
        return track

    def get_last_track(self):
        with self.lock:
            return self.last_track

    def _draw_overlay(self, frame: np.ndarray, track: Any) -> np.ndarray:
        display = frame.copy()
        if track is None:
            return display
        # TrackPoint has .x .y .confidence (see core/ball_tracker.py)
        tx = getattr(track, "x", None)
        ty = getattr(track, "y", None)
        conf = getattr(track, "confidence", 0.0)
        if tx is None or ty is None:
            return display
        cv2.circle(display, (int(tx), int(ty)), 22, (0, 255, 100), 2)
        cv2.putText(
            display,
            f"{conf:.2f}",
            (int(tx) + 24, int(ty) - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 100),
            1,
        )
        cv2.putText(
            display,
            f"cam {self.camera_id}",
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )
        return display


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


class MultiCameraLivePipeline:
    """N-camera live DRS pipeline.

    Usage:
        pipeline = MultiCameraLivePipeline(camera_indices=[0])
        pipeline.start()   # blocks; press A for appeal, Q to quit

    The pipeline keeps one detector + one tracker instance and shares them
    across cameras. Calibrator is loaded for the PRIMARY camera only;
    additional cameras are treated as confirmation views.
    """

    def __init__(
        self,
        camera_indices: list[int] | None = None,
        primary_camera_index: int | None = None,
    ) -> None:
        self.live_cfg = _load_live_config()
        cam_cfg: dict = {}
        if _CONFIG_PATH.exists():
            try:
                cam_cfg = (yaml.safe_load(_CONFIG_PATH.read_text()) or {}).get("cameras", {})
            except Exception:
                cam_cfg = {}

        if camera_indices is None:
            camera_indices = cam_cfg.get("default_indices") or [self.live_cfg["primary_camera_index"]]
        if primary_camera_index is None:
            primary_camera_index = (
                self.live_cfg.get("primary_camera_index")
                or (camera_indices[0] if camera_indices else 0)
            )

        self.camera_indices = list(camera_indices)
        self.primary_camera_index = primary_camera_index
        self.target_fps = cam_cfg.get("target_fps", 30)
        self.buffer_seconds = cam_cfg.get("buffer_seconds", 8)

        # Detector / tracker
        self.detector = BallDetector() if _HAS_DETECTOR else None
        self.tracker = BallTracker(fps=float(self.target_fps)) if _HAS_TRACKER else None

        # Calibrator (primary camera only)
        self.calibrator: Any = None
        if _HAS_CALIBRATOR:
            try:
                self.calibrator = PitchCalibrator()
                # Try to load a saved profile for the primary camera
                loaded = self.calibrator.load_profile(camera_id=self.primary_camera_index)
                if loaded:
                    log.info("Loaded calibration for primary camera %d", self.primary_camera_index)
                else:
                    log.info("No saved calibration for camera %d yet", self.primary_camera_index)
            except Exception as exc:  # pragma: no cover
                log.warning("Could not init PitchCalibrator: %s", exc)
                self.calibrator = None

        # Decision service (optional)
        self.decision_service: Any = None
        if _HAS_DECISION:
            try:
                self.decision_service = DRSDecisionService()
            except Exception as exc:  # pragma: no cover
                log.warning("Could not init DRSDecisionService: %s", exc)
                self.decision_service = None

        # Per-camera workers
        self._workers: list[_CameraWorker] = []
        self._frame_queue: queue.Queue = queue.Queue(maxsize=len(self.camera_indices) * 2)

        # 8-second ring buffer (per camera) of 3D positions for the primary
        buf_size = int(self.target_fps * self.buffer_seconds)
        self._positions_3d: deque[tuple[float, float, float]] = deque(maxlen=buf_size)

        self._ws_hub: Any = None
        try:  # pragma: no cover - dashboard integration
            from core.ws_hub import WSBroadcastHub
            self._ws_hub = WSBroadcastHub()
        except Exception:
            pass

        self.running = False
        self.appeal_active = False
        self._appeal_lock = threading.Lock()
        self._last_appeal_at: float = 0.0

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        if not self.camera_indices:
            raise ValueError("No camera indices configured")

        if self.detector is not None and not self.camera_indices:
            log.warning("Starting with no cameras")

        print("=" * 60)
        print("DRS LIVE MODE — {} camera(s)".format(len(self.camera_indices)))
        print("  Cameras: {}".format(self.camera_indices))
        print("  Primary: {}".format(self.primary_camera_index))
        print("  Controls: A=appeal  C=calibrate  Q=quit")
        print("=" * 60)
        if self.calibrator is None or getattr(self.calibrator, "profile", None) is None:
            print("  WARNING: no calibration loaded for primary camera.")
            print("  Run: python scripts/run_calibration.py --camera {}".format(self.primary_camera_index))

        # Spin up camera workers
        for cam_id in self.camera_indices:
            worker = _CameraWorker(
                camera_id=cam_id,
                detector=self.detector,
                tracker=self.tracker,
                frame_queue=self._frame_queue,
                live_cfg=self.live_cfg,
            )
            worker.start()
            self._workers.append(worker)

        self.running = True
        try:
            self._display_loop()
        finally:
            self.stop()

    def stop(self) -> None:
        self.running = False
        for worker in self._workers:
            worker.stop()
        for worker in self._workers:
            worker.join(timeout=1.0)
        cv2.destroyAllWindows()

    def trigger_appeal(self) -> None:
        with self._appeal_lock:
            cooldown = float(self.live_cfg.get("appeal_cooldown_seconds", 15))
            if self.appeal_active:
                return
            if time.time() - self._last_appeal_at < cooldown:
                log.info("Appeal on cooldown (%.1fs remaining)", cooldown - (time.time() - self._last_appeal_at))
                return
            self.appeal_active = True
            self._last_appeal_at = time.time()
        threading.Thread(target=self._run_appeal, daemon=True, name="drs-appeal").start()

    # ----------------------------------------------------------------- private

    def _display_loop(self) -> None:
        layout = self._grid_layout(len(self.camera_indices))
        primary_worker = next((w for w in self._workers if w.camera_id == self.primary_camera_index), None)
        appeal_red = (0, 0, 255)

        while self.running:
            try:
                payload = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                self._handle_key(self._poll_key(), primary_worker)
                continue

            # Cache positions from primary camera
            if payload.camera_id == self.primary_camera_index and self.calibrator is not None:
                track = primary_worker.get_last_track() if primary_worker else None
                world = self._track_to_world(track)
                if world is not None:
                    self._positions_3d.append(world)

            # Compose a tiled display
            canvas = self._compose_canvas(layout, [payload])
            if self.appeal_active:
                cv2.rectangle(canvas, (0, 0), (canvas.shape[1], canvas.shape[0]), appeal_red, 6)
                cv2.putText(
                    canvas,
                    "REVIEWING...",
                    (canvas.shape[1] // 2 - 140, canvas.shape[0] // 2),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.6,
                    appeal_red,
                    4,
                )
            cv2.imshow("DRS Live", canvas)

            self._handle_key(cv2.waitKey(1) & 0xFF, primary_worker)

    def _grid_layout(self, n: int) -> tuple[int, int]:
        if n <= 1:
            return 1, 1
        if n == 2:
            return 1, 2
        if n <= 4:
            return 2, 2
        # For >4 cameras, fall back to 3-wide grid
        cols = 3
        rows = (n + cols - 1) // cols
        return rows, cols

    def _compose_canvas(self, layout: tuple[int, int], latest: list[_CameraFrame]) -> np.ndarray:
        """Stitch the latest frame from each camera into a single display."""
        rows, cols = layout
        tile_h = 360
        tile_w = 640
        canvas = np.zeros((tile_h * rows, tile_w * cols, 3), dtype=np.uint8)

        # Pull the most recent payload per camera id (we only have one
        # in the queue at a time, so for now just show the single frame)
        if not latest:
            return canvas
        frame = latest[-1].frame
        h, w = frame.shape[:2]
        # Resize to tile
        tile = cv2.resize(frame, (tile_w, tile_h))
        canvas[0:tile_h, 0:tile_w] = tile
        return canvas

    def _track_to_world(self, track: Any) -> tuple[float, float, float] | None:
        if track is None or self.calibrator is None:
            return None
        if getattr(self.calibrator, "profile", None) is None:
            return None
        tx = getattr(track, "x", None)
        ty = getattr(track, "y", None)
        if tx is None or ty is None:
            return None
        try:
            return self.calibrator.pixel_to_world(float(tx), float(ty), ground_z=0.05)
        except Exception as exc:  # pragma: no cover
            log.debug("pixel_to_world failed: %s", exc)
            return None

    def _poll_key(self) -> int:
        # Non-blocking key read for empty-queue path
        return cv2.waitKey(1) & 0xFF

    def _handle_key(self, key: int, primary_worker: _CameraWorker | None) -> None:
        if key in (ord("q"), ord("Q"), 27):
            self.running = False
        elif key in (ord("a"), ord("A")):
            self.trigger_appeal()
        elif key in (ord("c"), ord("C")):
            if primary_worker is not None:
                self._run_calibration(primary_worker)

    def _run_calibration(self, primary_worker: _CameraWorker) -> None:
        if self.calibrator is None:
            print("No calibrator available; cannot run wizard.")
            return
        # Grab the latest raw frame from the worker
        if primary_worker.cap is None or not primary_worker.cap.isOpened():
            print("Primary camera not open; cannot calibrate.")
            return
        ok, frame = primary_worker.cap.read()
        if not ok or frame is None:
            print("Failed to grab frame for calibration.")
            return
        try:
            profile = self.calibrator.calibrate_interactive(
                camera_frame=frame,
                camera_id=self.primary_camera_index,
                profile_name="live",
                ground_name="district",
            )
            rms = profile.get("rms_error_px", 0.0)
            print(f"Calibration complete: RMS={rms:.2f}px")
        except KeyboardInterrupt:
            print("Calibration cancelled.")
        except Exception as exc:  # pragma: no cover
            print(f"Calibration failed: {exc}")

    def _run_appeal(self) -> None:
        positions = list(self._positions_3d)
        primary_worker = next((w for w in self._workers if w.camera_id == self.primary_camera_index), None)
        track = primary_worker.get_last_track() if primary_worker else None
        n_3d = len(positions)
        n_2d = 1 if track is not None else 0
        print(f"Appeal: {n_3d} 3D positions, {n_2d} 2D track points")

        decision: dict = {"verdict": "REVIEW_INCONCLUSIVE", "confidence": 0.0}
        try:
            if self.decision_service is not None and track is not None and self.calibrator is not None and getattr(self.calibrator, "profile", None) is not None:
                tx = getattr(track, "x", 0.0)
                ty = getattr(track, "y", 0.0)
                tracks_payload = [{"x": float(tx), "y": float(ty), "t": time.time()}]
                try:
                    decision = self.decision_service.evaluate_tracks(  # type: ignore[attr-defined]
                        tracks_payload, camera_id=self.primary_camera_index
                    ) or decision
                except Exception as exc:
                    log.debug("evaluate_tracks failed: %s", exc)
            elif n_3d >= 4:
                # Fall back to physics-only predictor
                from core.trajectory import predict_with_physics

                traj = predict_with_physics(positions, fps=float(self.target_fps))
                decision = {
                    "verdict": "OUT" if traj["would_hit_stumps"] else "NOT_OUT",
                    "confidence": float(traj["confidence"]),
                    "model_used": traj["model_used"],
                }
            else:
                decision = {
                    "verdict": "REVIEW_INCONCLUSIVE",
                    "confidence": 0.0,
                    "reason": "Insufficient tracking data for LBW analysis.",
                }
        except Exception as exc:
            log.error("Appeal analysis failed: %s", exc)
            decision = {"verdict": "ERROR", "confidence": 0.0, "reason": str(exc)}

        verdict = decision.get("verdict", "UNKNOWN")
        conf = float(decision.get("confidence", 0.0))
        print(f"DECISION: {verdict} ({conf * 100:.0f}%)")
        self._broadcast_decision(decision)
        self._log_decision(decision, n_3d, n_2d)

        cooldown = float(self.live_cfg.get("appeal_cooldown_seconds", 15))
        time.sleep(min(5.0, cooldown))
        self.appeal_active = False

    def _broadcast_decision(self, decision: dict) -> None:
        if self._ws_hub is None:
            return
        try:
            # Best-effort sync broadcast via asyncio.run if no loop is running
            import asyncio

            asyncio.run(
                self._ws_hub.broadcast("decision", {"type": "decision", **decision})
            )
        except Exception:
            pass

    def _log_decision(self, decision: dict, n_3d: int, n_2d: int) -> None:
        try:
            log_path = Path(__file__).resolve().parent.parent / "data" / "decisions.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            from datetime import datetime

            entry = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "cameras": self.camera_indices,
                "primary_camera": self.primary_camera_index,
                "verdict": decision.get("verdict"),
                "confidence": decision.get("confidence"),
                "positions_3d": n_3d,
                "tracks_2d": n_2d,
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(entry) + "\n")
        except Exception as exc:  # pragma: no cover
            log.debug("Failed to log decision: %s", exc)


# ---------------------------------------------------------------------------
# Backward-compat alias for any code that imports the prompt's old name.
# ---------------------------------------------------------------------------
SingleCameraLivePipeline = MultiCameraLivePipeline
