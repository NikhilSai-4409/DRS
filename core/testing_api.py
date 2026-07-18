"""FastAPI API for the offline Cricket DRS Testing Platform."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from core.decision_mapper import map_summary_to_dashboard_decision
from core.pitch_calibration import (
    MARKER_KEYS,
    ManualPitchCalibrator,
    calibration_status_payload,
    default_icc_profile,
)
from core.testing_database import TestingDatabase
from core.testing_pipeline import AnalysisOptions, DeliveryTestingPipeline, OUTPUT_DIR, UPLOAD_DIR
from core import lbw_validation
from core.model_registry import ModelRegistry
from core.ws_hub import CHANNELS, WSBroadcastHub
from utils.logger import get_logger

log = get_logger("testing_api")


def _calibration_quality(error_cm: float | None) -> dict[str, Any]:
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


DB_PATH = Path("data/testing/drs_testing.sqlite3")
CALIBRATION_DIR = Path("data/calibration")
CALIBRATION_PROFILES_PATH = Path("config/calibration_profiles.json")
db = TestingDatabase(DB_PATH)
pipeline = DeliveryTestingPipeline()
# Pipelines for explicitly-selected models are cached so re-testing the same model
# doesn't reload YOLO weights each run. None → the default pipeline above.
_pipeline_cache: dict[str, DeliveryTestingPipeline] = {}


def _pipeline_for(model_path: str | None) -> DeliveryTestingPipeline:
    if not model_path:
        return pipeline
    key = str(model_path)
    if key not in _pipeline_cache:
        _pipeline_cache[key] = DeliveryTestingPipeline(model_path=model_path)
    return _pipeline_cache[key]


# One validation run at a time; the UI polls /api/testing/validation/runs for state.
_validation_state: dict[str, Any] = {"status": "idle", "run_id": None, "error": None, "started": None}


def _run_validation(model: str | None, calibration: str | None, limit: int | None) -> None:
    """Background task: run the LBW ground-truth set through the REAL pipeline."""
    _validation_state.update(
        {"status": "running", "run_id": None, "error": None,
         "started": datetime.now().isoformat(timespec="seconds")}
    )
    try:
        def run_clip(spec, model_path):
            return _pipeline_for(model_path).process(
                f"val_{spec.id}", [Path(spec.path)], AnalysisOptions(model_path=model_path)
            )

        run = lbw_validation.validate(
            model_override=model,
            calibration_override=calibration,
            limit=limit,
            run_clip=run_clip,
        )
        _validation_state.update({"status": "complete", "run_id": run.run_id})
        log.info(
            "[API] Validation run {} complete: {}/{} = {:.1f}%",
            run.run_id, run.correct, run.scored, run.accuracy * 100,
        )
    except Exception as exc:  # never let a bad run wedge the state as 'running'
        _validation_state.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        log.error("[API] Validation run failed: {}", exc, exc_info=True)


# Model A-vs-B comparison on the validation set (one at a time).
_compare_state: dict[str, Any] = {"status": "idle", "error": None, "result": None}


def _run_compare(model_a: str | None, model_b: str | None, limit: int | None) -> None:
    """Background: run the validation set with model A and model B, diff the verdicts."""
    _compare_state.update({"status": "running", "error": None, "result": None})
    try:
        def run_clip(spec, model_path):
            return _pipeline_for(model_path).process(
                f"cmp_{spec.id}", [Path(spec.path)], AnalysisOptions(model_path=model_path)
            )

        run_a = lbw_validation.validate(model_override=model_a, limit=limit, run_clip=run_clip, write=False)
        run_b = lbw_validation.validate(model_override=model_b, limit=limit, run_clip=run_clip, write=False)
        a_by = {c.id: c for c in run_a.clips}
        clips = []
        for cb in run_b.clips:
            ca = a_by.get(cb.id)
            clips.append({
                "id": cb.id,
                "expected": cb.expected_verdict,
                "a_verdict": ca.actual_verdict if ca else None,
                "b_verdict": cb.actual_verdict,
                "a_correct": bool(ca and ca.match),
                "b_correct": bool(cb.match),
            })
        _compare_state.update({"status": "complete", "result": {
            "model_a": model_a or "default", "model_b": model_b or "default",
            "accuracy_a": run_a.accuracy, "accuracy_b": run_b.accuracy,
            "correct_a": run_a.correct, "correct_b": run_b.correct, "scored": run_b.scored,
            "clips": clips,
        }})
        log.info("[API] Compare complete: A {:.1f}% vs B {:.1f}%", run_a.accuracy * 100, run_b.accuracy * 100)
    except Exception as exc:
        _compare_state.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        log.error("[API] Compare failed: {}", exc, exc_info=True)


ws_hub = WSBroadcastHub()
START_TIME = time.time()
MAX_CAMERAS = 6
job_progress: dict[str, dict[str, Any]] = {}
connected_camera_count = 2
analysis_mode: dict[str, str] = {
    "id": "visible",
    "label": "Mode A - visible-spectrum approximation",
    "description": "Frame differencing and motion-energy analysis. No thermal inference is claimed.",
}
review_history: list[dict[str, Any]] = []

current_decision: dict[str, Any] = {
    "status": "WAITING",
    "outcome": None,
    "time": None,
    "over": "--",
    "ball": "--",
    "decision": "WAITING",
    "ball_confidence": None,
    "tracking_confidence": None,
    "calibration_confidence": None,
    "prediction_confidence": None,
    "model_confidence": None,
    "overall_confidence": None,
    "impact_point": None,
    "wicket_zone_status": "--",
    "ball_speed_kmh": None,
    "trajectory": [],
    "bounce_point": None,
    "predicted_extension": [],
    "wicket_zone": {"x": 412, "y": 64, "w": 18, "h": 42},
}


async def _system_broadcast_loop() -> None:
    while True:
        try:
            payload = {
                "type": "system_health",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "health": system_health_payload(),
                "calibration": calibration_status_payload(),
                "decision": current_decision,
            }
            await ws_hub.broadcast("system", payload)
            await ws_hub.broadcast("live", {"type": "camera_status", "cameras": camera_fps_payload()["cameras"]})
        except Exception as exc:
            log.warning("[WS] System broadcast failed: {}", exc)
        await asyncio.sleep(1.0)


# The event loop serving the API. Analysis jobs run in worker threads with no loop
# of their own, so schedule_broadcast() uses this reference to marshal WS pushes back
# onto the serving loop — without it every job-progress event was silently dropped.
_main_loop: "asyncio.AbstractEventLoop | None" = None
_system_broadcast_task: "asyncio.Task | None" = None


async def start_background_services() -> None:
    """Run the testing-platform startup: capture the serving loop, recover stale
    jobs, and start the system broadcast loop. Called from both the standalone
    testing app lifespan and the unified backend lifespan so behaviour is identical."""
    global _main_loop, _system_broadcast_task
    _main_loop = asyncio.get_running_loop()
    cleaned, _job_ids = cleanup_stale_jobs(15)
    log.info("[API] Stale job cleanup: {} jobs recovered.", cleaned)
    if _system_broadcast_task is None or _system_broadcast_task.done():
        _system_broadcast_task = asyncio.create_task(_system_broadcast_loop())


async def stop_background_services() -> None:
    global _main_loop, _system_broadcast_task
    if _system_broadcast_task is not None:
        _system_broadcast_task.cancel()
        try:
            await _system_broadcast_task
        except asyncio.CancelledError:
            pass
        _system_broadcast_task = None
    _main_loop = None
    log.info("[API] Shutdown complete.")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await start_background_services()
    try:
        yield
    finally:
        await stop_background_services()


def create_testing_app() -> FastAPI:
    app = FastAPI(title="Cricket DRS Testing Platform", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.websocket("/ws/job/{job_id}")
    async def websocket_job_channel(websocket: WebSocket, job_id: str) -> None:
        channel = WSBroadcastHub.job_channel(_clean_job_id(job_id))
        await ws_hub.connect(channel, websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await ws_hub.disconnect(channel, websocket)

    @app.websocket("/ws/review")
    async def websocket_review(websocket: WebSocket) -> None:
        """Push job progress updates to review dashboard subscribers."""
        await websocket.accept()
        try:
            while True:
                # Send snapshot of all active job progress
                active_jobs = {
                    jid: prog for jid, prog in job_progress.items()
                    if prog.get("status") in {"processing", "queued"}
                }
                await websocket.send_json({
                    "type": "review_status",
                    "active_jobs": active_jobs,
                    "total_reviews": len(review_history),
                    "latest_decision": current_decision.get("status", "WAITING"),
                    "timestamp_ms": time.time() * 1000.0,
                })
                # Push individual updates for each active job
                for jid, prog in active_jobs.items():
                    await websocket.send_json({
                        "type": "job_progress",
                        "job_id": jid,
                        **prog,
                    })
                await asyncio.sleep(1.5)
        except WebSocketDisconnect:
            return

    def health_payload() -> dict[str, Any]:
        detector = pipeline.detector
        return {
            "status": "ok",
            "offline": True,
            "uptime_seconds": int(time.time() - START_TIME),
            "database": str(DB_PATH),
            "upload_dir": str(UPLOAD_DIR),
            "output_dir": str(OUTPUT_DIR),
            "active_model_name": detector.active_model_name,
            "ball_class_ids": sorted(detector.ball_class_ids),
            "model_loaded": detector.model is not None,
        "features": [
                "one_to_six_camera_operation",
                "ball_detection",
                "ball_tracking",
                "trajectory_prediction",
                "lbw_analysis",
                "edge_detection_option",
                "replay_generation",
                "json_csv_pdf_exports",
                "electron_primary_dashboard",
                "react_testing_platform",
            ],
            "max_cameras": MAX_CAMERAS,
            "analysis_mode": analysis_mode,
        }

    @app.get("/api/testing/health")
    def testing_health() -> dict[str, Any]:
        return health_payload()

    @app.post("/api/cameras/{camera_id}/reconnect")
    def reconnect_camera(camera_id: int) -> dict[str, Any]:
        global connected_camera_count
        if camera_id < 1 or camera_id > MAX_CAMERAS:
            raise HTTPException(status_code=400, detail="Invalid camera id")
        connected_camera_count = max(connected_camera_count, camera_id)
        schedule_broadcast("system", {"type": "camera_reconnect", "camera_id": camera_id, "status": "connected"})
        return {"camera_id": camera_id, "status": "connected", "cameras": camera_fps_payload()}

    @app.post("/api/cameras/{camera_id}/disconnect")
    def disconnect_camera(camera_id: int) -> dict[str, Any]:
        global connected_camera_count
        if camera_id < 1 or camera_id > MAX_CAMERAS:
            raise HTTPException(status_code=400, detail="Invalid camera id")
        connected_camera_count = max(1, min(connected_camera_count, camera_id - 1))
        schedule_broadcast("system", {"type": "camera_disconnect", "camera_id": camera_id, "status": "offline"})
        return {"camera_id": camera_id, "status": "offline", "cameras": camera_fps_payload()}

    @app.get("/api/calibration/default-profile")
    def calibration_default_profile() -> dict[str, Any]:
        return default_icc_profile()

    @app.get("/api/calibration/cameras/{camera_id}")
    def get_camera_calibration(camera_id: int) -> dict[str, Any]:
        profile = ManualPitchCalibrator().load_profile(camera_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"No calibration profile for camera {camera_id}")
        snapshot = _calibration_snapshot_path(camera_id)
        payload = profile.to_dict()
        payload["snapshot_available"] = snapshot.exists()
        if snapshot.exists():
            payload["snapshot_url"] = f"/api/calibration/cameras/{camera_id}/snapshot"
        return payload

    @app.post("/api/calibration/cameras/{camera_id}")
    def save_camera_calibration(camera_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        markers = payload.get("markers")
        image_size = payload.get("image_size")
        if not isinstance(markers, dict):
            raise HTTPException(status_code=400, detail="markers object is required")
        missing = [key for key in MARKER_KEYS if key not in markers]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing markers: {', '.join(missing)}")
        if not isinstance(image_size, (list, tuple)) or len(image_size) != 2:
            raise HTTPException(status_code=400, detail="image_size must be [width, height]")
        try:
            profile = ManualPitchCalibrator().save_profile(
                camera_id,
                markers,
                (int(image_size[0]), int(image_size[1])),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log.info("[API] Saved manual pitch calibration for camera {}", camera_id)
        return {"saved": True, "profile": profile.to_dict(), "status": calibration_status_payload()}

    @app.delete("/api/calibration/cameras/{camera_id}")
    def delete_camera_calibration(camera_id: int) -> dict[str, Any]:
        deleted = ManualPitchCalibrator().delete_profile(camera_id)
        snapshot = _calibration_snapshot_path(camera_id)
        snapshot_deleted = False
        if snapshot.exists():
            snapshot.unlink()
            snapshot_deleted = True
        if deleted:
            log.info("[API] Removed manual pitch calibration for camera {}", camera_id)
        return {
            "deleted": deleted,
            "snapshot_deleted": snapshot_deleted,
            "camera_id": camera_id,
            "status": calibration_status_payload(),
        }

    @app.get("/api/calibration/cameras/{camera_id}/snapshot")
    def get_camera_calibration_snapshot(camera_id: int) -> FileResponse:
        path = _calibration_snapshot_path(camera_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Calibration snapshot not found")
        return FileResponse(path)

    @app.post("/api/calibration/cameras/{camera_id}/snapshot")
    async def upload_camera_calibration_snapshot(camera_id: int, file: UploadFile = File(...)) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Snapshot file is required")
        content = await file.read()
        await file.close()
        if not content:
            raise HTTPException(status_code=400, detail="Snapshot upload was empty")
        path = _calibration_snapshot_path(camera_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"saved": True, "camera_id": camera_id, "path": str(path)}

    @app.post("/api/calibration/cameras/{camera_id}/capture")
    def capture_camera_calibration_snapshot(camera_id: int) -> dict[str, Any]:
        frame = _synthetic_live_frame(camera_id)
        path = _calibration_snapshot_path(camera_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(path), frame)
        if not ok:
            raise HTTPException(status_code=500, detail="Could not save captured snapshot")
        return {
            "saved": True,
            "camera_id": camera_id,
            "image_size": [frame.shape[1], frame.shape[0]],
            "snapshot_url": f"/api/calibration/cameras/{camera_id}/snapshot",
        }

    @app.post("/api/calibration/import")
    async def import_calibration(file: UploadFile = File(...)) -> dict[str, Any]:
        if not file.filename or not file.filename.lower().endswith(".json"):
            raise HTTPException(status_code=400, detail="Calibration upload must be a JSON file")
        content = await file.read()
        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Calibration upload must contain valid JSON") from exc
        CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        dest = CALIBRATION_DIR / _clean_name(file.filename, "calibration.json")
        dest.write_bytes(content)
        await file.close()
        if data.get("method") == "manual_pitch_markers" and data.get("camera_id") is not None:
            from core.pitch_calibration import refresh_readiness_from_profiles

            refresh_readiness_from_profiles()
        return {"saved": True, "path": str(dest), "status": calibration_status_payload()}

    @app.get("/api/testing/models")
    def list_testing_models() -> dict[str, Any]:
        """List the .pt models available for testing, grouped by folder. The Testing
        page populates its Model dropdown from this so the model the operator picks is
        the one actually loaded for analysis (no hidden default)."""
        from config.settings import BASE_DIR

        models_root = BASE_DIR / "models"

        def scan(label: str, folder: Path) -> dict[str, Any] | None:
            if not folder.exists():
                return None
            items = []
            for path in sorted(folder.glob("*.pt")):
                try:
                    size_mb = round(path.stat().st_size / 1_000_000, 1)
                except OSError:
                    size_mb = None
                items.append({"name": path.name, "path": str(path), "size_mb": size_mb})
            return {"group": label, "models": items} if items else None

        def scan_recursive(label: str, folder: Path | None) -> dict[str, Any] | None:
            if folder is None or not folder.exists():
                return None
            items = []
            for path in sorted(folder.rglob("*.pt")):
                try:
                    size_mb = round(path.stat().st_size / 1_000_000, 1)
                except OSError:
                    size_mb = None
                items.append({"name": str(path.relative_to(folder)).replace("\\", "/"), "path": str(path), "size_mb": size_mb})
            return {"group": label, "models": items} if items else None

        # Vision Studio workspace models (the handoff): read the workspace root from the
        # dev config and list <workspace>/Models/**.pt so freshly-trained models show up.
        vs_models = None
        try:
            import yaml
            _cfg = yaml.safe_load((BASE_DIR / "config" / "development.yaml").read_text(encoding="utf-8")) or {}
            _ws = (_cfg.get("vision_studio") or {}).get("workspace")
            if _ws:
                _ws_path = Path(_ws)
                _ws_path = _ws_path if _ws_path.is_absolute() else BASE_DIR / _ws_path
                vs_models = _ws_path / "Models"
        except Exception:
            vs_models = None

        groups = [g for g in (
            scan("Production", models_root / "production"),
            scan("Candidates", models_root / "candidates"),
            scan_recursive("Vision Studio", vs_models),
            scan("Models", models_root),  # top-level *.pt (non-recursive)
        ) if g]
        default = next((g["models"][0]["path"] for g in groups if g["group"] == "Production" and g["models"]), None)
        if default is None and groups and groups[0]["models"]:
            default = groups[0]["models"][0]["path"]
        return {"groups": groups, "default": default}

    @app.post("/api/testing/promote")
    def promote_model(payload: dict = Body(default_factory=dict)) -> dict[str, Any]:
        """Promote a tested model to production. Delegates to the model registry —
        the SINGLE promotion implementation — so this and /api/models/promote behave
        identically. Accepts a registry id or an arbitrary model_path (browsed file)."""
        raw = str(payload.get("id") or payload.get("model_path") or "")
        if not raw:
            raise HTTPException(status_code=400, detail="id or model_path required")
        reg = ModelRegistry()
        by = payload.get("by") or "operator"
        reason = payload.get("reason")
        try:
            if reg.get(raw) is not None:
                rec = reg.promote(raw, reason=reason, by=by)          # a registry model
            else:
                rec = reg.promote_source(raw, reason=reason, by=by)   # external/browsed .pt
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _pipeline_cache.clear()  # live/testing analysis reloads the new production model
        log.info("[API] Promoted {} to production", rec.name)
        return {"ok": True, "promoted": rec.name, "production": rec.path, "archived_previous": None}

    @app.post("/api/testing/rollback")
    def rollback_model() -> dict[str, Any]:
        """Roll production back to the previous model. Delegates to the registry."""
        try:
            rec = ModelRegistry().rollback(by="operator")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _pipeline_cache.clear()
        log.info("[API] Rolled back production to {}", rec.name)
        return {"ok": True, "restored": rec.name}

    # --- LBW ground-truth validation (engineer tooling) -------------------- #
    # Thin wiring over core.lbw_validation. The engine is interface-independent;
    # the CLI (scripts/validate_lbw.py) drives the exact same code.
    @app.get("/api/testing/validation/manifest")
    def validation_manifest() -> dict[str, Any]:
        """The labelled validation set: clips + their known (expected) verdicts."""
        from dataclasses import asdict as _asdict

        try:
            m = lbw_validation.load_manifest()
        except FileNotFoundError:
            return {"description": "", "defaults": {}, "clips": [], "exists": False}
        return {
            "description": m.description,
            "defaults": m.defaults,
            "clips": [_asdict(c) for c in m.clips],
            "exists": True,
        }

    @app.get("/api/testing/validation/runs")
    def validation_runs() -> dict[str, Any]:
        """History of validation runs (compact summaries) + current run state."""
        return {"runs": lbw_validation.load_history(), "state": dict(_validation_state)}

    @app.get("/api/testing/validation/runs/{run_id}")
    def validation_run_detail(run_id: str) -> dict[str, Any]:
        """Full per-clip report for one run."""
        safe = "".join(ch for ch in run_id if ch.isalnum() or ch in {"_", "-"})
        report = lbw_validation.RUNS_DIR / safe / "report.json"
        if not report.exists():
            raise HTTPException(status_code=404, detail="Validation run not found")
        return json.loads(report.read_text(encoding="utf-8"))

    @app.post("/api/testing/validation/run")
    def validation_run_start(
        background_tasks: BackgroundTasks, payload: dict = Body(default_factory=dict)
    ) -> dict[str, Any]:
        """Kick off a validation run in the background (one at a time)."""
        if _validation_state.get("status") == "running":
            raise HTTPException(status_code=409, detail="A validation run is already in progress")
        try:
            manifest = lbw_validation.load_manifest()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not manifest.clips:
            raise HTTPException(status_code=400, detail="Validation manifest has no clips")
        model = payload.get("model") or None
        calibration = payload.get("calibration") or None
        limit = payload.get("limit")
        background_tasks.add_task(_run_validation, model, calibration, limit)
        return {"status": "queued", "clips": len(manifest.clips)}

    # --- Model Manager (single source of truth for every detector model) --- #
    @app.get("/api/models")
    def models_list() -> dict[str, Any]:
        reg = ModelRegistry()
        return {
            "models": [r.to_dict() for r in reg.list()],
            "history": reg.deployment_history()[-20:],
            "compare": {k: v for k, v in _compare_state.items() if k != "result"},
        }

    @app.post("/api/models/promote")
    def models_promote(payload: dict = Body(default_factory=dict)) -> dict[str, Any]:
        model_id = str(payload.get("id") or "")
        try:
            rec = ModelRegistry().promote(model_id, reason=payload.get("reason"), by=payload.get("by") or "operator")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _pipeline_cache.clear()  # next analysis reloads the newly-promoted production model
        return {"ok": True, "production": rec.to_dict()}

    @app.post("/api/models/rollback")
    def models_rollback() -> dict[str, Any]:
        try:
            rec = ModelRegistry().rollback()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _pipeline_cache.clear()
        return {"ok": True, "production": rec.to_dict()}

    @app.post("/api/models/archive")
    def models_archive(payload: dict = Body(default_factory=dict)) -> dict[str, Any]:
        try:
            rec = ModelRegistry().archive_model(str(payload.get("id") or ""))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "model": rec.to_dict()}

    @app.post("/api/models/delete")
    def models_delete(payload: dict = Body(default_factory=dict)) -> dict[str, Any]:
        try:
            result = ModelRegistry().delete(str(payload.get("id") or ""))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/models/notes")
    def models_notes(payload: dict = Body(default_factory=dict)) -> dict[str, Any]:
        try:
            rec = ModelRegistry().set_notes(str(payload.get("id") or ""), str(payload.get("notes") or ""))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "model": rec.to_dict()}

    @app.post("/api/models/compare")
    def models_compare(background_tasks: BackgroundTasks, payload: dict = Body(default_factory=dict)) -> dict[str, Any]:
        if _compare_state.get("status") == "running":
            raise HTTPException(status_code=409, detail="A comparison is already in progress")
        reg = ModelRegistry()
        model_a = payload.get("model_a")  # a registry id/path, or null for the default/production model
        model_b = payload.get("model_b")
        for m in (model_a, model_b):
            if m and reg.get(str(m)) is None:
                raise HTTPException(status_code=404, detail=f"Model not found: {m}")
        try:
            manifest = lbw_validation.load_manifest()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not manifest.clips:
            raise HTTPException(status_code=400, detail="Validation manifest has no clips to compare on")
        # resolve ids to real paths for the pipeline
        path_a = str(reg.get(str(model_a)).path) if model_a else None
        path_b = str(reg.get(str(model_b)).path) if model_b else None
        background_tasks.add_task(_run_compare, path_a, path_b, payload.get("limit"))
        return {"status": "queued", "clips": len(manifest.clips)}

    @app.get("/api/models/compare")
    def models_compare_state() -> dict[str, Any]:
        return dict(_compare_state)

    @app.post("/api/testing/uploads")
    async def upload_only(
        video: UploadFile = File(...),
    ) -> dict[str, Any]:
        """Upload a clip WITHOUT queuing the full analysis pipeline. Used by the
        Testing page's per-review-type runner: selecting Wide / Run Out / Stumping
        uploads here, then POST /api/testing/jobs/{id}/review/{type} runs ONLY that
        module through the shared ReviewEngine — no LBW code executes."""
        job_id = uuid.uuid4().hex[:12]
        job_upload_dir = UPLOAD_DIR / job_id
        job_upload_dir.mkdir(parents=True, exist_ok=True)
        path = await _save_upload(video, job_upload_dir / _clean_name(video.filename, "camera_0.mp4"))
        db.create_job(job_id, "upload_only", {"upload_only": True}, path, None)
        db.update_job(job_id, "uploaded")
        return {"job_id": job_id, "status": "uploaded", "video": path.name}

    @app.post("/api/testing/jobs")
    async def create_job(
        background_tasks: BackgroundTasks,
        video_a: UploadFile = File(...),
        video_b: UploadFile | None = File(default=None),
        video_c: UploadFile | None = File(default=None),
        video_d: UploadFile | None = File(default=None),
        video_e: UploadFile | None = File(default=None),
        video_f: UploadFile | None = File(default=None),
        options_json: str = Form(default="{}"),
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        try:
            options_data = json.loads(options_json or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="options_json must be valid JSON") from exc
        options = AnalysisOptions(**{key: value for key, value in options_data.items() if key in AnalysisOptions.__dataclass_fields__})
        job_upload_dir = UPLOAD_DIR / job_id
        job_upload_dir.mkdir(parents=True, exist_ok=True)
        video_a_path = await _save_upload(video_a, job_upload_dir / _clean_name(video_a.filename, "camera_0.mp4"))
        videos = [video_a_path]
        secondary_path = None
        for index, upload in enumerate([video_b, video_c, video_d, video_e, video_f], start=1):
            if upload is not None and upload.filename:
                saved = await _save_upload(upload, job_upload_dir / _clean_name(upload.filename, f"camera_{index}.mp4"))
                videos.append(saved)
                secondary_path = secondary_path or saved
        mode = f"{len(videos)}_camera"
        db.create_job(job_id, mode, options_data, video_a_path, secondary_path)
        background_tasks.add_task(_run_job, job_id, videos, options)
        return {"job_id": job_id, "mode": mode, "status": "queued"}

    @app.post("/api/analyze")
    async def analyze_video(
        background_tasks: BackgroundTasks,
        video: UploadFile = File(...),
        options_json: str = Form(default="{}"),
    ) -> dict[str, Any]:
        response = await create_job(
            background_tasks=background_tasks,
            video_a=video,
            video_b=None,
            video_c=None,
            video_d=None,
            video_e=None,
            video_f=None,
            options_json=options_json,
        )
        job_id = response["job_id"]
        job_progress[job_id] = _initial_job_progress(job_id)
        return {"job_id": job_id, "status": "processing", "websocket_channel": f"/ws/job/{job_id}"}

    @app.post("/api/analyze/calibrated")
    async def analyze_video_calibrated(
        background_tasks: BackgroundTasks,
        video: UploadFile = File(...),
        calibration_profile_id: str = Form(...),
        options_json: str = Form(default="{}"),
    ) -> dict[str, Any]:
        profiles = _load_calibration_profiles()
        if not any(profile.get("id") == calibration_profile_id for profile in profiles):
            raise HTTPException(status_code=404, detail="Calibration profile not found")
        response = await analyze_video(background_tasks, video, options_json)
        response["calibration_profile_id"] = calibration_profile_id
        return response

    @app.get("/api/analyze/{job_id}/status")
    def analyze_status(job_id: str) -> dict[str, Any]:
        job_id = _clean_job_id(job_id)
        if job_id == "test":
            return {"status": "complete", "progress": 100, "current_step": "Complete", "frames_processed": 0}
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        progress = job_progress.get(job_id, _initial_job_progress(job_id))
        if job["status"] == "completed":
            progress.update({"status": "complete", "progress": 100, "current_step": "Complete"})
        elif job["status"] == "failed":
            progress.update({"status": "error", "current_step": job.get("error") or "Analysis failed"})
        return progress

    @app.get("/api/analyze/{job_id}")
    def analyze_alias(job_id: str) -> dict[str, Any]:
        job_id = _clean_job_id(job_id)
        if job_id == "test":
            return {"job_id": "test", "status": "complete", "progress": 100, "current_step": "Complete"}
        return analyze_status(job_id)

    @app.get("/api/analyze/{job_id}/results")
    def analyze_results(job_id: str) -> dict[str, Any]:
        job_id = _clean_job_id(job_id)
        if job_id == "test":
            return _sample_results_payload("test", _sample_processing_decision([1]))
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] != "completed" or not job.get("result"):
            raise HTTPException(status_code=409, detail="Analysis is not complete")
        return _dashboard_results_payload(job_id, job["result"])

    @app.get("/api/analyze/{job_id}/animation")
    def analyze_animation(job_id: str) -> FileResponse:
        job_id = _clean_job_id(job_id)
        job = db.get_job(job_id)
        if job is None or not job.get("result"):
            raise HTTPException(status_code=404, detail="Completed job not found")
        path = Path(job["result"].get("exports", {}).get("animation_video", ""))
        if not path.exists():
            raise HTTPException(status_code=404, detail="Animation export not available")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/testing/jobs/{job_id}/review/{review_type}")
    def run_review_on_job(job_id: str, review_type: str) -> dict[str, Any]:
        """Run ONE review type on an uploaded clip through the shared ReviewEngine —
        the exact code path the live 'Request Review' uses, just fed from the recorded
        file. Lets Wide / No Ball / Run Out / Edge be validated on recorded 120/240 FPS
        deliveries without a live camera rig."""
        job_id = _clean_job_id(job_id)
        job_dir = UPLOAD_DIR / job_id
        videos = sorted(job_dir.glob("*.mp4")) if job_dir.exists() else []
        if not videos:
            raise HTTPException(status_code=404, detail="No uploaded video for this job")
        from core.review_engine import run_review_on_video

        analysis = run_review_on_video(str(videos[0]), review_type)
        if analysis is None:
            raise HTTPException(status_code=422, detail="Could not read frames or unknown review type")
        return {"job_id": job_id, "review_type": review_type.lower(), "video": videos[0].name, "analysis": analysis}

    @app.post("/api/test/upload")
    async def upload_test_job(
        background_tasks: BackgroundTasks,
        video_a: UploadFile = File(...),
        video_b: UploadFile | None = File(default=None),
        options_json: str = Form(default="{}"),
    ) -> dict[str, Any]:
        return await create_job(
            background_tasks=background_tasks,
            video_a=video_a,
            video_b=video_b,
            video_c=None,
            video_d=None,
            video_e=None,
            video_f=None,
            options_json=options_json,
        )

    @app.get("/api/testing/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job["output_dir"] = str(OUTPUT_DIR / job_id)
        return job

    @app.get("/api/test/jobs/{job_id}")
    def get_test_job(job_id: str) -> dict[str, Any]:
        return get_job(job_id)

    @app.get("/api/testing/jobs/{job_id}/exports/{export_name}")
    def download_export(job_id: str, export_name: str) -> FileResponse:
        job = db.get_job(job_id)
        if job is None or not job.get("result"):
            raise HTTPException(status_code=404, detail="Completed job not found")
        exports = job["result"].get("exports", {})
        key_map = {
            "json": "json",
            "csv": "csv",
            "pdf": "pdf",
            "video": "analyzed_video",
            "animation": "animation_video",
            "replay_players": "replay_players",
            "replay_review": "replay_review",
        }
        key = key_map.get(export_name)
        if key is None or key not in exports:
            raise HTTPException(status_code=404, detail="Export not available")
        path = Path(exports[key])
        if not path.exists():
            raise HTTPException(status_code=404, detail="Export file missing")
        return FileResponse(path)

    @app.post("/api/testing/jobs/{job_id}/reprocess")
    def reprocess_job(job_id: str, background_tasks: BackgroundTasks, options: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        videos = [Path(job["video_a_path"])]
        if job.get("video_b_path"):
            videos.append(Path(job["video_b_path"]))
        analysis_options = AnalysisOptions(**{key: value for key, value in options.items() if key in AnalysisOptions.__dataclass_fields__})
        db.update_job(job_id, "queued", error=None)
        background_tasks.add_task(_run_job, job_id, videos, analysis_options)
        return {"job_id": job_id, "status": "queued"}

    @app.post("/api/jobs/cleanup-stale")
    def cleanup_stale_endpoint(older_than_minutes: int = Query(default=15, ge=1)) -> dict[str, Any]:
        cleaned, job_ids = cleanup_stale_jobs(older_than_minutes)
        return {"cleaned": cleaned, "job_ids": job_ids}

    return app


def _calibration_snapshot_path(camera_id: int) -> Path:
    return CALIBRATION_DIR / "snapshots" / f"camera_{camera_id}.jpg"


def camera_fps_payload() -> dict[str, Any]:
    now = time.time()
    cameras = []
    for camera_id in range(1, MAX_CAMERAS + 1):
        connected = camera_id <= connected_camera_count
        cameras.append(
            {
                "id": camera_id,
                "fps": 29.2 + ((camera_id % 3) * 0.4) if connected else 0.0,
                "sync_delta_ms": round((camera_id - 1) * 1.7, 2) if connected else None,
                "status": "synthetic" if connected else "offline",
                "mode": analysis_mode["id"],
                "updated_at": now,
                "connected": connected,
                "resolution": "1280x720" if connected else None,
                "latency_ms": round(28.0 + camera_id * 2.1, 1) if connected else None,
                "recording": connected,
                "health": "good" if connected else "offline",
            }
        )
    return {
        "max_cameras": MAX_CAMERAS,
        "connected_count": connected_camera_count,
        "mode": analysis_mode,
        "cameras": cameras,
    }


def system_health_payload() -> dict[str, Any]:
    camera_payload = camera_fps_payload()
    return {
        "cpu_percent": _cpu_percent(),
        "ram_percent": _ram_percent(),
        "gpu": {"available": False, "label": "GPU telemetry unavailable", "percent": None},
        "camera_fps": {str(item["id"]): item["fps"] for item in camera_payload["cameras"] if item["connected"]},
        "frame_drops": {str(item["id"]): (item["id"] - 1) for item in camera_payload["cameras"] if item["connected"]},
        "latency_ms": round(34.0 + connected_camera_count * 3.2, 1),
        "storage": _storage_payload(),
        "network": {"hostname": socket.gethostname(), "status": "local", "latency_ms": 1.0},
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def resolve_dashboard_decision(camera_ids: list[int], status: str | None = None) -> dict[str, Any]:
    job = db.get_latest_completed_job()
    if job and job.get("result") and job["result"].get("summary"):
        decision = map_summary_to_dashboard_decision(job["result"]["summary"], job["id"])
        decision["camera_ids"] = camera_ids
        decision["analysis_mode"] = dict(analysis_mode)
        if status:
            decision["status"] = status
            decision["outcome"] = "Processing review" if status == "PROCESSING" else decision.get("outcome")
        return decision
    return _empty_decision(camera_ids, status)


def _empty_decision(camera_ids: list[int], status: str | None = None) -> dict[str, Any]:
    return {
        "status": status or "REVIEW_INCONCLUSIVE",
        "outcome": "Review inconclusive",
        "time": datetime.now().isoformat(timespec="seconds"),
        "over": "--",
        "ball": "--",
        "decision": "REVIEW INCONCLUSIVE",
        "camera_ids": camera_ids,
        "analysis_mode": dict(analysis_mode),
        "ball_confidence": 0.0,
        "tracking_confidence": 0.0,
        "calibration_confidence": calibration_status_payload().get("quality_score", 0.0),
        "prediction_confidence": 0.0,
        "model_confidence": 0.0,
        "overall_confidence": 0.0,
        "trajectory": [],
        "timeline": [{"label": "Upload delivery", "status": "active"}],
        "explanation": "No completed analysis job found. Upload a delivery in the testing platform, then request review.",
    }


def schedule_broadcast(channel: str, payload: dict[str, Any]) -> None:
    # Called from two contexts: (1) inside the event loop (schedule directly) and
    # (2) from a job worker thread with no running loop — where the old code hit
    # RuntimeError and silently dropped the event. Marshal onto the captured serving
    # loop instead so /ws/job/{id} subscribers actually receive progress.
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(ws_hub.broadcast(channel, payload))
        return
    except RuntimeError:
        pass
    loop = _main_loop
    if loop is not None and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(ws_hub.broadcast(channel, payload), loop)
        except Exception:
            pass


def schedule_job_broadcast(job_id: str, payload: dict[str, Any]) -> None:
    schedule_broadcast(WSBroadcastHub.job_channel(job_id), payload)


def _clean_job_id(job_id: str) -> str:
    return "".join(char for char in str(job_id) if char.isalnum() or char in {"_", "-"})[:48]


def _initial_job_progress(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "processing",
        "progress": 0,
        "current_step": "Queued",
        "frames_processed": 0,
        "frames_done": 0,
        "ball_detected": 0,
    }


def _update_job_progress(job_id: str, step: str, percent: int, **extra: Any) -> None:
    payload = {
        **job_progress.get(job_id, _initial_job_progress(job_id)),
        "status": "processing" if percent < 100 else "complete",
        "progress": max(0, min(100, int(percent))),
        "current_step": step,
        **extra,
    }
    job_progress[job_id] = payload
    schedule_job_broadcast(
        job_id,
        {
            "type": "progress",
            "step": step,
            "percent": payload["progress"],
            "frames_done": payload.get("frames_done", payload.get("frames_processed", 0)),
            **extra,
        },
    )


def _load_calibration_profiles() -> list[dict[str, Any]]:
    if not CALIBRATION_PROFILES_PATH.exists():
        CALIBRATION_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION_PROFILES_PATH.write_text("[]\n", encoding="utf-8")
    try:
        data = json.loads(CALIBRATION_PROFILES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = []
    return data if isinstance(data, list) else []


def _save_calibration_profiles(profiles: list[dict[str, Any]]) -> None:
    CALIBRATION_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PROFILES_PATH.write_text(json.dumps(profiles, indent=2), encoding="utf-8")


def _default_world_points() -> list[list[float]]:
    return [
        [-1.22, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [1.22, 0.0, 0.0],
        [-1.22, 1.22, 0.0],
        [0.0, 1.22, 0.0],
        [1.22, 1.22, 0.0],
        [-0.1143, 20.12, 0.711],
        [0.0, 20.12, 0.711],
        [0.1143, 20.12, 0.711],
    ]


def _estimate_rms_error_px(image_points: list[Any]) -> float:
    try:
        pts = np.asarray(image_points, dtype=float)
        if pts.shape != (9, 2):
            return 99.0
        center = pts.mean(axis=0)
        spread = np.linalg.norm(pts - center, axis=1).mean()
        return round(max(1.0, min(12.0, 900.0 / max(spread, 1.0))), 2)
    except Exception:
        return 99.0


def _identity_camera_matrix() -> list[list[float]]:
    return [[1200.0, 0.0, 640.0], [0.0, 1200.0, 360.0], [0.0, 0.0, 1.0]]


def _decode_base64_image(image_data: str) -> np.ndarray | None:
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    try:
        raw = base64.b64decode(image_data)
    except Exception:
        return None
    data = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _auto_detect_reference_points(frame: np.ndarray) -> tuple[list[list[float]], float]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 180)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=90, minLineLength=120, maxLineGap=18)
    h, w = frame.shape[:2]
    if lines is None or len(lines) < 2:
        return _fallback_image_points(w, h), 0.25
    horizontal = []
    for line in lines[:80]:
        x1, y1, x2, y2 = [int(value) for value in line[0]]
        if abs(y2 - y1) < max(8, abs(x2 - x1) * 0.08):
            horizontal.append((x1, y1, x2, y2))
    if len(horizontal) < 2:
        return _fallback_image_points(w, h), 0.35
    ys = sorted({int((line[1] + line[3]) / 2) for line in horizontal})
    top = ys[max(0, len(ys) // 3 - 1)]
    mid = ys[min(len(ys) - 1, (len(ys) * 2) // 3)]
    return [
        [w * 0.32, top],
        [w * 0.50, top],
        [w * 0.68, top],
        [w * 0.32, mid],
        [w * 0.50, mid],
        [w * 0.68, mid],
        [w * 0.46, h * 0.34],
        [w * 0.50, h * 0.34],
        [w * 0.54, h * 0.34],
    ], 0.55


def _fallback_image_points(width: int, height: int) -> list[list[float]]:
    return [
        [width * 0.32, height * 0.68],
        [width * 0.50, height * 0.68],
        [width * 0.68, height * 0.68],
        [width * 0.32, height * 0.54],
        [width * 0.50, height * 0.54],
        [width * 0.68, height * 0.54],
        [width * 0.46, height * 0.34],
        [width * 0.50, height * 0.34],
        [width * 0.54, height * 0.34],
    ]


def _sample_processing_decision(camera_ids: list[int] | None = None) -> dict[str, Any]:
    camera_ids = camera_ids or [1]
    confidence_parts = {
        "ball_confidence": 0.82,
        "tracking_confidence": min(0.94, 0.66 + len(camera_ids) * 0.055),
        "calibration_confidence": 0.74,
        "prediction_confidence": 0.79,
        "model_confidence": 0.81,
    }
    overall = sum(confidence_parts.values()) / len(confidence_parts)
    now = datetime.now().isoformat(timespec="seconds")
    over = f"{12 + (len(review_history) // 6)}.{(len(review_history) % 6) + 1}"
    return {
        "status": "PROCESSING",
        "outcome": "Processing review",
        "time": now,
        "over": over,
        "ball": str((len(review_history) % 6) + 1),
        "decision": "PROCESSING",
        **confidence_parts,
        "overall_confidence": round(overall, 3),
        "impact_point": {"x": 382, "y": 86},
        "wicket_zone_status": "Clipping leg stump",
        "ball_speed_kmh": 128.4,
        "camera_ids": camera_ids,
        "analysis_mode": dict(analysis_mode),
        "trajectory": [
            {"x": -9.4, "y": 0.9, "z": 1.55, "confidence": 0.66},
            {"x": -6.2, "y": 0.62, "z": 1.15, "confidence": 0.76},
            {"x": -3.2, "y": 0.28, "z": 0.52, "confidence": 0.83},
            {"x": -1.2, "y": 0.12, "z": 0.05, "confidence": 0.86},
            {"x": 1.4, "y": 0.04, "z": 0.42, "confidence": 0.82},
            {"x": 4.7, "y": -0.12, "z": 0.71, "confidence": 0.79},
        ],
        "bounce_point": {"x": -1.2, "y": 0.12, "z": 0.05},
        "impact_marker": {"x": 4.7, "y": -0.12, "z": 0.71},
        "predicted_extension": [
            {"x": 4.7, "y": -0.12, "z": 0.71},
            {"x": 6.15, "y": -0.17, "z": 0.74},
            {"x": 7.0, "y": -0.21, "z": 0.76},
        ],
        "wicket_zone": {"x": 412, "y": 64, "w": 18, "h": 42},
        "wicket_prediction": {"stump": "leg", "umpire_call": True, "collision": {"x": 7.0, "y": -0.21, "z": 0.76}},
        "timeline": [
            {"label": "Appeal", "status": "complete"},
            {"label": "Ball Detected", "status": "complete"},
            {"label": "Bounce Detected", "status": "complete"},
            {"label": "Impact Detected", "status": "complete"},
            {"label": "Wicket Predicted", "status": "complete"},
            {"label": "Decision Generated", "status": "active"},
            {"label": "Umpire Call", "status": "pending"},
        ],
        "explanation": "Projected path clips leg stump; confidence is gated by calibration and tracking quality.",
    }


def _dashboard_results_payload(job_id: str, result: dict[str, Any]) -> dict[str, Any]:
    cameras = result.get("cameras") or []
    first_camera = cameras[0] if cameras else {}
    summary = result.get("summary") or {}
    decision = map_summary_to_dashboard_decision(summary, job_id)
    tracks = first_camera.get("tracking_points") or []
    detections = first_camera.get("detections") or []
    detected = [item for item in detections if item.get("confidence", 0.0) > 0]
    fps = float(first_camera.get("fps") or 0.0)
    frames = int(first_camera.get("frames_processed") or 0)
    width = int(first_camera.get("width") or 0)
    height = int(first_camera.get("height") or 0)
    duration_s = round(frames / fps, 3) if fps else 0.0
    avg_confidence = float(np.mean([item.get("confidence", 0.0) for item in detected])) if detected else 0.0
    detection_rate = len(detected) / max(1, frames)
    bounce = first_camera.get("bounce_point_px")
    impact = first_camera.get("impact_point_px")
    # Prefer the canonical trajectory the pipeline now emits (observed + predicted +
    # validity). Fall back to the legacy synthesis only for older jobs that predate it,
    # so the frontend consumes exactly one trajectory contract when it's available.
    canonical_trajectory = result.get("trajectory")
    trajectory_payload = (
        canonical_trajectory
        if isinstance(canonical_trajectory, dict) and "observed" in canonical_trajectory
        else {
            "release_point": _track_point_to_world(tracks[0]) if tracks else None,
            "bounce_point": _point_list_to_xyz(bounce),
            "impact_point": _point_list_to_xyz(impact),
            "predicted_stumps": decision.get("wicket_prediction"),
            "points": decision.get("trajectory", []),
        }
    )
    payload = {
        **result,
        "job_id": job_id,
        "video_info": {
            "duration_s": duration_s,
            "fps": fps,
            "resolution": [width, height],
            "total_frames": frames,
            "filename": Path(first_camera.get("source_video", "")).name if first_camera.get("source_video") else "",
        },
        "ball_tracking": {
            "frames_tracked": len(tracks),
            "detection_rate": round(detection_rate, 3),
            "avg_confidence": round(avg_confidence, 3),
        },
        "trajectory": trajectory_payload,
        "diagnostics": result.get("diagnostics"),
        "lbw_gates": _lbw_gate_payload(summary, decision),
        "decision": {
            "verdict": _normal_verdict(decision.get("status") or decision.get("decision")),
            "confidence": float(decision.get("overall_confidence") or summary.get("confidence_score") or 0.0),
            "explanation": decision.get("explanation") or summary.get("explanation") or "Analysis complete.",
        },
        "exports": result.get("exports", {}),
        "animation_frames": result.get("exports", {}).get("animation_video"),
    }
    return payload


def _sample_results_payload(job_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "completed",
        "video_info": {"duration_s": 5.0, "fps": 30.0, "resolution": [1280, 720], "total_frames": 150, "filename": "test"},
        "ball_tracking": {"frames_tracked": 94, "detection_rate": 0.72, "avg_confidence": 0.81},
        "trajectory": {
            "release_point": {"x": -9.4, "y": 0.9, "z": 1.55},
            "bounce_point": decision.get("bounce_point"),
            "impact_point": decision.get("impact_marker"),
            "predicted_stumps": decision.get("wicket_prediction"),
            "points": decision.get("trajectory", []),
        },
        "lbw_gates": _lbw_gate_payload({}, decision),
        "decision": {"verdict": "UMPIRES_CALL", "confidence": 0.82, "explanation": decision.get("explanation", "")},
        "exports": {},
        "animation_frames": None,
    }


def _lbw_gate_payload(summary: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    gates = summary.get("gate", {}) if isinstance(summary, dict) else {}
    failed = set(gates.get("failed_gates", []) or [])
    return {
        "pitching": {
            "result": "outside_leg" if "pitching" in failed else "in_line",
            "confidence": float(decision.get("calibration_confidence") or 0.74),
        },
        "impact": {
            "result": "outside_leg" if "impact" in failed else "in_line",
            "confidence": float(decision.get("tracking_confidence") or 0.74),
            "height_m": float(summary.get("impact_height_m") or 0.41),
        },
        "wickets": {
            "result": "missing" if "wickets" in failed else "hitting",
            "confidence": float(decision.get("prediction_confidence") or 0.79),
            "stumps_hit": ["middle", "leg"] if "wickets" not in failed else [],
        },
        "overall": {"confidence": float(decision.get("overall_confidence") or summary.get("confidence_score") or 0.0)},
    }


def _normal_verdict(value: Any) -> str:
    raw = str(value or "REVIEW_INCONCLUSIVE").upper().replace(" ", "_")
    if raw in {"OUT", "NOT_OUT"}:
        return raw
    if "UMPIRE" in raw:
        return "UMPIRES_CALL"
    return "NOT_OUT" if "NOT" in raw else "OUT" if raw == "OUT" else "UMPIRES_CALL"


def _point_list_to_xyz(point: Any) -> dict[str, float] | None:
    if not isinstance(point, list) or len(point) < 2:
        return None
    return {"x": float(point[0]), "y": float(point[1]), "z": 0.0}


def _track_point_to_world(point: dict[str, Any]) -> dict[str, float]:
    return {
        "x": float(point.get("x", 0.0)) / 100.0,
        "y": float(point.get("y", 0.0)) / 100.0,
        "z": 0.2,
    }


def _synthetic_live_frame(camera_id: int, thermal_overlay: bool = False) -> np.ndarray:
    width, height = 960, 540
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (20, 60, 34) if camera_id == 1 else (45, 48, 58)
    cv2.rectangle(frame, (0, height // 2), (width, height), (35, 92, 48), -1)
    cv2.rectangle(frame, (width // 2 - 70, 80), (width // 2 + 70, height - 40), (178, 162, 114), -1)
    cv2.line(frame, (width // 2 + 140, 80), (width // 2 + 140, height - 40), (235, 235, 220), 3)
    for offset in (-18, 0, 18):
        cv2.line(frame, (width // 2 + 190 + offset, 170), (width // 2 + 190 + offset, 310), (238, 238, 220), 5)
    t = time.time()
    x = int(120 + ((t * 220 + camera_id * 80) % 650))
    y = int(140 + 90 * np.sin(t * 2.4 + camera_id))
    cv2.circle(frame, (x, y), 10, (245, 245, 245), -1, cv2.LINE_AA)
    if thermal_overlay:
        heat = np.zeros_like(frame)
        cv2.circle(heat, (x, y), 62, (0, 120, 255), -1, cv2.LINE_AA)
        cv2.rectangle(heat, (width // 2 + 160, 160), (width // 2 + 230, 326), (0, 70, 210), -1)
        frame = cv2.addWeighted(frame, 0.62, heat, 0.38, 0)
        cv2.putText(frame, "DEMO THERMAL OVERLAY - SIMULATED", (24, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 210, 255), 2)
    cv2.putText(frame, f"Synthetic camera {camera_id}", (24, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (245, 245, 245), 2)
    cv2.putText(frame, datetime.now().strftime("%H:%M:%S"), (24, height - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 2)
    return frame


def _validated_camera_ids(raw: Any) -> list[int]:
    ids = []
    for value in raw if isinstance(raw, list) else [raw]:
        try:
            camera_id = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= camera_id <= MAX_CAMERAS and camera_id <= connected_camera_count:
            ids.append(camera_id)
    return sorted(set(ids)) or [1]


def _store_review(decision: dict[str, Any]) -> None:
    review_history.append(
        {
            "id": uuid.uuid4().hex[:10],
            "time": decision.get("time") or datetime.now().isoformat(timespec="seconds"),
            "over": decision.get("over", "--"),
            "ball": decision.get("ball", "--"),
            "decision": decision.get("decision") or decision.get("outcome") or decision.get("status"),
            "confidence": decision.get("overall_confidence") or decision.get("ball_confidence"),
            "replay": {"available": True, "engine": "shared_replay_engine"},
            "trajectory": decision.get("trajectory", []),
            "timeline": decision.get("timeline", []),
            "analysis_mode": decision.get("analysis_mode", analysis_mode),
        }
    )


def _cpu_percent() -> float | None:
    # Honest: if psutil isn't available we report None ("--" in the UI) rather than a
    # sine-wave fake that reads as a real load graph. psutil is in requirements.txt.
    try:
        import psutil

        return float(psutil.cpu_percent(interval=None))
    except Exception:
        return None


def _ram_percent() -> float | None:
    try:
        import psutil

        return float(psutil.virtual_memory().percent)
    except Exception:
        return None


def _storage_payload() -> dict[str, Any]:
    usage = shutil.disk_usage(os.getcwd())
    return {
        "free_gb": round(usage.free / (1024**3), 2),
        "used_gb": round(usage.used / (1024**3), 2),
        "total_gb": round(usage.total / (1024**3), 2),
    }


def cleanup_stale_jobs(older_than_minutes: int = 15) -> tuple[int, list[str]]:
    return db.cleanup_stale_processing_jobs(older_than_minutes)


async def _save_upload(upload: UploadFile, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    await upload.close()
    return dest


def _clean_name(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback
    safe = "".join(char for char in Path(filename).name if char.isalnum() or char in {".", "_", "-"})
    return safe or fallback


# Analysis jobs are SERIALIZED: one pipeline run at a time. Two concurrent runs double the
# YOLO/tracking memory and (with the replay renders on top) exhausted system commit —
# Windows resource-exhaustion events killed the whole desktop app (observed 2026-07-17,
# python.exe at 5.7GB with two overlapping jobs). A second Analyze click now WAITS in the
# queue instead of running in parallel.
_job_lock = threading.Lock()


def _run_job(job_id: str, videos: list[Path], options: AnalysisOptions) -> None:
    db.update_job(job_id, "processing")
    job_progress[job_id] = _initial_job_progress(job_id)
    if not _job_lock.acquire(blocking=False):
        _update_job_progress(job_id, "Queued (another analysis is running)...", 2)
        _job_lock.acquire()
    try:
        _run_job_locked(job_id, videos, options)
    finally:
        _job_lock.release()


def _run_job_locked(job_id: str, videos: list[Path], options: AnalysisOptions) -> None:
    _update_job_progress(job_id, "Extracting frames...", 8)
    schedule_broadcast("review", {"type": "job_processing", "job_id": job_id})
    try:
        log.info("[API] Starting analysis for job {} with {} video(s)", job_id, len(videos))
        _update_job_progress(job_id, "Detecting ball...", 25)
        result = _pipeline_for(options.model_path).process(job_id, videos, options)
        frames_done = sum(int(cam.get("frames_processed", 0)) for cam in result.get("cameras", []))
        ball_detected = sum(int(cam.get("real_detection_count", 0)) for cam in result.get("cameras", []))
        _update_job_progress(job_id, "Tracking...", 55, frames_done=frames_done, frames_processed=frames_done, ball_detected=ball_detected)
        db.insert_tracking(job_id, [point for cam in result["cameras"] for point in cam["tracking_points"]])
        _update_job_progress(job_id, "Predicting trajectory...", 72, frames_done=frames_done, frames_processed=frames_done, ball_detected=ball_detected)
        db.update_job(job_id, "completed", result=result)
        _update_job_progress(job_id, "Running LBW analysis...", 88, frames_done=frames_done, frames_processed=frames_done, ball_detected=ball_detected)
        decision = map_summary_to_dashboard_decision(result["summary"], job_id)
        current_decision.update(decision)
        dashboard_results = _dashboard_results_payload(job_id, result)
        job_progress[job_id] = {
            **job_progress.get(job_id, _initial_job_progress(job_id)),
            "status": "complete",
            "progress": 100,
            "current_step": "Complete",
            "frames_processed": frames_done,
            "frames_done": frames_done,
            "ball_detected": ball_detected,
        }
        schedule_broadcast("trajectory", {"type": "trajectory_update", "job_id": job_id, "trajectory": decision.get("trajectory", [])})
        schedule_broadcast("decision", {"type": "decision_update", "job_id": job_id, "decision": decision})
        schedule_broadcast("replay", {"type": "replay_ready", "job_id": job_id, "exports": result.get("exports", {})})
        schedule_job_broadcast(job_id, {"type": "decision_ready", "verdict": dashboard_results["decision"]["verdict"], "confidence": dashboard_results["decision"]["confidence"]})
        schedule_job_broadcast(job_id, {"type": "animation_ready", "animation_url": f"/api/analyze/{job_id}/animation"})
        schedule_job_broadcast(job_id, {"type": "progress", "step": "Complete", "percent": 100, "frames_done": frames_done})
        log.info("[API] Job {} completed successfully", job_id)
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {str(exc)}"
        log.error("[API] Job {} failed with error: {}", job_id, error_msg, exc_info=True)
        db.update_job(job_id, "failed", error=error_msg)
        job_progress[job_id] = {
            **job_progress.get(job_id, _initial_job_progress(job_id)),
            "status": "error",
            "current_step": error_msg,
        }
        schedule_job_broadcast(job_id, {"type": "progress", "step": error_msg, "percent": job_progress[job_id].get("progress", 0)})
        schedule_broadcast("review", {"type": "job_failed", "job_id": job_id, "error": error_msg})


def run_testing_api(host: str, port: int) -> None:
    import uvicorn

    log.info("[API] Starting Cricket DRS Testing API on http://{}:{}", host, port)
    log.info("[API] Loading ball detection model...")

    uvicorn.run(create_testing_app(), host=host, port=port)


app = create_testing_app()
