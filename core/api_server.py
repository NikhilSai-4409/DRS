"""FastAPI bridge between the Python DRS backend and Electron dashboard."""

from __future__ import annotations

import asyncio
import argparse
import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from config.settings import BUFFER_SECONDS, CAMERA_IDS, DATA_DIR, RECORDINGS_DIR, TARGET_FPS
from core import activity_log
from core.camera_manager import CameraManager, ReplayController, VideoFrame
from core.camera_roles import normalize_roles
from core.frame_buffer import FrameBuffer
from core.integration import DRSPipeline, PipelineState
from core.overlay_builder import build_overlay_payload
from core.pitch_calibration import calibration_status_payload
from core.review_logger import ReviewLogger
from core.review_modules import ReviewContext, build_review_result, run_review
from core.review_engine import ReviewEngine
from core.synchronization import SyncVerifier
from utils.logger import get_logger

log = get_logger("api_server")
SESSION_PATH = DATA_DIR / "decisions" / "desktop_session.json"
MATCHES_DIR = DATA_DIR / "matches"  # Session History: one archived match per file.


def _compute_code_version() -> str:
    """Content hash of the backend Python sources, captured once at process start.
    The Electron launcher (main.js) computes the SAME hash from disk on every launch;
    a mismatch means a stale backend (old code) is still bound to port 8765, so it
    kills and respawns it. Keep the file set + ordering in sync with main.js."""
    root = Path(__file__).resolve().parent.parent
    core_files = sorted((root / "core").rglob("*.py"), key=lambda p: p.relative_to(root).as_posix())
    ordered = core_files + [root / "drs_app.py", root / "config" / "settings.py"]
    digest = hashlib.sha1()
    for path in ordered:
        try:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        except OSError:
            continue
    return digest.hexdigest()[:16]


CODE_VERSION = _compute_code_version()


# Pre-match operator checklist thresholds. The checklist gates a match on these
# so the operator sees one green "Match ready" line instead of chasing telemetry
# across panels during a live over.
PREFLIGHT_MIN_FPS = 24.0            # per-camera capture floor for a clean review
PREFLIGHT_WARN_FPS = 15.0          # below this = unstable, hard fail
PREFLIGHT_MIN_STORAGE_GB = 20.0    # comfortable recording headroom
PREFLIGHT_WARN_STORAGE_GB = 5.0    # below this = free up disk before recording
PREFLIGHT_REPLAY_READY_FRACTION = 0.5  # ring buffer at least half full = replay-ready


APPEAL_PRESETS = {
    "NO_BALL": {
        "label": "No Ball",
        "big_camera_index": 2,
        "small_camera_indices": [0, 1],
        "needs_audio": False,
    },
    "LBW": {
        "label": "LBW",
        "big_camera_index": 0,
        "small_camera_indices": [2, 3],
        "needs_audio": False,
    },
    "EDGE": {
        "label": "Edge",
        "big_camera_index": 0,
        "small_camera_indices": [1],
        "needs_audio": True,
    },
}


def _calibration_quality(error_cm: float | None) -> dict:
    """Map a homography reprojection error (cm) to a star rating + operator label."""
    if error_cm is None:
        return {"stars": 0, "label": "Not calibrated", "level": "missing"}
    if error_cm < 1.0:
        return {"stars": 5, "label": "Excellent", "level": "excellent"}
    if error_cm < 2.0:
        return {"stars": 4, "label": "Good", "level": "good"}
    if error_cm < 3.5:
        return {"stars": 3, "label": "Fair", "level": "fair"}
    if error_cm < 5.0:
        return {"stars": 2, "label": "Needs recalibration", "level": "warn"}
    return {"stars": 1, "label": "Poor — redo capture", "level": "poor"}


