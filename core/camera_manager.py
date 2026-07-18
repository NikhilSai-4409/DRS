"""Low-latency multi-camera capture, synchronized recording, and replay."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from config.settings import (
    BUFFER_SECONDS,
    CAMERA_IDS,
    CAPTURE_QUEUE_SIZE,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    RECORDINGS_DIR,
    SYNC_TOLERANCE_MS,
    SYNTHETIC_CAMERAS,
    TARGET_FPS,
    VIDEO_CODEC,
    VIDEO_EXT,
)
from utils.helpers import draw_timestamp, timestamp_str
from utils.logger import get_logger

log = get_logger("camera_manager")


@dataclass(slots=True)
class VideoFrame:
    camera_id: int
    frame_id: int
    timestamp_ms: float
    frame: np.ndarray
    captured_at: float = 0.0
    cv2_timestamp: float = 0.0


class CameraWorker(threading.Thread):
    """Dedicated reader thread for one camera with a short output queue."""

    def __init__(self, camera_id: int, buffer_frames: int, synthetic_on_fail: bool = True):
        super().__init__(name=f"camera-{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.synthetic_on_fail = synthetic_on_fail
        self.buffer: deque[VideoFrame] = deque(maxlen=buffer_frames)
        self.output_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=CAPTURE_QUEUE_SIZE)
        self.fps_actual = 0.0
        self.dropped_queue_frames = 0
        self.synthetic = False
        self.reconnect_attempts = 0
        self.last_frame_at = 0.0
        self.last_error = ""
        self._stop_event = threading.Event()
        self._frame_id = 0
        self._capture: Optional[cv2.VideoCapture] = None

    def stop(self) -> None:
        self._stop_event.set()

    def latest(self) -> Optional[VideoFrame]:
        return self.buffer[-1] if self.buffer else None

    def snapshot(self) -> list[VideoFrame]:
        return list(self.buffer)

    def run(self) -> None:
        started = time.perf_counter()
        while not self._stop_event.is_set():
            if not self._open_capture():
                if not self.synthetic_on_fail:
                    # No synthetic fallback: stay alive but produce no frames, so
                    # the camera reports as "not connected". Retry quietly in case
                    # a camera is plugged in later.
                    self.synthetic = False
                    self.last_error = "not_connected"
                    if self._stop_event.wait(1.5):
                        break
                    continue
                log.warning("Camera {} unavailable; using synthetic feed while retrying", self.camera_id)
                self.synthetic = True
                self._run_synthetic_until_retry(started)
                continue

            self.synthetic = False
            while not self._stop_event.is_set() and self._capture is not None:
                ok, frame = self._capture.read()
                if not ok:
                    self.last_error = "read_failed"
                    log.warning("Camera {} read failed; reconnecting", self.camera_id)
                    self._release_capture()
                    break
                self._ingest(frame, started, self._capture.get(cv2.CAP_PROP_POS_MSEC))

        self._release_capture()

    def _open_capture(self) -> bool:
        self.reconnect_attempts += 1
        self._release_capture()
        self._capture = cv2.VideoCapture(self.camera_id, cv2.CAP_ANY)
        if not self._capture.isOpened():
            self.last_error = "open_failed"
            self._release_capture()
            return False
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        self._capture.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.last_error = ""
        return True

    def _release_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def _run_synthetic(self) -> None:
        started = time.perf_counter()
        self._run_synthetic_until_retry(started, retry_camera=False)

    def _run_synthetic_until_retry(self, started: float, retry_camera: bool = True) -> None:
        interval = 1.0 / TARGET_FPS
        next_retry = time.perf_counter() + 2.0
        color = [(42, 92, 180), (42, 150, 80), (170, 72, 72)][self.camera_id % 3]
        while not self._stop_event.is_set():
            t = time.perf_counter() - started
            frame = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), color, dtype=np.uint8)
            x = int(FRAME_WIDTH * (0.15 + 0.7 * ((np.sin(t * 2.0) + 1.0) / 2.0)))
            y = int(FRAME_HEIGHT * (0.30 + 0.35 * ((np.cos(t * 2.8) + 1.0) / 2.0)))
            cv2.circle(frame, (x, y), 13, (245, 245, 245), -1, cv2.LINE_AA)
            cv2.putText(frame, f"SYNTHETIC CAM {self.camera_id}", (24, 86), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
            self._ingest(frame, started, t * 1000.0)
            if retry_camera and time.perf_counter() >= next_retry:
                return
            time.sleep(interval)

    def _ingest(self, frame: np.ndarray, started: float, cv2_timestamp: float) -> None:
        timestamp_ms = time.time() * 1000.0
        captured_at = time.perf_counter()
        stamped = draw_timestamp(frame, timestamp_ms, self.camera_id)
        item = VideoFrame(self.camera_id, self._frame_id, timestamp_ms, stamped, captured_at, cv2_timestamp)
        self._frame_id += 1
        self.last_frame_at = timestamp_ms
        self.buffer.append(item)
        elapsed = max(0.001, time.perf_counter() - started)
        self.fps_actual = self._frame_id / elapsed
        try:
            self.output_queue.put_nowait(item)
        except queue.Full:
            self.dropped_queue_frames += 1


class SyncVideoWriter:
    """OpenCV VideoWriter wrapper, one file per synchronized camera."""

    def __init__(self, camera_ids: list[int], out_dir: Path, fps: float = TARGET_FPS):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._writers: dict[int, cv2.VideoWriter] = {}
        fourcc = cv2.VideoWriter_fourcc(*VIDEO_CODEC)
        for camera_id in camera_ids:
            path = out_dir / f"cam_{camera_id}{VIDEO_EXT}"
            self._writers[camera_id] = cv2.VideoWriter(str(path), fourcc, fps, (FRAME_WIDTH, FRAME_HEIGHT))

    def write(self, item: VideoFrame) -> None:
        writer = self._writers.get(item.camera_id)
        if writer is not None:
            writer.write(item.frame)

    def release(self) -> None:
        for writer in self._writers.values():
            writer.release()


class Synchronizer:
    """Aligns the latest frame from each camera by wall-clock timestamp."""

    def __init__(self, tolerance_ms: float = SYNC_TOLERANCE_MS):
        self.tolerance_ms = tolerance_ms

    def align_latest(self, frames: dict[int, VideoFrame]) -> tuple[dict[int, VideoFrame], dict[str, float]]:
        if not frames:
            return {}, {"spread_ms": 0.0, "aligned": 0.0}
        reference = min(item.timestamp_ms for item in frames.values())
        aligned = {
            camera_id: item
            for camera_id, item in frames.items()
            if abs(item.timestamp_ms - reference) <= self.tolerance_ms
        }
        spread = max(item.timestamp_ms for item in frames.values()) - reference
        return aligned, {"spread_ms": spread, "aligned": float(len(aligned) == len(frames))}


class ReplayController:
    """Buffered replay controller with play, pause, slow motion, and stepping."""

    def __init__(self, buffers: dict[int, list[VideoFrame]], fps: float = TARGET_FPS):
        self.buffers = buffers
        self.fps = fps
        self.cursor = 0
        self.speed = 1.0
        self.playing = False
        self.total_frames = max((len(buffer) for buffer in buffers.values()), default=0)
        self.updated_at = time.perf_counter()
        self._lock = threading.Lock()

    def play(self, speed: float = 1.0) -> None:
        with self._lock:
            self.speed = max(0.05, min(4.0, speed))
            self.playing = True
            self.updated_at = time.perf_counter()

    def pause(self) -> None:
        with self._lock:
            self.playing = False
            self.updated_at = time.perf_counter()

    def step(self, delta: int) -> None:
        with self._lock:
            self.cursor = max(0, min(self.total_frames - 1, self.cursor + delta))
            self.updated_at = time.perf_counter()

    def seek(self, frame_index: int) -> None:
        with self._lock:
            self.cursor = max(0, min(self.total_frames - 1, frame_index))
            self.updated_at = time.perf_counter()

    def current_frames(self) -> dict[int, VideoFrame]:
        with self._lock:
            index = self.cursor
        return {camera_id: frames[index] for camera_id, frames in self.buffers.items() if index < len(frames)}

    def tick(self) -> None:
        with self._lock:
            if not self.playing or self.total_frames <= 0:
                return
            now = time.perf_counter()
            elapsed = now - self.updated_at
            advance = int(elapsed * self.fps * self.speed)
            if advance <= 0:
                return
            self.updated_at = now
            self.cursor += advance
            if self.cursor >= self.total_frames:
                self.cursor = max(0, self.total_frames - 1)
                self.playing = False

    def frame_delay_ms(self) -> int:
        return max(1, int((1000.0 / self.fps) / self.speed))


class CameraManager:
    """Facade for capture, replay buffering, synchronized recording, and health."""

    def __init__(self, camera_ids: Optional[list[int]] = None, record: bool = False,
                 synthetic_on_fail: Optional[bool] = None):
        self.camera_ids = camera_ids or CAMERA_IDS
        self.record = record
        self.synthetic_on_fail = SYNTHETIC_CAMERAS if synthetic_on_fail is None else synthetic_on_fail
        self.workers: dict[int, CameraWorker] = {}
        self.writer: Optional[SyncVideoWriter] = None
        self.synchronizer = Synchronizer()
        self.recording_dir: Optional[Path] = None
        self._sync_log_counter = 0

    def start(self) -> None:
        buffer_frames = int(BUFFER_SECONDS * TARGET_FPS)
        for camera_id in self.camera_ids:
            if camera_id in self.workers and self.workers[camera_id].is_alive():
                continue
            worker = CameraWorker(camera_id, buffer_frames, synthetic_on_fail=self.synthetic_on_fail)
            self.workers[camera_id] = worker
            worker.start()
        if self.record:
            self.recording_dir = RECORDINGS_DIR / timestamp_str()
            self.writer = SyncVideoWriter(self.camera_ids, self.recording_dir)

    def stop(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=2.0)
        if self.writer:
            self.writer.release()
            self.writer = None

    def latest_frames(self, write_recording: bool = True) -> dict[int, VideoFrame]:
        frames = {camera_id: worker.latest() for camera_id, worker in self.workers.items()}
        frames = {camera_id: item for camera_id, item in frames.items() if item is not None}
        aligned, _ = self.synchronizer.align_latest(frames)
        self._log_sync_delta(frames)
        if self.writer and write_recording:
            for item in aligned.values():
                self.writer.write(item)
        return frames

    def latest_aligned_frames(self, write_recording: bool = False) -> dict[int, VideoFrame]:
        frames = {camera_id: worker.latest() for camera_id, worker in self.workers.items()}
        frames = {camera_id: item for camera_id, item in frames.items() if item is not None}
        aligned, _ = self.synchronizer.align_latest(frames)
        self._log_sync_delta(frames)
        if self.writer and write_recording:
            for item in aligned.values():
                self.writer.write(item)
        return aligned

    def create_replay(self) -> ReplayController:
        return ReplayController({camera_id: worker.snapshot() for camera_id, worker in self.workers.items()})

    def save_replay(self, out_dir: Optional[Path] = None) -> Path:
        out_dir = out_dir or (RECORDINGS_DIR / f"replay_{timestamp_str()}")
        writer = SyncVideoWriter(self.camera_ids, out_dir)
        try:
            replay = self.create_replay()
            for index in range(replay.total_frames):
                replay.seek(index)
                for item in replay.current_frames().values():
                    writer.write(item)
        finally:
            writer.release()
        return out_dir

    def export_replay(self, output_path: str | Path | None = None) -> str:
        """Export current replay buffer to a video file."""
        output_path = Path(output_path) if output_path else (RECORDINGS_DIR / f"replay_{timestamp_str()}.mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        writer = SyncVideoWriter(self.camera_ids, output_path.parent)
        try:
            replay = self.create_replay()
            for index in range(replay.total_frames):
                replay.seek(index)
                for item in replay.current_frames().values():
                    writer.write(item)
        finally:
            writer.release()
        return str(output_path)

    def health(self) -> dict[int, dict[str, float]]:
        now_ms = time.time() * 1000.0
        result = {}
        for camera_id, worker in self.workers.items():
            fps = worker.fps_actual
            drops = float(worker.dropped_queue_frames)
            alive = worker.is_alive()
            age_ms = max(0.0, now_ms - worker.last_frame_at) if worker.last_frame_at else 999999.0
            # Health score: 1.0 = perfect, 0.0 = dead
            health_score = 1.0
            if not alive:
                health_score = 0.0
            else:
                if fps < 10:
                    health_score -= 0.3
                if drops > 100:
                    health_score -= 0.2
                if age_ms > 2000:
                    health_score -= 0.3
                if worker.synthetic:
                    health_score -= 0.1
            result[camera_id] = {
                "fps": fps,
                "buffered_frames": float(len(worker.buffer)),
                "dropped_queue_frames": drops,
                "synthetic": float(worker.synthetic),
                "reconnect_attempts": float(worker.reconnect_attempts),
                "last_frame_age_ms": age_ms,
                "alive": float(alive),
                "health_score": max(0.0, min(1.0, health_score)),
                "connected": float(alive and fps > 0),
                "last_error": worker.last_error,
            }
        return result

    def restart_camera(self, camera_id: int) -> None:
        """Restart a specific camera worker."""
        old_worker = self.workers.get(camera_id)
        if old_worker is not None:
            old_worker.stop()
            old_worker.join(timeout=2.0)
        buffer_frames = int(BUFFER_SECONDS * TARGET_FPS)
        new_worker = CameraWorker(camera_id, buffer_frames, synthetic_on_fail=self.synthetic_on_fail)
        self.workers[camera_id] = new_worker
        new_worker.start()
        log.info("Camera {} restarted", camera_id)

    def _log_sync_delta(self, frames: dict[int, VideoFrame]) -> None:
        if len(frames) < 2:
            return
        self._sync_log_counter += 1
        if self._sync_log_counter % 30 != 0:
            return
        timestamps = [item.captured_at for item in frames.values()]
        delta = max(timestamps) - min(timestamps)
        message = "[DRS] Multi-camera sync delta: {:.1f}ms".format(delta * 1000.0)
        if delta > 0.050:
            log.warning("{} -- camera_sync gate FAIL", message)
        else:
            log.info(message)