class DRSBackend:
    """Owns camera capture, replay snapshots, sync verification, and API state."""

    def __init__(self, camera_ids: list[int], record: bool = False):
        self.camera_ids = camera_ids
        self.camera_manager = CameraManager(camera_ids, record=record)
        # Single synchronized source of frames for every review type.
        self.frame_buffer = FrameBuffer(self.camera_manager)
        # Auto-saves each review (json + replay + frames) to data/reviews/.
        self.review_logger = ReviewLogger()
        self.sync_verifier = SyncVerifier()
        self.active_replay: Optional[ReplayController] = None
        self.started_at_ms = time.time() * 1000.0
        self.analysis_mode = {"id": "visible", "label": "Mode A - visible-spectrum approximation"}
        self.current_decision = self._waiting_decision()
        # Match-level state (survives restart): name/teams/overs + review history.
        # `self.reviews` is a live view onto the current match's reviews.
        self.match: dict = self._new_match()
        # Real-time detection/tracking pipeline integration
        self.pipeline = DRSPipeline(camera_ids, record=False, detector=None)
        self.pipeline_state: Optional[PipelineState] = None
        self._last_detection: Optional[dict] = None
        self._load_session()

    def start(self) -> None:
        self.camera_manager.start()
        # Share camera_manager feeds with the pipeline (avoid double-opening)
        self.pipeline.camera_manager = self.camera_manager
        self.pipeline.running = True
        log.info("API backend started with cameras {}", self.camera_ids)
        activity_log.record("backend_started", "DRS backend started",
                            cameras=list(self.camera_ids))
        # UltraEdge capture: best-effort — a missing/busy microphone must never block
        # the video backend. Preflight and /api/audio/edge report the honest state.
        # Buffer matches the video replay window so an appeal can query the whole
        # delivery; audio chunks share the video frames' wall-clock milliseconds.
        self.audio_pipeline = None
        try:
            from core.audio_pipeline import UltraEdgeAudioPipeline

            pipeline = UltraEdgeAudioPipeline(buffer_seconds=float(BUFFER_SECONDS))
            pipeline.start_capture()
            if getattr(pipeline.analyzer, "running", False):
                self.audio_pipeline = pipeline
                activity_log.record("audio_started", "UltraEdge audio capture started")
                log.info("UltraEdge audio capture running ({} Hz)", pipeline.sample_rate)
            else:
                log.warning("UltraEdge audio: no input device — edge evidence disabled")
        except Exception as exc:
            log.warning("UltraEdge audio unavailable: {}", exc)
        # Sound classifier (bat/pad/glove/ground/stump/noise) — present only after
        # scripts/train_ultraedge.py has been run on labeled nets recordings.
        self.audio_classifier = None
        try:
            from core.audio_classifier import UltraEdgeClassifier

            classifier = UltraEdgeClassifier()
            if classifier.available:
                self.audio_classifier = classifier
        except Exception as exc:
            log.warning("UltraEdge classifier unavailable: {}", exc)

    def stop(self) -> None:
        self._save_session()
        if getattr(self, "audio_pipeline", None) is not None:
            try:
                self.audio_pipeline.stop_capture()
            except Exception:
                pass
        self.camera_manager.stop()
        log.info("API backend stopped")

    def audio_edge_for_window(self, start_ms: float, end_ms: float, total_frames: int) -> dict:
        """Scan the captured audio across a replay window for snick transients.

        Coarse 0.5 s stride with the analyzer's 3-sigma band-limited detector;
        events are deduped (<120 ms apart = one contact) and mapped to replay frame
        indices so the UI's spike timeline can seek straight to them."""
        analyzer = self.audio_pipeline.analyzer
        classifier = getattr(self, "audio_classifier", None)
        events: list[dict] = []
        span = max(1.0, end_ms - start_ms)
        t = start_ms
        while t <= end_ms:
            r = analyzer.detect_edge_at(t)
            if r.has_edge:
                if not events or abs(r.edge_timestamp_ms - events[-1]["timestamp_ms"]) > 120.0:
                    event = {
                        "timestamp_ms": round(r.edge_timestamp_ms, 1),
                        "frame_id": int(max(0, min(total_frames - 1,
                                                   (r.edge_timestamp_ms - start_ms) / span * total_frames))),
                        "confidence": round(float(r.edge_confidence), 3),
                    }
                    if classifier is not None:
                        window = analyzer.get_waveform_window(r.edge_timestamp_ms, duration_s=0.3)
                        info = classifier.classify(window.samples, window.sample_rate)
                        if info:
                            event.update(info)
                    events.append(event)
            t += 500.0
        # With a trained classifier, only BAT-labeled spikes count as edge evidence
        # (ambient/speech/ground spikes are real sounds but not bat involvement).
        # Without one, every transient counts — honest, but unfiltered.
        if classifier is not None:
            probability = max((e["label_confidence"] for e in events if e.get("is_bat")), default=0.0)
            bat_count = sum(1 for e in events if e.get("is_bat"))
            reason = (f"{bat_count} bat sound(s) among {len(events)} transient(s)"
                      if events else "no snick-band transient in the captured window")
        else:
            probability = max((e["confidence"] for e in events), default=0.0)
            reason = (f"{len(events)} transient(s) in the {span / 1000.0:.1f}s window (unclassified — train the sound model)"
                      if events else "no snick-band transient in the captured window")
        return {
            "available": True,
            "source": "microphone",
            "classified": classifier is not None,
            "inconclusive": False,
            "edge_probability": probability,
            "events": events,
            "window_s": round(span / 1000.0, 2),
            "reason": reason,
        }

    def health(self) -> dict:
        frames = self.camera_manager.latest_frames(write_recording=False)
        sync_report = self.sync_verifier.evaluate(frames)
        camera_health = self.camera_manager.health()
        return {
            "status": "ok",
            "camera_ids": self.camera_ids,
            "health": camera_health,
            "sync": asdict(sync_report),
            "started_at_ms": self.started_at_ms,
            "uptime_seconds": int((time.time() * 1000.0 - self.started_at_ms) / 1000.0),
            "timestamp_ms": time.time() * 1000.0,
            "active_model_name": "live-camera-backend",
            "code_version": CODE_VERSION,
        }

    def latest_frame(self, camera_id: int) -> VideoFrame:
        frames = self.camera_manager.latest_frames(write_recording=False)
        if camera_id not in frames:
            raise KeyError(camera_id)
        return frames[camera_id]

    def create_replay(self) -> dict:
        self.active_replay = self.camera_manager.create_replay()
        timestamps = []
        for buffer in self.active_replay.buffers.values():
            timestamps.extend(item.timestamp_ms for item in buffer)
        return {
            "total_frames": self.active_replay.total_frames,
            "camera_ids": sorted(self.active_replay.buffers.keys()),
            "start_timestamp_ms": min(timestamps) if timestamps else None,
            "end_timestamp_ms": max(timestamps) if timestamps else None,
        }

    def replay_state(self) -> dict:
        if self.active_replay is None:
            meta = self.create_replay()
        else:
            # Same shape as create_replay()'s meta (incl. the capture-time window) so
            # readers can attach to the EXISTING frozen snapshot — e.g. Sync Replay
            # reuses the review's timeline instead of clobbering it with a new one.
            timestamps = []
            for buffer in self.active_replay.buffers.values():
                timestamps.extend(item.timestamp_ms for item in buffer)
            meta = {
                "total_frames": self.active_replay.total_frames,
                "camera_ids": sorted(self.active_replay.buffers.keys()),
                "start_timestamp_ms": min(timestamps) if timestamps else None,
                "end_timestamp_ms": max(timestamps) if timestamps else None,
            }
        assert self.active_replay is not None
        self.active_replay.tick()
        return {
            **meta,
            "cursor": self.active_replay.cursor,
            "playing": self.active_replay.playing,
            "speed": self.active_replay.speed,
            "fps": self.active_replay.fps,
        }

    def replay_frame(self, camera_id: int, frame_index: int | None, timestamp_ms: float | None) -> VideoFrame:
        if self.active_replay is None:
            self.create_replay()
        assert self.active_replay is not None
        buffer = self.active_replay.buffers.get(camera_id, [])
        if not buffer:
            raise KeyError(camera_id)
        if timestamp_ms is not None:
            return min(buffer, key=lambda item: abs(item.timestamp_ms - timestamp_ms))
        index = 0 if frame_index is None else max(0, min(len(buffer) - 1, frame_index))
        return buffer[index]

    def replay_frames(self, camera_ids: list[int], frame_index: int | None, timestamp_ms: float | None) -> dict:
        if self.active_replay is None:
            meta = self.create_replay()
        else:
            meta = {
                "total_frames": self.active_replay.total_frames,
                "camera_ids": sorted(self.active_replay.buffers.keys()),
            }
        assert self.active_replay is not None
        reference_timestamp_ms = timestamp_ms
        if reference_timestamp_ms is None and frame_index is not None and camera_ids:
            reference = self.replay_frame(camera_ids[0], frame_index, None)
            reference_timestamp_ms = reference.timestamp_ms

        frames = {}
        for camera_id in camera_ids:
            try:
                item = self.replay_frame(camera_id, frame_index, reference_timestamp_ms)
            except KeyError:
                continue
            frames[str(camera_id)] = {
                "camera_id": item.camera_id,
                "frame_id": item.frame_id,
                "timestamp_ms": item.timestamp_ms,
                "delta_ms": 0.0 if reference_timestamp_ms is None else item.timestamp_ms - reference_timestamp_ms,
                "image_url": f"/api/replay/{item.camera_id}.jpg?timestamp_ms={item.timestamp_ms}",
            }
        return {
            "replay": meta,
            "reference_timestamp_ms": reference_timestamp_ms,
            "frames": frames,
        }

    def status_events(self) -> list[dict]:
        health = self.health()
        events: list[dict] = [
            {"type": "camera_health", **health},
            {"type": "sync_report", "sync": health.get("sync", {}), "timestamp_ms": health.get("timestamp_ms")},
        ]

        # ball_detected events from latest pipeline tick
        if self._last_detection is not None:
            events.append({"type": "ball_detected", **self._last_detection})

        # trajectory_update from current decision trajectory
        trajectory = self.current_decision.get("trajectory", [])
        if trajectory:
            events.append({"type": "trajectory_update", "trajectory": trajectory, "point_count": len(trajectory)})

        # decision_update with current decision state
        events.append({"type": "decision_update", "decision": self.current_decision})

        # calibration_status with calibration quality
        events.append({"type": "calibration_status", "calibration": calibration_status_payload()})

        return events

    def camera_status(self) -> dict:
        health = self.camera_manager.health()
        cameras = []
        now = time.time() * 1000.0
        for camera_id in self.camera_ids:
            item = health.get(camera_id, {})
            fps = float(item.get("fps", 0.0))
            buffered = int(item.get("buffered_frames", 0.0))
            connected = buffered > 0
            latency_ms = 0.0
            latest = self.camera_manager.workers.get(camera_id).latest() if camera_id in self.camera_manager.workers else None
            if latest is not None:
                latency_ms = max(0.0, now - latest.timestamp_ms)
            score = max(0.0, min(1.0, (fps / 24.0) * 0.65 + (1.0 if connected else 0.0) * 0.35))
            status = "online" if score >= 0.75 else "warn" if connected else "offline"
            cameras.append(
                {
                    "id": camera_id,
                    "connected": connected,
                    "status": status,
                    "fps": round(fps, 2),
                    "latency_ms": round(latency_ms, 1),
                    "dropped_frames": int(item.get("dropped_queue_frames", 0.0)),
                    "synthetic": bool(item.get("synthetic", 0.0)),
                    "reconnect_attempts": int(item.get("reconnect_attempts", 0.0)),
                    "last_frame_age_ms": round(float(item.get("last_frame_age_ms", 0.0)), 1),
                    "health_score": round(score, 3),
                }
            )
        return {"cameras": cameras, "mode": self.analysis_mode, "max_cameras": len(self.camera_ids)}

    def live_payload(self, include_frames: bool = True) -> dict:
        payload = {"type": "live", **self.camera_status(), "timestamp_ms": time.time() * 1000.0}
        if include_frames:
            frames = {}
            for camera_id, item in self.camera_manager.latest_frames(write_recording=False).items():
                encoded = encode_jpeg(item, quality=58)
                frames[str(camera_id)] = {
                    "camera_id": camera_id,
                    "frame_id": item.frame_id,
                    "timestamp_ms": item.timestamp_ms,
                    "jpeg_base64": base64.b64encode(encoded).decode("ascii"),
                }
            payload["frames"] = frames
        return payload

    def system_health(self) -> dict:
        camera_status = self.camera_status()
        camera_fps = {str(item["id"]): item["fps"] for item in camera_status["cameras"]}
        frame_drops = {str(item["id"]): item["dropped_frames"] for item in camera_status["cameras"]}
        latencies = [item["latency_ms"] for item in camera_status["cameras"] if item["connected"]]
        payload = {
            "cpu_percent": _cpu_percent(),
            "ram_percent": _ram_percent(),
            "gpu": _gpu_status(),
            "camera_fps": camera_fps,
            "frame_drops": frame_drops,
            "latency_ms": round(max(latencies, default=0.0), 1),
            "storage": {"free_gb": _free_gb(RECORDINGS_DIR)},
            "network": {"status": "local"},
            "camera_health": camera_status["cameras"],
            "calibration": calibration_status_payload(),
            "timestamp_ms": time.time() * 1000.0,
        }
        return payload

    def preflight_checklist(
        self,
        selected_cameras: list[int] | None = None,
        require_audio: bool = False,
        require_gpu: bool = False,
    ) -> dict:
        """Tournament pre-match checklist.

        Every item is auto-verified against live telemetry and rated
        ``pass`` / ``warn`` / ``fail`` (``skip`` = not applicable). ``match_ready``
        is True when no *required* item is failing. The operator chooses which
        cameras are in use, so spare/unused indices never block readiness — that is
        the "select cams available" control the dashboard renders one row per.
        """
        detected = list(self.camera_ids)
        selected = [cid for cid in (selected_cameras or detected) if cid in detected]
        if not selected:
            selected = list(detected)
        # Stable A/B/C… labels follow detection order, matching the operator's
        # "Camera A/B/C" mental model while keeping the raw index in the detail.
        letters = {cid: (chr(ord("A") + idx) if idx < 26 else str(cid)) for idx, cid in enumerate(detected)}

        health = self.camera_manager.health()
        buffer_capacity = max(1, int(BUFFER_SECONDS * TARGET_FPS))
        items: list[dict] = []
        fps_values: list[float] = []
        replay_fractions: list[float] = []

        # --- One connection row per selected camera ---
        for cid in selected:
            item = health.get(cid, {})
            fps = float(item.get("fps", 0.0))
            connected = float(item.get("connected", 0.0)) > 0 or (float(item.get("alive", 0.0)) > 0 and fps > 0)
            buffered = int(item.get("buffered_frames", 0.0))
            synthetic = bool(item.get("synthetic", 0.0))
            fps_values.append(fps)
            replay_fractions.append(min(1.0, buffered / buffer_capacity))
            if connected:
                status = "warn" if synthetic else "pass"
                detail = f"{fps:.1f} fps — {'synthetic source (no live signal)' if synthetic else 'live'}"
            else:
                status = "fail"
                detail = item.get("last_error") or "No frames — check USB / capture card / cable."
            items.append(_pf_item(
                f"camera_{cid}", f"Camera {letters.get(cid, cid)} connected", "cameras",
                status, detail, required=True,
                value={"camera_id": cid, "fps": round(fps, 2), "synthetic": synthetic},
            ))

        # --- FPS stable (aggregate across selected cameras) ---
        if fps_values:
            min_fps = min(fps_values)
            if min_fps >= PREFLIGHT_MIN_FPS:
                fps_status = "pass"
                fps_detail = f"All selected cameras ≥ {PREFLIGHT_MIN_FPS:.0f} fps (lowest {min_fps:.1f})."
            elif min_fps >= PREFLIGHT_WARN_FPS:
                fps_status = "warn"
                fps_detail = f"Lowest camera {min_fps:.1f} fps — below {PREFLIGHT_MIN_FPS:.0f} fps target."
            else:
                fps_status = "fail"
                fps_detail = f"Lowest camera {min_fps:.1f} fps — capture unstable."
        else:
            fps_status, fps_detail = "fail", "No camera FPS available yet."
        items.append(_pf_item("fps_stable", "FPS stable", "capture", fps_status, fps_detail,
                              required=True, value={"min_fps": round(min(fps_values), 2) if fps_values else 0.0}))

        # --- Calibration valid ---
        calib = calibration_status_payload()
        calibrated_ids = set(calib.get("camera_ids") or [])
        uncalibrated = [cid for cid in selected if cid not in calibrated_ids]
        if not calib.get("calibrated"):
            cal_status = "fail"
            cal_detail = "No calibration profiles. Run pitch calibration on the match cameras."
        elif uncalibrated:
            cal_status = "warn"
            cal_detail = f"No profile for camera(s): {', '.join(str(letters.get(c, c)) for c in uncalibrated)}."
        elif calib.get("readiness") == "good":
            cal_status = "pass"
            err = calib.get("homography_error_cm")
            cal_detail = "All selected cameras calibrated" + (f" (avg error {err:.2f} cm)." if err else ".")
        else:
            cal_status = "warn"
            cal_detail = "Calibration present but error above target — consider recalibrating."
        items.append(_pf_item("calibration", "Calibration valid", "vision", cal_status, cal_detail,
                              required=True, value={"quality_score": calib.get("quality_score"),
                                                    "error_cm": calib.get("homography_error_cm")}))

        # --- Models loaded ---
        try:
            from core.model_selector import DetectorModelSelector

            _model_path, readiness = DetectorModelSelector().select()
            if readiness.selected_model == "missing":
                model_status, model_detail = "fail", readiness.reason
            elif readiness.usable:
                model_status = "pass"
                model_detail = f"{readiness.selected_model} loaded" + (
                    f" (mAP50 {readiness.map50})." if readiness.map50 is not None else "."
                )
            else:
                model_status, model_detail = "warn", readiness.reason
            model_value = {"model": readiness.selected_model, "path": readiness.model_path, "map50": readiness.map50}
        except Exception as exc:  # noqa: BLE001 - never let a probe crash the checklist
            model_status, model_detail, model_value = "warn", f"Model probe failed: {exc}", {}
        items.append(_pf_item("models", "Models loaded", "vision", model_status, model_detail,
                              required=True, value=model_value))

        # --- Storage available ---
        free_gb = _free_gb(RECORDINGS_DIR)
        if free_gb >= PREFLIGHT_MIN_STORAGE_GB:
            st_status, st_detail = "pass", f"{free_gb:.1f} GB free for recordings."
        elif free_gb >= PREFLIGHT_WARN_STORAGE_GB:
            st_status, st_detail = "warn", f"Only {free_gb:.1f} GB free — enough for a short session."
        else:
            st_status, st_detail = "fail", f"Only {free_gb:.1f} GB free — free up disk before recording."
        items.append(_pf_item("storage", "Storage available", "system", st_status, st_detail,
                              required=True, value={"free_gb": free_gb}))

        # --- Replay buffer ready ---
        if replay_fractions:
            fill = min(replay_fractions)
            if fill >= PREFLIGHT_REPLAY_READY_FRACTION:
                rb_status = "pass"
                rb_detail = f"Ring buffer {fill * 100:.0f}% filled (~{BUFFER_SECONDS}s replay window)."
            elif fill > 0:
                rb_status = "warn"
                rb_detail = f"Buffer filling ({fill * 100:.0f}%) — let cameras run a few more seconds."
            else:
                rb_status, rb_detail = "fail", "Replay buffer empty — no frames captured yet."
        else:
            rb_status, rb_detail = "fail", "No cameras feeding the replay buffer."
        items.append(_pf_item("replay_buffer", "Replay buffer ready", "capture", rb_status, rb_detail,
                              required=True, value={"fill_fraction": round(min(replay_fractions), 3) if replay_fractions else 0.0}))

        # --- Audio connected (optional unless the review type needs it) ---
        audio_started = getattr(self, "audio_pipeline", None) is not None
        if audio_started:
            au_status, au_detail = "pass", "Audio capture running."
        else:
            au_status = "fail" if require_audio else "skip"
            au_detail = "Audio capture not started." + ("" if require_audio else " Not required for this review type.")
        items.append(_pf_item("audio", "Audio connected", "system", au_status, au_detail,
                              required=require_audio, value={"started": audio_started}))

        # --- GPU healthy (CPU fallback exists, so optional by default) ---
        gpu = _gpu_status()
        if gpu.get("available"):
            gpu_status, gpu_detail = "pass", gpu.get("detail", "GPU ready.")
        else:
            gpu_status = "fail" if require_gpu else "warn"
            gpu_detail = gpu.get("detail", "No GPU detected — running on CPU.")
        items.append(_pf_item("gpu", "GPU healthy", "system", gpu_status, gpu_detail,
                              required=require_gpu, value=gpu))

        # --- Database writable (job/session persistence) ---
        db_ok, db_detail = _path_writable(DATA_DIR / "testing")
        items.append(_pf_item(
            "database", "Database writable", "system",
            "pass" if db_ok else "fail",
            "Job/session database directory is writable." if db_ok else db_detail,
            required=True, value={"path": str(DATA_DIR / "testing")},
        ))

        # --- Export folder writable (report/replay outputs) ---
        exports_dir = DATA_DIR / "exports"
        ex_ok, ex_detail = _path_writable(exports_dir)
        items.append(_pf_item(
            "export_folder", "Export folder writable", "system",
            "pass" if ex_ok else "fail",
            "Export directory is writable." if ex_ok else ex_detail,
            required=True, value={"path": str(exports_dir)},
        ))

        # --- Aggregate: Match ready ---
        blocking = [it["key"] for it in items if it["required"] and it["status"] == "fail"]
        warnings = [it["key"] for it in items if it["status"] == "warn"]
        match_ready = not blocking
        summary = {state: sum(1 for it in items if it["status"] == state) for state in ("pass", "warn", "fail", "skip")}
        items.append(_pf_item(
            "match_ready", "Match ready", "summary",
            "pass" if match_ready else "fail",
            "All required checks passed — cleared for live operation."
            if match_ready else f"{len(blocking)} required check(s) failing.",
            required=True, value={"blocking": blocking},
        ))

        return {
            "generated_at_ms": time.time() * 1000.0,
            "cameras_detected": detected,
            "cameras_selected": selected,
            "require_audio": require_audio,
            "require_gpu": require_gpu,
            "items": items,
            "match_ready": match_ready,
            "blocking": blocking,
            "warnings": warnings,
            "summary": summary,
        }

    def request_review(
        self,
        camera_ids: list[int] | None = None,
        review_type: str = "lbw",
        camera_roles: dict | None = None,
        primary_camera_id: int | None = None,
    ) -> dict:
        review_type = str(review_type or "lbw").lower()
        replay = self.create_replay()
        # One synchronized snapshot drives the whole review — every type sees the
        # same frames, timestamps and per-camera sync telemetry.
        snap = self.frame_buffer.snapshot()
        decision = {
            **self._processing_seed(),
            "camera_ids": camera_ids or self.camera_ids,
            "replay": replay,
            "review_type": review_type,
            "explanation": "Review initiated. Live replay buffer captured for operator analysis.",
        }

        # One interface for every review type: LBW / Wide / No Ball / Edge all run
        # through run_review(ctx). Unknown types fall through with the seeded decision.
        try:
            ctx = self._build_review_context(review_type, camera_roles, primary_camera_id, snap)
            analysis = ReviewEngine.execute(review_type, ctx)
            if analysis:
                decision.update(analysis)
        except Exception as exc:
            log.warning("Review analysis failed for {}: {}", review_type, exc)

        # Uniform pipeline tail: identical synchronized telemetry and the same
        # normalised ReviewResult for every review type, so the dashboard renders
        # LBW / Wide / No Ball / Edge through one code path.
        decision["camera_sync"] = snap.sync_report()
        decision["review_result"] = build_review_result(review_type, decision, replay=replay)
        # Project the analytical geometry into the render-ready overlay payload that
        # both the replay video and the live dashboard draw through OverlayRenderer.
        decision["overlay"] = build_overlay_payload(decision, calibrators=self.pipeline.calibrators)

        # Auto-save the whole review (json + replay + key frames) to data/reviews/.
        primary_cam = max(snap.frames, key=lambda cid: len(snap.frames[cid]), default=None)
        replay_frames = snap.frames.get(primary_cam, []) if primary_cam is not None else []
        log_info = self.review_logger.log(
            decision,
            frames=replay_frames,
            calibration=calibration_status_payload(),
            frame_timestamps=snap.timestamps,
        )
        if log_info.get("saved"):
            decision["log"] = {key: log_info[key] for key in ("review_id", "dir", "artifacts") if key in log_info}
            replay_meta = log_info.get("replay") or {}
            if replay_meta.get("available"):
                decision["review_result"]["replay"] = {
                    "path": replay_meta.get("path"),
                    "frame_count": replay_meta.get("frame_count"),
                    "duration_s": replay_meta.get("duration_s"),
                }

        # Real UltraEdge: when the microphone is live, replace the module's no-audio
        # placeholder with actual snick-band analysis over the SAME frozen replay
        # window (events carry frame ids so the spike timeline can seek to them).
        if (getattr(self, "audio_pipeline", None) is not None
                and review_type in {"lbw", "edge", "ultraedge", "ultra_edge", "snicko"}
                and replay.get("start_timestamp_ms") is not None):
            try:
                audio = self.audio_edge_for_window(
                    float(replay["start_timestamp_ms"]),
                    float(replay["end_timestamp_ms"]),
                    int(replay.get("total_frames") or 1),
                )
                decision["edge_analysis"] = audio
                prob = audio["edge_probability"]
                summary = decision.get("summary") or {}
                for row in summary.get("measurements", []):
                    if row.get("label") == "UltraEdge":
                        row["value"] = f"{prob * 100:.0f}% spike" if audio["events"] else "No spike — mic live"
                        row["flag"] = prob >= 0.5
                summary["warnings"] = [w for w in summary.get("warnings", [])
                                       if "UltraEdge inconclusive" not in w]
                if prob >= 0.5:
                    summary["warnings"].append(
                        "UltraEdge spike detected — check bat involvement before confirming OUT.")
            except Exception as exc:
                log.warning("Audio edge analysis failed: {}", exc)

        self.current_decision = decision
        self._save_session()
        activity_log.record(
            "review_requested",
            f"{review_type.upper()} review requested",
            review_type=review_type,
            cameras=decision.get("camera_ids"),
        )
        return {"decision": decision, "replay": replay}

    def _build_review_context(
        self,
        review_type: str,
        camera_roles: dict | None,
        primary_camera_id: int | None,
        snap,
    ) -> ReviewContext:
        primary = None
        if primary_camera_id is not None:
            try:
                primary = int(primary_camera_id)
            except (TypeError, ValueError):
                primary = None
        # Same shared engine the offline path uses — only the frame source differs.
        return ReviewEngine.build_context(
            review_type,
            frames=snap.frames,
            detector=self.pipeline.detector,
            calibrators=self.pipeline.calibrators,
            camera_roles=normalize_roles(camera_roles),
            primary_camera_id=primary,
            timestamps=snap.timestamps,
            telemetry=snap.telemetry_dict(),
            reference_timestamp_ms=snap.reference_timestamp_ms,
        )

    def confirm_decision(self, outcome: str) -> dict:
        # Records the confirmed verdict into the current match's review history and
        # returns it. The RESULT → IDLE transition is a separate step: the operator
        # UI follows this with /api/decision/reset once the verdict is acknowledged.
        # The confirmed payload IS the real analysis produced by request_review —
        # the operator's verdict is stamped onto it, never regenerated.
        status = "OUT" if outcome == "OUT" else "NOT_OUT"
        decision = dict(self.current_decision or {})
        system_recommendation = decision.get("outcome")
        decision["status"] = status
        decision["outcome"] = "OUT" if status == "OUT" else "NOT OUT"
        decision["confirmed_at_ms"] = time.time() * 1000.0
        for step in decision.get("timeline") or []:
            if step.get("status") == "active":
                step["status"] = "complete"
        self.current_decision = decision
        review = {
            "id": f"review_{len(self.reviews) + 1}",
            "time": decision["confirmed_at_ms"],
            "over": f"{len(self.reviews) + 1}.0",
            "type": str(decision.get("review_type") or "lbw").upper(),
            "decision": decision["outcome"],
            "system_recommendation": system_recommendation,
            "confidence": decision.get("overall_confidence"),
            "review_id": (decision.get("log") or {}).get("review_id"),
            "provenance": self._provenance(),
        }
        self.reviews.insert(0, review)
        self._save_session()
        activity_log.record(
            "decision_confirmed",
            f"Decision confirmed: {decision['outcome']}",
            review_type=review.get("type"),
            confidence=review.get("confidence"),
            system_recommendation=system_recommendation,
        )
        return self.current_decision

    def _provenance(self) -> dict:
        """Model + calibration identity captured at confirm time, so every review
        answers \"which model/calibration produced this?\" months later."""
        pipeline = getattr(self, "pipeline", None)
        detector = getattr(pipeline, "detector", None)
        calib = calibration_status_payload()
        return {
            "model": getattr(detector, "active_model_name", "none"),
            "calibrated_camera_ids": calib.get("camera_ids", []),
            "calibration_quality": calib.get("quality_score"),
            "camera_ids": list(self.camera_ids),
        }

    def reset_decision(self) -> dict:
        """Return to IDLE (WAITING). Used to clear the active review after the
        verdict is confirmed/acknowledged, or to abandon a review without a verdict.
        The completed review (if any) already lives in self.reviews."""
        self.current_decision = self._waiting_decision()
        self._save_session()
        return self.current_decision

    def export_replay(self) -> Path:
        if self.active_replay is None:
            self.create_replay()
        assert self.active_replay is not None
        out_dir = RECORDINGS_DIR / f"replay_{int(time.time())}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "drs_replay.mp4"
        first_camera = next(iter(sorted(self.active_replay.buffers)), None)
        if first_camera is None:
            blank = VideoFrame(0, 0, time.time() * 1000.0, _blank_frame())
            frames = [blank]
        else:
            frames = self.active_replay.buffers[first_camera]
        if not frames:
            frames = [VideoFrame(first_camera or 0, 0, time.time() * 1000.0, _blank_frame())]

        h, w = frames[0].frame.shape[:2]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (w, h))
        try:
            for index, item in enumerate(frames):
                frame = item.frame.copy()
                self._draw_replay_overlay(frame, index, len(frames))
                writer.write(frame)
        finally:
            writer.release()
        activity_log.record("replay_exported", "Replay exported", path=str(path))
        return path

    def _draw_replay_overlay(self, frame, index: int, total: int) -> None:
        decision = self.current_decision
        status = decision.get("outcome") or decision.get("status", "WAITING")
        cv2.rectangle(frame, (18, 18), (520, 132), (5, 12, 20), -1)
        cv2.rectangle(frame, (18, 18), (520, 132), (60, 220, 150), 2)
        cv2.putText(frame, f"DRS REPLAY | {status}", (34, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (245, 245, 245), 2)
        cv2.putText(frame, f"Frame {index + 1}/{total} | {decision.get('wicket_zone_status', '--')}", (34, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (80, 230, 255), 2)
        cv2.putText(frame, str(decision.get("explanation", ""))[:58], (34, 116), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 230, 220), 1)
        h, w = frame.shape[:2]
        trajectory = decision.get("trajectory") or []
        if len(trajectory) >= 2:
            pts = []
            for point in trajectory:
                x = int((float(point.get("x", 0.0)) + 8.0) / 16.0 * w)
                y = int(h * 0.72 - float(point.get("z", 0.1)) * h * 0.22 + float(point.get("y", 0.0)) * 80)
                pts.append((max(0, min(w - 1, x)), max(0, min(h - 1, y))))
            cv2.polylines(frame, [np.asarray(pts, dtype=np.int32)], False, (40, 255, 150), 3, cv2.LINE_AA)
            cv2.circle(frame, pts[min(index, len(pts) - 1)], 7, (255, 255, 255), -1, cv2.LINE_AA)

    # ------------------------------------------------------------------ match
    @property
    def reviews(self) -> list[dict]:
        """Live view of the current match's review history (newest first)."""
        return self.match["reviews"]

    def _new_match(
        self,
        name: str | None = None,
        teams: dict | None = None,
        overs=None,
        session: dict | None = None,
    ) -> dict:
        # A match IS the review session (Roadmap "Review Session"): it carries the
        # operator/venue/ground/tournament context plus the model + calibration in
        # force when it started, so every review it holds is reproducible months later.
        session = session or {}
        provenance = self._provenance()
        return {
            "id": f"match_{int(time.time() * 1000)}",
            "name": name or "Untitled Match",
            "teams": teams or {},
            "overs": overs,
            "started_at": time.time() * 1000.0,
            "ended_at": None,
            "session": {
                "operator": str(session.get("operator") or "").strip() or None,
                "tournament": str(session.get("tournament") or "").strip() or None,
                "venue": str(session.get("venue") or "").strip() or None,
                "ground": str(session.get("ground") or "").strip() or None,
                "active_model": provenance.get("model"),
                "calibration_profile": session.get("calibration_profile")
                or (f"{len(provenance.get('calibrated_camera_ids') or [])} camera(s)"
                    if provenance.get("calibrated_camera_ids") else None),
                "camera_ids": provenance.get("camera_ids"),
            },
            "reviews": [],
        }

    def current_match(self) -> dict:
        m = self.match
        return {
            "id": m["id"],
            "name": m.get("name", "Untitled Match"),
            "teams": m.get("teams", {}),
            "overs": m.get("overs"),
            "started_at": m.get("started_at"),
            "ended_at": m.get("ended_at"),
            "session": m.get("session", {}),
            "reviews": m["reviews"][:50],
            "review_count": len(m["reviews"]),
        }

    def new_match(
        self,
        name: str | None = None,
        teams: dict | None = None,
        overs=None,
        session: dict | None = None,
    ) -> dict:
        """Archive the current match to Session History (if it has any reviews),
        then start a fresh empty match/session and return to IDLE."""
        if self.match.get("reviews"):
            self.match["ended_at"] = time.time() * 1000.0
            self._archive_match(self.match)
        self.match = self._new_match(name, teams, overs, session=session)
        self.current_decision = self._waiting_decision()
        self._save_session()
        sess = self.match.get("session", {})
        activity_log.record(
            "session_started",
            f"Session started: {self.match.get('name')}",
            operator=sess.get("operator"), venue=sess.get("venue"),
            ground=sess.get("ground"), model=sess.get("active_model"),
        )
        return self.current_match()

    def _archive_match(self, match: dict) -> None:
        try:
            MATCHES_DIR.mkdir(parents=True, exist_ok=True)
            archived = {**match, "archived_at": time.time() * 1000.0}
            (MATCHES_DIR / f"{match['id']}.json").write_text(json.dumps(archived, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Could not archive match {}: {}", match.get("id"), exc)

    def list_matches(self) -> list[dict]:
        """Session History — archived matches, newest first (read-only summaries)."""
        out: list[dict] = []
        if MATCHES_DIR.exists():
            for path in MATCHES_DIR.glob("match_*.json"):
                try:
                    m = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                session = m.get("session", {}) or {}
                out.append({
                    "id": m.get("id"),
                    "name": m.get("name", "Untitled Match"),
                    "started_at": m.get("started_at"),
                    "ended_at": m.get("ended_at"),
                    "archived_at": m.get("archived_at"),
                    "operator": session.get("operator"),
                    "venue": session.get("venue"),
                    "ground": session.get("ground"),
                    "active_model": session.get("active_model"),
                    "review_count": len(m.get("reviews", [])),
                })
        out.sort(key=lambda x: x.get("archived_at") or 0, reverse=True)
        return out

    def get_match(self, match_id: str) -> dict | None:
        path = MATCHES_DIR / f"{match_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ---------------------------------------------------------------- session
    def _save_session(self) -> None:
        try:
            SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
            SESSION_PATH.write_text(
                json.dumps({"current_match": self.match, "current_decision": self.current_decision}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("Could not persist desktop session: {}", exc)

    def _load_session(self) -> None:
        if not SESSION_PATH.exists():
            return
        try:
            data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not load desktop session: {}", exc)
            return
        # The MATCH resumes across restarts (name/teams/overs + review history), so a
        # crash mid-innings continues where it left off.
        if isinstance(data.get("current_match"), dict):
            self.match = data["current_match"]
            self.match.setdefault("reviews", [])
        elif "reviews" in data:  # migrate a legacy flat review list into a resumed match
            self.match = self._new_match(name="Resumed Match")
            self.match["reviews"] = list(data.get("reviews", []))
        # The ACTIVE REVIEW never resumes: every launch starts IDLE (WAITING) so the
        # operator sees "Request Review". If a review was in flight when the app closed,
        # record it as INTERRUPTED rather than silently dropping or resuming it.
        prev = data.get("current_decision") or {}
        if prev.get("status") not in (None, "WAITING"):
            self.match["reviews"].insert(0, {
                "id": f"review_{len(self.match['reviews']) + 1}",
                "time": time.time() * 1000.0,
                "over": "--",
                "decision": "INTERRUPTED",
                "confidence": None,
            })

    def _waiting_decision(self) -> dict:
        return {
            "status": "WAITING",
            "outcome": "Waiting for appeal",
            "overall_confidence": None,
            "ball_confidence": None,
            "tracking_confidence": None,
            "calibration_confidence": None,
            "prediction_confidence": None,
            "model_confidence": None,
            "impact_point": None,
            "bounce_point": None,
            "wicket_zone_status": "--",
            "ball_speed_kmh": None,
            "trajectory": [],
            "predicted_extension": [],
            "timeline": [],
            "explanation": "Awaiting appeal sequence.",
        }

    def _run_pipeline_tick(self) -> None:
        """Execute one pipeline cycle and store detection/tracking results."""
        try:
            state = self.pipeline.process_once()
            self.pipeline_state = state
            # Aggregate best detection across cameras for WebSocket broadcast
            best: dict | None = None
            for camera_id, pf in state.frames.items():
                det = pf.detection
                if det.detected:
                    candidate = {
                        "camera_id": camera_id,
                        "confidence": round(float(det.confidence), 4),
                        "bbox": list(det.bbox) if det.bbox is not None else None,
                        "inference_ms": round(float(det.inference_ms), 2),
                        "frame_id": pf.video_frame.frame_id,
                        "timestamp_ms": pf.video_frame.timestamp_ms,
                    }
                    if best is None or candidate["confidence"] > best["confidence"]:
                        best = candidate
            self._last_detection = best
            # Update trajectory from tracker points if any exist
            for camera_id, pf in state.frames.items():
                if pf.track_point is not None:
                    tracker = self.pipeline.trackers.get(camera_id)
                    if tracker is not None and tracker.history:
                        trajectory = [
                            {"x": float(pt.x), "y": float(pt.y), "z": 0.0}
                            for pt in tracker.history[-60:]
                        ]
                        self.current_decision["trajectory"] = trajectory
                        break
        except Exception as exc:
            log.debug("Pipeline tick skipped: {}", exc)
            self._last_detection = None

    def _processing_seed(self) -> dict:
        """Honest placeholder for a just-requested review. The review engine fills in
        whatever it can actually measure; every field here is null/empty so the
        dashboard shows "--" rather than a fabricated impact point, speed, wicket call
        or confidence. (This replaces the old `_sample_decision`, which injected a fixed
        sine trajectory + 0.88 confidences + 128.4 km/h that leaked to the operator as
        if they were real analysis when the live pipeline couldn't measure them.)"""
        return {
            "status": "PROCESSING",
            "outcome": "Processing review",
            "overall_confidence": None,
            "ball_confidence": None,
            "tracking_confidence": None,
            "calibration_confidence": None,
            "prediction_confidence": None,
            "model_confidence": None,
            "impact_point": None,
            "impact_marker": None,
            "bounce_point": None,
            "wicket_zone_status": "--",
            "wicket_prediction": None,
            "ball_speed_kmh": None,
            "trajectory": [],
            "predicted_extension": [],
            "timeline": [
                {"label": "Appeal", "status": "complete"},
                {"label": "Ball detected", "status": "pending"},
                {"label": "Bounce detected", "status": "pending"},
                {"label": "Impact detected", "status": "pending"},
                {"label": "Decision generated", "status": "active"},
            ],
            "edge_analysis": {"edge_probability": 0.0, "events": []},
            "hotspot_analysis": {"contact_detected": False, "reason": "No contact heatmap for LBW review."},
            "explanation": "Analyzing the captured replay buffer…",
        }


def create_app(camera_ids: list[int], record: bool = False) -> FastAPI:
    backend = DRSBackend(camera_ids, record=record)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        backend.start()
        watchdog = asyncio.create_task(_watchdog_loop(backend))
        try:
            yield
        finally:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            backend.stop()

    app = FastAPI(title="Cricket DRS Backend", version="0.1.0", lifespan=lifespan)
    app.state.backend = backend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return backend.health()

    @app.get("/api/cameras")
    def cameras() -> dict:
        return {
            "camera_ids": backend.camera_ids,
            "health": backend.camera_manager.health(),
        }

    @app.get("/api/cameras/fps")
    def cameras_fps() -> dict:
        return backend.camera_status()

    @app.get("/api/system/health")
    def system_health() -> dict:
        return backend.system_health()

    @app.get("/api/system/info")
    def system_info_route() -> dict:
        return system_info()

    @app.get("/api/preflight")
    def preflight(
        cameras: str | None = Query(default=None, description="Comma-separated camera ids in use"),
        require_audio: bool = Query(default=False),
        require_gpu: bool = Query(default=False),
    ) -> dict:
        selected: list[int] | None = None
        if cameras:
            selected = [int(part) for part in cameras.split(",") if part.strip().lstrip("-").isdigit()]
        return backend.preflight_checklist(selected, require_audio, require_gpu)

    @app.get("/api/calibration/status")
    async def calibration_status() -> dict:
        return calibration_status_payload()

    @app.post("/api/calibration/save")
    async def save_calibration(body: dict = Body(...)) -> dict:
        """Save calibration markers from the dashboard UI."""
        from core.pitch_calibration import ManualPitchCalibrator
        camera_id = int(body.get("camera_id", 0))
        markers = body.get("markers", {})
        image_size = tuple(body.get("image_size", [1280, 720]))
        required = {"off_stump", "middle_stump", "leg_stump", "bowling_crease", "popping_crease"}
        if not required.issubset(markers.keys()):
            raise HTTPException(status_code=422, detail=f"Missing markers: {required - set(markers.keys())}")
        calibrator = ManualPitchCalibrator()
        profile = calibrator.save_profile(camera_id, markers, image_size)
        activity_log.record(
            "calibration_saved",
            f"Calibration saved for camera {camera_id}",
            camera_id=camera_id,
            homography_error_cm=profile.homography_error_cm,
        )
        return {
            "status": "saved",
            "camera_id": camera_id,
            "homography_error_cm": profile.homography_error_cm,
        }

    @app.delete("/api/calibration/cameras/{camera_id}")
    async def delete_camera_calibration(camera_id: int) -> dict:
        """Remove a saved per-camera pitch calibration profile."""
        from core.pitch_calibration import ManualPitchCalibrator
        deleted = ManualPitchCalibrator().delete_profile(camera_id)
        return {
            "deleted": deleted,
            "camera_id": camera_id,
            "status": calibration_status_payload(),
        }

    @app.post("/api/calibration/verify")
    async def verify_calibration(body: dict = Body(...)) -> dict:
        """Test pixel→world transform for a given point."""
        from core.pitch_calibration import ManualPitchCalibrator
        camera_id = int(body.get("camera_id", 0))
        px = float(body.get("x", 0))
        py = float(body.get("y", 0))
        calibrator = ManualPitchCalibrator()
        result = calibrator.pixel_to_pitch_mm(camera_id, px, py)
        if result is None:
            return {"error": "No calibration profile for this camera", "camera_id": camera_id}
        return {
            "camera_id": camera_id,
            "pixel": {"x": px, "y": py},
            "world_mm": {"lateral_mm": result[0], "along_mm": result[1]},
        }

    @app.get("/api/calibration/profiles")
    async def calibration_profiles() -> dict:
        """List saved per-camera pitch profiles for the wizard cards + health summary."""
        from core.pitch_calibration import ManualPitchCalibrator
        calibrator = ManualPitchCalibrator()
        profiles = []
        for profile in calibrator.list_profiles():
            error_cm = profile.homography_error_cm
            profiles.append({
                "camera_id": profile.camera_id,
                "name": f"Camera {profile.camera_id}",
                "camera": f"Cam {profile.camera_id}",
                "homography_error_cm": error_cm,
                "quality": _calibration_quality(error_cm),
                "marker_count": len(profile.markers),
                "image_size": list(profile.image_size),
                "updated_at": profile.updated_at,
                "markers": profile.markers,
            })
        return {"profiles": profiles, "configured_cameras": backend.camera_ids}

    @app.post("/api/calibration/compute")
    async def compute_calibration(body: dict = Body(...)) -> dict:
        """Solve the homography from markers WITHOUT persisting — live quality preview."""
        from core.pitch_calibration import ManualPitchCalibrator
        markers = body.get("markers", {})
        required = {"off_stump", "middle_stump", "leg_stump", "bowling_crease", "popping_crease"}
        missing = required - set(markers.keys())
        if missing:
            raise HTTPException(status_code=422, detail=f"Missing markers: {sorted(missing)}")
        try:
            homography, error_cm = ManualPitchCalibrator().compute_homography(markers)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "homography": homography,
            "homography_error_cm": error_cm,
            "quality": _calibration_quality(error_cm),
        }

    @app.post("/api/calibration/auto-detect")
    async def auto_detect_calibration(body: dict = Body(default_factory=dict)) -> dict:
        """Propose a draggable marker template scaled to the frame.

        Real stump/crease detection needs a trained model; until then this returns a
        sensible pitch-shaped starting layout the operator nudges onto the markers.
        """
        image_size = body.get("image_size") or [1280, 720]
        try:
            width, height = int(image_size[0]), int(image_size[1])
        except (TypeError, ValueError, IndexError):
            width, height = 1280, 720
        cx = width * 0.5
        stump_y = height * 0.46
        half = width * 0.045  # off/leg spread either side of middle
        markers = {
            "off_stump": {"x": round(cx - half, 1), "y": round(stump_y, 1)},
            "middle_stump": {"x": round(cx, 1), "y": round(stump_y, 1)},
            "leg_stump": {"x": round(cx + half, 1), "y": round(stump_y, 1)},
            "bowling_crease": {"x": round(cx, 1), "y": round(height * 0.5, 1)},
            "popping_crease": {"x": round(cx, 1), "y": round(height * 0.63, 1)},
        }
        return {
            "markers": markers,
            "method": "template",
            "confidence": 0.0,
            "note": "Proposed positions — drag each numbered marker onto the real stump base or crease line.",
        }

    @app.get("/api/decision/current")
    def decision_current() -> dict:
        return backend.current_decision

    @app.post("/api/decision/confirm")
    def decision_confirm(payload: dict = Body(default_factory=dict)) -> dict:
        return backend.confirm_decision(str(payload.get("outcome", "NOT_OUT")).upper())

    @app.post("/api/decision/reset")
    def decision_reset() -> dict:
        return backend.reset_decision()

    @app.get("/api/reviews")
    def reviews() -> dict:
        return {"reviews": backend.reviews}

    @app.get("/api/reviews/{review_id}")
    def review_by_id(review_id: str) -> dict:
        for review in backend.reviews:
            if review.get("id") == review_id:
                return review
        raise HTTPException(status_code=404, detail="Unknown review")

    @app.get("/api/match/current")
    def match_current() -> dict:
        return backend.current_match()

    @app.post("/api/match/new")
    def match_new(payload: dict = Body(default_factory=dict)) -> dict:
        name = str(payload.get("name") or "").strip() or None
        # Session provenance travels either at the top level or under "session".
        session = payload.get("session") if isinstance(payload.get("session"), dict) else {
            key: payload.get(key)
            for key in ("operator", "tournament", "venue", "ground", "calibration_profile")
            if payload.get(key) is not None
        }
        return backend.new_match(
            name=name,
            teams=payload.get("teams"),
            overs=payload.get("overs"),
            session=session,
        )

    @app.get("/api/matches")
    def matches_history() -> dict:
        return {"matches": backend.list_matches()}

    @app.get("/api/activity")
    def activity(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        return {"events": activity_log.recent(limit)}

    @app.get("/api/matches/{match_id}")
    def match_by_id(match_id: str) -> dict:
        match = backend.get_match(match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Unknown match")
        return match

    @app.get("/api/review-types")
    def review_types() -> dict:
        """Capability contract for every registered review module. The dashboard
        builds its review-type registry (labels, camera role, timeline stages,
        evidence, replay mode, decision-card rows) from THIS — one source of truth,
        so adding a module never requires re-declaring it in the renderer."""
        from core.review_modules import describe_types

        return {"types": describe_types()}

    @app.get("/api/camera-roles")
    def camera_roles_catalog() -> dict:
        """Canonical camera roles (with icons) for the calibration role picker."""
        from core.camera_roles import role_catalog
        return {"roles": role_catalog()}

    @app.post("/api/appeal/request")
    def appeal_request(payload: dict = Body(default_factory=dict)) -> dict:
        camera_ids = payload.get("camera_ids") or backend.camera_ids
        if not isinstance(camera_ids, list):
            raise HTTPException(status_code=400, detail="camera_ids must be a list")
        review_type = str(payload.get("review_type", "lbw")).lower()
        camera_roles = payload.get("camera_roles") if isinstance(payload.get("camera_roles"), dict) else None
        primary_camera_id = payload.get("primary_camera_id")
        # The review engine runs the right analysis for the active review type and
        # merges its output (wide_analysis / no_ball_analysis / ...) into the decision.
        result = backend.request_review(
            [int(camera_id) for camera_id in camera_ids],
            review_type=review_type,
            camera_roles=camera_roles,
            primary_camera_id=primary_camera_id,
        )
        # LBW appeals additionally run the CANONICAL decision/replay pipeline — the exact
        # same code path the Testing page uses (ONE implementation, no dashboard copy).
        # The captured live-replay clip becomes a canonical job; the dashboard then polls
        # the same /api/analyze/{id}/results and plays the same replay exports.
        decision = result.get("decision") or {}
        if review_type == "lbw":
            clip = ((decision.get("review_result") or {}).get("replay") or {}).get("path")
            if clip and Path(clip).is_file():
                import threading
                import uuid as _uuid

                from core.testing_api import _run_job, db as testing_db
                from core.testing_pipeline import AnalysisOptions

                job_id = _uuid.uuid4().hex[:12]
                testing_db.create_job(job_id, "1_camera", {"source": "live_appeal"}, str(clip), None)
                threading.Thread(
                    target=_run_job,
                    # DRS protocol: an LBW review always includes the UltraEdge trace —
                    # the umpire checks bat involvement BEFORE reading ball-tracking.
                    args=(job_id, [Path(clip)], AnalysisOptions(use_calibration=False, edge_detection=True)),
                    daemon=True,
                ).start()
                decision["canonical_job_id"] = job_id
            else:
                # honesty rule: never fabricate — say exactly why there is no replay
                decision["canonical_job_id"] = None
                decision["canonical_skip_reason"] = "no live replay clip captured for this appeal"
        return result

    @app.get("/api/animation/trajectory")
    async def get_trajectory_animation() -> dict:
        """Return trajectory points for 3D visualization."""
        decision = backend.current_decision
        trajectory = decision.get("trajectory")
        return {
            "trajectory": trajectory,
            "has_data": trajectory is not None,
            "decision_status": decision.get("status", "WAITING"),
        }

    @app.get("/api/analysis-mode")
    def get_analysis_mode() -> dict:
        return backend.analysis_mode

    @app.post("/api/analysis-mode")
    def analysis_mode(payload: dict = Body(default_factory=dict)) -> dict:
        mode = str(payload.get("mode", "visible"))
        backend.analysis_mode = (
            {"id": "thermal_demo", "label": "Mode B - simulated thermal presentation"}
            if mode == "thermal_demo"
            else {"id": "visible", "label": "Mode A - visible-spectrum approximation"}
        )
        return backend.analysis_mode

    @app.get("/api/presets")
    def presets() -> dict:
        return APPEAL_PRESETS

    @app.get("/api/presets/{appeal_type}")
    def preset(appeal_type: str) -> dict:
        key = appeal_type.upper()
        if key not in APPEAL_PRESETS:
            raise HTTPException(status_code=404, detail="Unknown appeal type")
        return resolve_preset(APPEAL_PRESETS[key], backend.camera_ids)

    @app.post("/api/replay/create")
    def create_replay() -> dict:
        return backend.create_replay()

    @app.get("/api/replay/state")
    def replay_state() -> dict:
        return backend.replay_state()

    @app.post("/api/replay/control")
    def replay_control(payload: dict = Body(default_factory=dict)) -> dict:
        if backend.active_replay is None:
            backend.create_replay()
        assert backend.active_replay is not None
        action = str(payload.get("action", "")).lower()
        if action == "play":
            backend.active_replay.play(float(payload.get("speed", 1.0)))
        elif action == "pause":
            backend.active_replay.pause()
        elif action == "step_forward":
            backend.active_replay.step(1)
        elif action == "step_back":
            backend.active_replay.step(-1)
        elif action == "seek":
            backend.active_replay.seek(int(payload.get("frame_index", 0)))
        elif action == "speed":
            backend.active_replay.speed = max(0.05, min(4.0, float(payload.get("speed", 1.0))))
        else:
            raise HTTPException(status_code=400, detail="Unknown replay action")
        return backend.replay_state()

    @app.post("/api/replay/export")
    def replay_export() -> dict:
        path = backend.export_replay()
        return {"status": "exported", "path": str(path)}

    @app.post("/api/replay/request")
    def replay_request(payload: dict = Body(default_factory=dict)) -> dict:
        camera_ids = payload.get("camera_ids", backend.camera_ids)
        frame_index = payload.get("frame_index")
        timestamp_ms = payload.get("timestamp_ms")
        if not isinstance(camera_ids, list):
            raise HTTPException(status_code=400, detail="camera_ids must be a list")
        return backend.replay_frames(
            [int(camera_id) for camera_id in camera_ids],
            int(frame_index) if frame_index is not None else None,
            float(timestamp_ms) if timestamp_ms is not None else None,
        )

    @app.get("/api/audio/edge")
    def edge_audio(timestamp_ms: float | None = Query(default=None)) -> dict:
        """Live UltraEdge query. With a timestamp: was there a snick-band transient
        within the analyzer's window around that instant? Without: check the last
        ~0.6 s. Honest when no microphone is capturing."""
        if getattr(backend, "audio_pipeline", None) is None:
            return {
                "available": False,
                "timestamp_ms": timestamp_ms,
                "edge_probability": 0.0,
                "status": "no microphone — audio capture not running",
            }
        query_ts = float(timestamp_ms) if timestamp_ms is not None else time.time() * 1000.0 - 600.0
        result = backend.audio_pipeline.analyzer.detect_edge_at(query_ts)
        return {
            "available": True,
            "status": "live",
            "timestamp_ms": query_ts,
            "has_edge": result.has_edge,
            "edge_probability": round(float(result.edge_confidence), 3),
            "edge_timestamp_ms": round(float(result.edge_timestamp_ms), 1),
            "reason": result.reason,
        }

    @app.get("/api/live/{camera_id}.jpg")
    def live_frame(camera_id: int) -> Response:
        try:
            return jpeg_response(backend.latest_frame(camera_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Camera {camera_id} has no frame")

    @app.get("/api/replay/{camera_id}.jpg")
    def replay_frame(
        camera_id: int,
        frame_index: int | None = Query(default=None),
        timestamp_ms: float | None = Query(default=None),
    ) -> Response:
        try:
            return jpeg_response(backend.replay_frame(camera_id, frame_index, timestamp_ms))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Camera {camera_id} has no replay frame")

    @app.get("/api/live/{camera_id}.mjpg")
    def live_stream(camera_id: int) -> StreamingResponse:
        return StreamingResponse(mjpeg_generator(backend, camera_id), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.websocket("/ws/status")
    async def websocket_status(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                # Run pipeline tick to update detection/tracking state
                backend._run_pipeline_tick()
                for payload in backend.status_events():
                    await websocket.send_json(payload)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/{channel}")
    async def websocket_channel(websocket: WebSocket, channel: str) -> None:
        if channel not in {"live", "trajectory", "decision", "replay", "system"}:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                if channel == "live":
                    payload = backend.live_payload(include_frames=True)
                elif channel == "trajectory":
                    payload = {"type": "trajectory", "trajectory": backend.current_decision.get("trajectory", [])}
                elif channel == "decision":
                    payload = {"type": "decision", "decision": backend.current_decision}
                elif channel == "replay":
                    payload = {"type": "replay", "replay": backend.replay_state()}
                else:
                    payload = {"type": "system", "health": backend.system_health()}
                await websocket.send_json(payload)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    return app


def resolve_preset(preset: dict, camera_ids: list[int]) -> dict:
    camera_ids = sorted(camera_ids)
    if not camera_ids:
        return {**preset, "big_camera_id": None, "small_camera_ids": []}
    big_index = min(preset["big_camera_index"], len(camera_ids) - 1)
    big_camera_id = camera_ids[big_index]
    small_camera_ids = []
    for index in preset["small_camera_indices"]:
        if index < len(camera_ids):
            small_camera_ids.append(camera_ids[index])
    if not small_camera_ids:
        small_camera_ids = [item for item in camera_ids if item != big_camera_id][:1]
    return {
        **preset,
        "big_camera_id": big_camera_id,
        "small_camera_ids": small_camera_ids,
    }


def jpeg_response(item: VideoFrame) -> Response:
    encoded = encode_jpeg(item, quality=82)
    headers = {
        "X-Camera-Id": str(item.camera_id),
        "X-Frame-Id": str(item.frame_id),
        "X-Timestamp-Ms": str(item.timestamp_ms),
        "Cache-Control": "no-store",
    }
    return Response(content=encoded, media_type="image/jpeg", headers=headers)


def encode_jpeg(item: VideoFrame, quality: int = 82) -> bytes:
    ok, encoded = cv2.imencode(".jpg", item.frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode frame")
    return encoded.tobytes()


def _blank_frame() -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (12, 22, 30)
    cv2.putText(frame, "NO REPLAY FRAMES AVAILABLE", (60, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (230, 230, 230), 2)
    return frame


async def mjpeg_generator(backend: DRSBackend, camera_id: int):
    while True:
        try:
            item = backend.latest_frame(camera_id)
            ok, encoded = cv2.imencode(".jpg", item.frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
            if ok:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n"
        except KeyError:
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.03)


async def _watchdog_loop(backend: DRSBackend) -> None:
    # Remember each camera's last known connected state so the activity log records
    # transitions (connect/disconnect) rather than spamming an event every second.
    last_connected: dict[int, bool] = {}
    while True:
        health = backend.camera_status()
        offline = [item for item in health["cameras"] if item["health_score"] < 0.35]
        if offline:
            log.warning("Camera watchdog detected unhealthy cameras: {}", [item["id"] for item in offline])
        for item in health["cameras"]:
            cid = item["id"]
            connected = bool(item["connected"])
            if cid in last_connected and last_connected[cid] != connected:
                if connected:
                    activity_log.record("camera_connected", f"Camera {cid} connected", camera_id=cid)
                else:
                    activity_log.record("camera_disconnected", f"Camera {cid} disconnected", camera_id=cid)
            last_connected[cid] = connected
        if backend.active_replay is not None:
            backend.active_replay.tick()
        await asyncio.sleep(1.0)


def _attach_testing_platform_routes(app: FastAPI) -> int:
    """Fold the offline testing-platform routes into the single canonical backend.

    This is what makes the Electron app talk to ONE API: the real dashboard /
    review-engine / 5-marker-calibration routes defined above stay authoritative,
    and only the routes unique to the upload/testing platform (jobs, analyze,
    uploads, exports, per-camera snapshot calibration, and their websockets) are
    pulled in. Anything this app already defines is left untouched, so there is
    exactly one handler per (path, method) — keyed on the method too, so a GET the
    core app owns does not shadow the testing platform's POST on the same path.
    """
    try:
        from core.testing_api import create_testing_app
        testing_app = create_testing_app()
    except Exception as exc:  # never let the testing platform break the core API
        log.warning("Testing-platform routes unavailable; serving core API only: {}", exc)
        return 0

    def route_keys(route) -> list[tuple[str, str]]:
        path = getattr(route, "path", None)
        if not path:
            return []
        methods = getattr(route, "methods", None)
        if not methods:  # websocket routes carry no HTTP methods
            return [(path, "WS")]
        return [(path, method) for method in methods]

    existing: set[tuple[str, str]] = set()
    for route in app.router.routes:
        existing.update(route_keys(route))

    def is_ws_catchall(route) -> bool:
        # The core app's /ws/{channel} matches ANY single /ws/<x> segment. Starlette
        # matches routes in list order, so a literal /ws/review appended AFTER it is
        # never reached (it 403'd). Literal /ws/... routes must sit before the catch-all.
        path = getattr(route, "path", "") or ""
        return path.startswith("/ws/") and "{" in path

    added = 0
    for route in testing_app.router.routes:
        path = getattr(route, "path", None)
        if not path or not (path.startswith("/api/") or path.startswith("/ws/")):
            continue
        keys = route_keys(route)
        if any(key in existing for key in keys):
            continue
        if path.startswith("/ws/") and "{" not in path:
            insert_at = next(
                (i for i, r in enumerate(app.router.routes) if is_ws_catchall(r)),
                len(app.router.routes),
            )
            app.router.routes.insert(insert_at, route)
        else:
            app.router.routes.append(route)
        existing.update(keys)
        added += 1
    log.info("Unified backend: attached {} testing-platform routes", added)
    return added


def create_unified_app(camera_ids: list[int] | None = None, record: bool = False) -> FastAPI:
    """The single backend the Electron app and the test suite talk to.

    It is the live camera + review-engine + 5-marker-calibration API from
    create_app(), with the offline upload/testing-platform routes (jobs, analyze,
    uploads, exports, per-camera snapshot calibration) folded in on top. Every path
    has exactly one handler: the core API is authoritative and only routes it does
    not already define are pulled in from the testing platform.
    """
    app = create_app(camera_ids or list(CAMERA_IDS), record=record)
    _attach_testing_platform_routes(app)

    # The testing platform's own startup (stale-job recovery, capturing the serving
    # loop for cross-thread WS broadcasts, the system broadcast loop) only ran under
    # its standalone lifespan. In the unified app that lifespan was dropped, so wrap
    # the core lifespan to run both — core first, then testing on top.
    core_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def unified_lifespan(a: FastAPI) -> AsyncIterator[None]:
        try:
            from core import testing_api
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Testing-platform lifespan unavailable: {}", exc)
            testing_api = None
        async with core_lifespan(a):
            if testing_api is not None:
                await testing_api.start_background_services()
            try:
                yield
            finally:
                if testing_api is not None:
                    await testing_api.stop_background_services()

    app.router.lifespan_context = unified_lifespan
    return app


def run_api(camera_ids: list[int], record: bool, host: str, port: int) -> None:
    import uvicorn

    app = create_unified_app(camera_ids, record=record)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _pf_item(
    key: str,
    label: str,
    group: str,
    status: str,
    detail: str,
    required: bool = True,
    value: dict | None = None,
) -> dict:
    """One preflight checklist row. `status` is pass|warn|fail|skip."""
    return {
        "key": key,
        "label": label,
        "group": group,
        "status": status,
        "detail": detail,
        "required": required,
        "value": value or {},
    }


def _gpu_status() -> dict:
    """Best-effort GPU probe via torch. PyTorch/CUDA absence is reported truthfully
    but never raises — the detector falls back to CPU, so a missing GPU is a warning,
    not a hard failure, unless the operator explicitly requires one."""
    try:
        import torch
    except Exception:
        return {"available": False, "percent": None, "device": None,
                "detail": "PyTorch not installed — running on CPU."}
    try:
        if not torch.cuda.is_available():
            return {"available": False, "percent": None, "device": None,
                    "detail": "No CUDA device detected — running on CPU."}
        index = torch.cuda.current_device()
        name = torch.cuda.get_device_name(index)
        used_percent = None
        free_gb = total_gb = None
        try:
            free, total = torch.cuda.mem_get_info(index)
            if total:
                used_percent = round(1.0 - free / total, 3)
                free_gb = round(free / (1024 ** 3), 2)
                total_gb = round(total / (1024 ** 3), 2)
        except Exception:
            pass
        return {"available": True, "percent": used_percent, "device": name,
                "memory_free_gb": free_gb, "memory_total_gb": total_gb,
                "detail": f"{name} ready."}
    except Exception as exc:  # noqa: BLE001 - driver mismatch etc. must not crash the probe
        return {"available": False, "percent": None, "device": None,
                "detail": f"GPU probe failed: {exc}"}


def _cpu_percent() -> float:
    try:
        import psutil

        return float(psutil.cpu_percent(interval=None)) / 100.0
    except Exception:
        return 0.0


def _ram_percent() -> float:
    try:
        import psutil

        return float(psutil.virtual_memory().percent) / 100.0
    except Exception:
        return 0.0


def _free_gb(path: Path) -> float:
    try:
        import shutil

        usage = shutil.disk_usage(path)
        return round(usage.free / (1024**3), 2)
    except Exception:
        return 0.0


def _path_writable(path: Path) -> tuple[bool, str]:
    """True if we can create+write a file under `path` (creating it if needed).
    Used by the preflight checklist so 'Database/Export writable' is a real probe,
    not an assumption."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, "writable"
    except Exception as exc:
        return False, f"Not writable: {exc}"


def system_info() -> dict:
    """Static/slow-changing environment facts for the System page: runtime versions,
    disk, memory, database size, git commit, and config summary. Everything an
    operator needs to answer 'what exactly is this machine running?'."""
    import platform
    import sys

    def _ver(module_name: str) -> str | None:
        try:
            mod = __import__(module_name)
            return getattr(mod, "__version__", None)
        except Exception:
            return None

    cuda = None
    torch_ver = _ver("torch")
    try:
        import torch

        cuda = torch.version.cuda if torch.cuda.is_available() else None
    except Exception:
        cuda = None

    db_path = DATA_DIR / "testing" / "drs_testing.sqlite3"
    db_size_mb = round(db_path.stat().st_size / (1024**2), 2) if db_path.exists() else 0.0

    # Git commit if a working repo exists (the local .git may be broken/absent — then None).
    git_commit = None
    try:
        head = DATA_DIR.parent / ".git" / "HEAD"
        if head.exists():
            ref = head.read_text(encoding="utf-8").strip()
            if ref.startswith("ref:"):
                ref_path = DATA_DIR.parent / ".git" / ref.split(" ", 1)[1].strip()
                if ref_path.exists():
                    git_commit = ref_path.read_text(encoding="utf-8").strip()[:10]
            else:
                git_commit = ref[:10]
    except Exception:
        git_commit = None

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch_ver,
        "cuda": cuda,
        "opencv": _ver("cv2"),
        "numpy": _ver("numpy"),
        "ultralytics": _ver("ultralytics"),
        "fastapi": _ver("fastapi"),
        "executable": sys.executable,
        "gpu": _gpu_status(),
        "cpu_percent": round(_cpu_percent() * 100, 1),
        "ram_percent": round(_ram_percent() * 100, 1),
        "disk_free_gb": _free_gb(DATA_DIR),
        "database": {"path": str(db_path), "size_mb": db_size_mb, "exists": db_path.exists()},
        "git_commit": git_commit,
        "data_dir": str(DATA_DIR),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cricket DRS live backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--cameras", default=",".join(str(item) for item in CAMERA_IDS))
    args = parser.parse_args()
    camera_ids = [int(item.strip()) for item in args.cameras.split(",") if item.strip()]
    run_api(camera_ids, args.record, args.host, args.port)


if __name__ == "__main__":
    main()
