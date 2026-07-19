"""Offline one-to-six-camera cricket delivery DRS analysis pipeline."""

from __future__ import annotations

import csv
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.ball_association import AssociatedTrackPoint, SingleBallByteTracker
from core.ball_detector import DetectionResult, BallDetector
from core.audio_edge import AudioEdgeDetector
from core.drs_decision import DRSDecisionService
from core.hotspot import HotSpotAnalyzer
from core.pitch_calibration import ManualPitchCalibrator
from core.readiness import ReadinessGate
from core.review_result import CalibratedTrajectoryProducer, ObservedTrajectory, ReviewResult, build_review_result
from core.tracking_quality import TrackingQualityAnalyzer
from core.trajectory import TrajectoryPredictor

TESTING_DATA_DIR = Path("data/testing")
UPLOAD_DIR = TESTING_DATA_DIR / "uploads"
# Where analyzed videos / exports land. Defaults next to the uploads, but can be
# redirected (e.g. alongside the raw recordings, or to an external drive) with
# DRS_TESTING_OUTPUT_DIR, or per-job via AnalysisOptions.output_dir.
OUTPUT_DIR = Path(os.environ.get("DRS_TESTING_OUTPUT_DIR", "").strip() or (TESTING_DATA_DIR / "outputs"))
CALIBRATION_DIR = Path("data/calibration")


def _open_video_writer(path: Path, fps: float, size: tuple[int, int]) -> cv2.VideoWriter:
    """VideoWriter that prefers H.264 (avc1) so the dashboard's browser <video>
    element can actually play the result — mp4v (MPEG-4 Part 2) does not decode in
    Chromium. Falls back to mp4v only if H.264 is unavailable in this OpenCV build."""
    for tag in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*tag), fps, size)
        if writer.isOpened():
            return writer
        writer.release()
    return cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)


@dataclass(slots=True)
class AnalysisOptions:
    ball_detection: bool = True
    ball_tracking: bool = True
    trajectory_prediction: bool = True
    lbw_analysis: bool = True
    edge_detection: bool = False
    replay_generation: bool = True
    max_frames: int | None = None
    confidence_threshold: float = 0.25
    model_path: str | None = None  # explicit .pt to load; None → detector default
    # Process only every Nth frame. 1 = every frame (default). For high-FPS
    # clips (120-240 fps) a stride of e.g. 4-8 samples the delivery down to a
    # sane analysis/training rate and cuts processing time proportionally.
    frame_stride: int = 1
    # Redirect this job's analyzed video / exports to a specific folder
    # (absolute or relative). None → the module OUTPUT_DIR.
    output_dir: str | None = None
    # YOLO inference resolution. Default 640 matches the model's training size and
    # gave the best recall in testing; higher (960/1280) is NOT always better —
    # only raise it if a small/fast ball is being missed in very wide footage.
    imgsz: int = 640
    # CLAHE/sharpen preprocessing before detection. OFF by default: on real footage
    # it corrupted detection (the model locked onto a stationary false positive,
    # 24px spread) while raw frames tracked the real moving ball (554px spread).
    preprocess: bool = False
    # Calibration choice from the UI. None = auto (use a camera-0 profile if one exists);
    # False = force heuristic (the Testing wizard's default — a stale/mismatched profile
    # must NOT silently gate uploads: observed 2026-07-17, a fresh workspace homography
    # made a quality-0.87 track "invalid: implausible release speed 1.5 km/h").
    use_calibration: bool | None = None


@dataclass(slots=True)
class ObjectEstimate:
    label: str
    bbox: dict[str, int] | None
    confidence: float
    source: str
    is_estimated: bool = False


class DeliveryTestingPipeline:
    """Processes uploaded cricket delivery clips into DRS-style evidence."""

    def __init__(self, model_path: Path | str | None = None) -> None:
        self.detector = BallDetector(model_path=model_path, export_results=False)
        self.trajectory = TrajectoryPredictor()
        self.decision_service = DRSDecisionService()
        self.hotspot = HotSpotAnalyzer()
        self.audio_edge = AudioEdgeDetector()
        self.quality = TrackingQualityAnalyzer()
        self.readiness = ReadinessGate()

    def process(self, job_id: str, video_paths: list[Path], options: AnalysisOptions) -> dict[str, Any]:
        if len(video_paths) < 1 or len(video_paths) > 6:
            raise ValueError("Testing platform supports one to six uploaded videos")
        import time as _time
        _t_start = _time.monotonic()
        _t_replay = 0.0

        base_output = Path(options.output_dir) if options.output_dir else OUTPUT_DIR
        job_dir = base_output / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        camera_results = []
        all_tracks: list[dict[str, Any]] = []
        for camera_id, path in enumerate(video_paths):
            result = self._process_camera(job_id, camera_id, path, job_dir, options)
            camera_results.append(result)
            all_tracks.extend(result["tracking_points"])

        sync = self._synchronize(camera_results) if len(camera_results) > 1 else None
        fused = self._fuse_tracks(camera_results)
        replay_fps = min([cam["fps"] for cam in camera_results], default=0.0)
        geometry_source = self._geometry_source(options)
        calibration = self.readiness.calibration()
        sync_readiness = self.readiness.sync(sync, replay_fps)
        edge_analysis = self._analyze_edge(camera_results[0], options) if options.edge_detection else None
        hotspot_analysis = self._analyze_hotspot(camera_results[0], options) if options.edge_detection else None
        decision = self._build_decision(
            fused,
            camera_results,
            bool(sync),
            calibration,
            sync_readiness,
            edge_analysis,
            hotspot_analysis,
        )
        # Canonical review artifact: the ONE trajectory object every surface (animation,
        # diagnostics, replay, export) reads. Built from the fused tracker output via a
        # swappable physics producer — see core/review_result.py.
        primary = camera_results[0] if camera_results else {}
        observed = ObservedTrajectory.from_tracks(
            fused, camera_id=0, fps=float(primary.get("fps") or 0.0)
        )
        pixels_per_meter = max(25.0, float(primary.get("width") or 1280) / 20.12)
        # Impact frame = first tracked point to enter the (estimated) pad region; used to
        # trim the post-impact drift so the trajectory ends at the delivery, not the clip.
        impact_frame = self._impact_frame(fused, primary.get("object_estimates", {}).get("pads"))
        last_frame = int(fused[-1]["frame_id"]) if fused else None
        # Calibrated producer (image→pitch homography) when a profile exists; else heuristic.
        producer = self._trajectory_producer(geometry_source, primary)
        review_result = build_review_result(
            job_id, observed, decision, geometry_source, producer=producer,
            pixels_per_meter=pixels_per_meter, impact_frame=impact_frame, last_frame=last_frame,
        )

        # Broadcast replay package (replaces the old bbox-normalized animation.mp4):
        # the DECISION-SERVICE side (reconstruction: smoothing, bounce, gates) feeds two
        # renderer-only generators. Replay 2 needs headless Chrome and degrades gracefully.
        reconstruction = None
        replay_players_path: Path | None = None
        replay_review_path: Path | None = None
        if options.replay_generation:
            from core.replay_reconstruction import build_replay_reconstruction

            _t0 = _time.monotonic()
            reconstruction = build_replay_reconstruction(
                review_result.trajectory.to_dict(), decision
            )
            if reconstruction is not None:
                from core.replay_broadcast import generate_drs_review_replay
                from core.replay_overlay import generate_replay_with_players

                replay_players_path = generate_replay_with_players(
                    video_paths[0], reconstruction, job_dir / "replay_players.mp4"
                )
                replay_review_path = generate_drs_review_replay(
                    reconstruction, job_dir / "replay_review.mp4"
                )
            _t_replay = _time.monotonic() - _t0

        report_path = self._write_report(job_dir, job_id, camera_results, decision, sync)
        json_path = self._write_json(
            job_dir, job_id, camera_results, decision, sync, geometry_source, review_result
        )
        csv_path = self._write_csv(job_dir, job_id, all_tracks)

        # Per-review performance metrics — recorded on EVERY job (result JSON + activity
        # stream) so regressions in analysis/render time and tracking quality are
        # visible as trends, not anecdotes.
        metrics = {
            "analysis_time_s": round(_time.monotonic() - _t_start, 2),
            "replay_render_time_s": round(_t_replay, 2) if _t_replay else None,
            "tracked_points": len(fused),
            "real_detections": observed.real_count,
            "replay_generated": bool(replay_review_path),
            "detector_model": getattr(self.detector, "active_model_name", "none"),
            "geometry_source": geometry_source,
        }
        try:
            from core import activity_log
            activity_log.record(
                "review_metrics",
                f"Job {job_id}: {metrics['analysis_time_s']}s analysis, "
                f"{metrics['tracked_points']} pts, replay={'yes' if metrics['replay_generated'] else 'no'}",
                **metrics,
            )
        except Exception:  # metrics must never fail a job
            pass

        return {
            "metrics": metrics,
            "job_id": job_id,
            "mode": f"{len(video_paths)}_camera",
            "status": "completed",
            "summary": decision,
            "sync": sync,
            "cameras": camera_results,
            "geometry_source": geometry_source,
            "trajectory": review_result.trajectory.to_dict(),
            "diagnostics": review_result.diagnostics,
            "reconstruction": reconstruction,
            "exports": {
                "json": str(json_path),
                "csv": str(csv_path),
                "pdf": str(report_path),
                "analyzed_video": camera_results[0]["analyzed_video"],
                # the DRS-review render is the animation now (same endpoint, new content)
                "animation_video": str(replay_review_path) if replay_review_path else "",
                "replay_players": str(replay_players_path) if replay_players_path else "",
                "replay_review": str(replay_review_path) if replay_review_path else "",
                "screenshots": [item for cam in camera_results for item in cam["screenshots"]],
            },
            "calibration_status": calibration.to_dict(),
            "sync_status": sync_readiness.to_dict(),
            "model_status": self.detector.model_readiness.to_dict() if self.detector.model_readiness else {},
            "readiness_gates": decision["gate"],
        }

    def _process_camera(
        self,
        job_id: str,
        camera_id: int,
        video_path: Path,
        job_dir: Path,
        options: AnalysisOptions,
    ) -> dict[str, Any]:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        # Frame sampling: keep every `stride`-th frame. stride=1 → every frame.
        # The effective rate drives tracking/speed math and the written video so
        # that a sampled clip still plays and measures at correct wall-clock time.
        stride = max(1, int(getattr(options, "frame_stride", 1) or 1))
        fps = source_fps / stride if stride > 1 else source_fps
        tracker = SingleBallByteTracker(fps=fps)
        output_path = job_dir / ("analyzed_video.mp4" if camera_id == 0 else f"camera_{camera_id}_analyzed.mp4")
        writer = _open_video_writer(output_path, fps, (width, height))

        expected_processed = (total_frames // stride) if total_frames else 0
        milestones = {0, max(0, expected_processed // 2), max(0, expected_processed - 1)}
        frame_id = 0  # index over KEPT (processed) frames
        raw_index = -1  # index over every decoded frame, sampled or not
        detections: list[dict[str, Any]] = []
        object_estimates: dict[str, ObjectEstimate] = {}
        screenshots: list[str] = []
        bounce_point_px: tuple[int, int] | None = None
        impact_point_px: tuple[int, int] | None = None

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            raw_index += 1
            if raw_index % stride != 0:
                continue
            if options.max_frames is not None and frame_id >= options.max_frames:
                break

            timestamp_ms = (raw_index / source_fps) * 1000.0
            detection_result = self._detect_ball(frame, frame_id, timestamp_ms, camera_id, options)
            track_point = tracker.update(detection_result) if options.ball_tracking else None
            estimates = self._estimate_static_objects(frame)
            object_estimates.update({item.label: item for item in estimates})

            annotated = frame.copy()
            if options.ball_detection:
                annotated = self.detector.annotate(annotated, detection_result)
            if options.ball_tracking:
                annotated = tracker.draw(annotated)
            if track_point:
                bounce_point_px = self._estimate_bounce(tracker.history) or bounce_point_px
                impact_point_px = self._estimate_impact(tracker.history, object_estimates.get("pads")) or impact_point_px
            self._draw_drs_overlay(annotated, object_estimates, bounce_point_px, impact_point_px)
            writer.write(annotated)

            if frame_id in milestones:
                shot_path = job_dir / f"camera_{camera_id}_frame_{frame_id}.jpg"
                cv2.imwrite(str(shot_path), annotated)
                screenshots.append(str(shot_path))

            best = detection_result.best
            detections.append(
                {
                    "frame_id": frame_id,
                    "timestamp_ms": timestamp_ms,
                    "camera_id": camera_id,
                    "bbox": best.bbox if best else None,
                    "center": [best.cx, best.cy] if best else None,
                    "confidence": best.confidence if best else 0.0,
                }
            )
            frame_id += 1

        cap.release()
        writer.release()
        tracks = [point.to_dict() for point in tracker.history]
        quality = self.quality.evaluate(detections, tracks)
        speed_px_s = float(np.median([point["speed_px_s"] for point in tracks])) if tracks else 0.0
        pixels_per_meter = max(25.0, width / 20.12)
        speed_kmh = (speed_px_s / pixels_per_meter) * 3.6

        # Transform bounce/impact pixel coords to world coords via calibration
        bounce_world: dict | None = None
        impact_world: dict | None = None
        calibrator = ManualPitchCalibrator()
        profile = calibrator.load_profile(camera_id)
        if profile and profile.homography:
            if bounce_point_px:
                bw = calibrator.pixel_to_pitch_mm(camera_id, float(bounce_point_px[0]), float(bounce_point_px[1]))
                if bw is not None:
                    bounce_world = {'lateral_mm': bw[0], 'along_mm': bw[1], 'pixel_x': bounce_point_px[0], 'pixel_y': bounce_point_px[1]}
            if impact_point_px:
                iw = calibrator.pixel_to_pitch_mm(camera_id, float(impact_point_px[0]), float(impact_point_px[1]))
                if iw is not None:
                    impact_world = {'lateral_mm': iw[0], 'along_mm': iw[1], 'pixel_x': impact_point_px[0], 'pixel_y': impact_point_px[1]}

        return {
            "camera_id": camera_id,
            "source_video": str(video_path),
            "analyzed_video": str(output_path),
            "fps": fps,
            "width": width,
            "height": height,
            "frames_processed": frame_id,
            "detections": detections,
            "tracking_points": tracks,
            "object_estimates": {key: asdict(value) for key, value in object_estimates.items()},
            "ball_speed_kmh": round(speed_kmh, 2),
            "bounce_point_px": list(bounce_point_px) if bounce_point_px else None,
            "impact_point_px": list(impact_point_px) if impact_point_px else None,
            "bounce_world": bounce_world,
            "impact_world": impact_world,
            "screenshots": screenshots,
            "confidence": quality.score,
            "tracking_quality": quality.to_dict(),
            "real_detection_count": sum(1 for point in tracks if point.get("real_detection")),
            "kalman_gap_fill_count": sum(1 for point in tracks if point.get("predicted")),
        }

    def _detect_ball(
        self,
        frame: np.ndarray,
        frame_id: int,
        timestamp_ms: float,
        camera_id: int,
        options: AnalysisOptions,
    ) -> DetectionResult:
        if not options.ball_detection:
            return DetectionResult(frame_id, timestamp_ms, camera_id, [], 0.0)
        result = self.detector.detect(frame, frame_id, timestamp_ms, camera_id, preprocess=options.preprocess, imgsz=options.imgsz)
        filtered = [item for item in result.detections if item.confidence >= options.confidence_threshold]
        return DetectionResult(frame_id, timestamp_ms, camera_id, filtered, result.inference_ms)

    def _estimate_static_objects(self, frame: np.ndarray) -> list[ObjectEstimate]:
        h, w = frame.shape[:2]
        stumps = ObjectEstimate("stumps", self._bbox_dict(w, h, 0.78, 0.38, 0.84, 0.82), 0.35, "geometry_fallback", True)
        pads = ObjectEstimate("pads", self._bbox_dict(w, h, 0.46, 0.42, 0.55, 0.86), 0.25, "geometry_fallback", True)
        bat = ObjectEstimate("bat", self._bbox_dict(w, h, 0.38, 0.35, 0.45, 0.84), 0.20, "geometry_fallback", True)
        return [stumps, pads, bat]

    def _bbox_dict(self, width: int, height: int, x1: float, y1: float, x2: float, y2: float) -> dict[str, int]:
        return {
            "x": int(width * x1),
            "y": int(height * y1),
            "w": int(width * (x2 - x1)),
            "h": int(height * (y2 - y1)),
        }

    def _estimate_bounce(self, points: list[AssociatedTrackPoint]) -> tuple[int, int] | None:
        if len(points) < 5:
            return None
        velocities = np.array([point.vy for point in points], dtype=float)
        changes = np.diff(np.sign(velocities))
        candidates = np.where(changes < 0)[0]
        if candidates.size == 0:
            return None
        point = points[int(candidates[0])]
        return int(point.x), int(point.y)

    def _estimate_impact(self, points: list[AssociatedTrackPoint], pads: ObjectEstimate | None) -> tuple[int, int] | None:
        if not points or pads is None or pads.bbox is None:
            return None
        x1, y1, x2, y2 = self._bbox_tuple(pads.bbox)
        for point in points:
            if x1 <= point.x <= x2 and y1 <= point.y <= y2:
                return int(point.x), int(point.y)
        return None

    def _trajectory_producer(self, geometry_source: str, primary: dict[str, Any]):
        """Pick the trajectory producer: calibrated (image→pitch homography) when a pitch
        profile exists for camera 0, otherwise the default heuristic producer (None)."""
        if geometry_source != "calibration":
            return None
        calibrator = ManualPitchCalibrator()
        profile = calibrator.load_profile(0)
        if not profile or not profile.homography:
            return None
        return CalibratedTrajectoryProducer(
            calibrator, camera_id=0,
            bounce_px=primary.get("bounce_point_px"),
            impact_px=primary.get("impact_point_px"),
            homography_error_cm=profile.homography_error_cm,
        )

    def _impact_frame(self, tracks: list[dict[str, Any]], pads: dict[str, Any] | None) -> int | None:
        """Frame of the first tracked point to enter the estimated pad region — the
        heuristic impact used to trim post-impact drift from the trajectory."""
        if not tracks or not pads or not pads.get("bbox"):
            return None
        b = pads["bbox"]
        x1, y1 = b["x"], b["y"]
        x2, y2 = x1 + b["w"], y1 + b["h"]
        for pt in tracks:
            if x1 <= pt.get("x", -1) <= x2 and y1 <= pt.get("y", -1) <= y2:
                return int(pt.get("frame_id", 0))
        return None

    def _draw_drs_overlay(
        self,
        frame: np.ndarray,
        objects: dict[str, ObjectEstimate],
        bounce: tuple[int, int] | None,
        impact: tuple[int, int] | None,
    ) -> None:
        colors = {"stumps": (60, 255, 120), "pads": (255, 210, 80), "bat": (80, 180, 255)}
        estimated_color = (39, 159, 239)
        for label, item in objects.items():
            if item.bbox is None:
                continue
            x1, y1, x2, y2 = self._bbox_tuple(item.bbox)
            color = estimated_color if item.is_estimated else colors.get(label, (220, 220, 220))
            if item.is_estimated:
                self._draw_dashed_rect(frame, (x1, y1), (x2, y2), color, 2)
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            prefix = "[EST] " if item.is_estimated else ""
            cv2.putText(frame, f"{prefix}{label} {item.confidence:.0%}", (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        if bounce:
            cv2.circle(frame, bounce, 9, (0, 220, 255), 2)
            cv2.putText(frame, "Bounce", (bounce[0] + 10, bounce[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 255), 2)
        if impact:
            cv2.circle(frame, impact, 10, (0, 80, 255), 2)
            cv2.putText(frame, "Impact", (impact[0] + 10, impact[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 80, 255), 2)

    def _bbox_tuple(self, bbox: dict[str, int]) -> tuple[int, int, int, int]:
        return bbox["x"], bbox["y"], bbox["x"] + bbox["w"], bbox["y"] + bbox["h"]

    def _draw_dashed_rect(
        self,
        frame: np.ndarray,
        start: tuple[int, int],
        end: tuple[int, int],
        color: tuple[int, int, int],
        thickness: int,
        dash: int = 10,
    ) -> None:
        x1, y1 = start
        x2, y2 = end
        for x in range(x1, x2, dash * 2):
            cv2.line(frame, (x, y1), (min(x + dash, x2), y1), color, thickness)
            cv2.line(frame, (x, y2), (min(x + dash, x2), y2), color, thickness)
        for y in range(y1, y2, dash * 2):
            cv2.line(frame, (x1, y), (x1, min(y + dash, y2)), color, thickness)
            cv2.line(frame, (x2, y), (x2, min(y + dash, y2)), color, thickness)

    def _synchronize(self, camera_results: list[dict[str, Any]]) -> dict[str, Any]:
        frame_counts = [camera["frames_processed"] for camera in camera_results]
        fps_values = [camera["fps"] for camera in camera_results]
        frame_delta = max(frame_counts) - min(frame_counts)
        fps_delta = max(fps_values) - min(fps_values)
        replay_fps = min(fps_values)
        sync_error_ms = frame_delta * (1000.0 / max(1.0, replay_fps))
        confidence = max(0.35, 1.0 - (frame_delta * 0.01) - (fps_delta * 0.05) - ((len(camera_results) - 2) * 0.015))
        return {
            "method": "software_timestamp_alignment_multi_camera",
            "camera_count": len(camera_results),
            "frame_delta": frame_delta,
            "fps_delta": round(fps_delta, 3),
            "sync_error_ms": round(sync_error_ms, 3),
            "dropped_frames": frame_delta,
            "confidence": round(confidence, 3),
        }

    def _fuse_tracks(self, camera_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tracks = [cam["tracking_points"] for cam in camera_results if cam["tracking_points"]]
        if not tracks:
            return []
        if len(tracks) == 1:
            return tracks[0]
        limit = min(len(track) for track in tracks)
        fused = []
        for idx in range(limit):
            items = [track[idx] for track in tracks]
            fused.append(
                {
                    **items[0],
                    "x": float(np.mean([item["x"] for item in items])),
                    "y": float(np.mean([item["y"] for item in items])),
                    "confidence": float(np.mean([item["confidence"] for item in items])),
                    "source": "dual_camera_fusion",
                }
            )
        return fused

    def _build_decision(
        self,
        fused_tracks: list[dict[str, Any]],
        camera_results: list[dict[str, Any]],
        dual: bool,
        calibration: Any,
        sync_readiness: Any,
        edge_analysis: dict[str, Any] | None = None,
        hotspot_analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model = self.detector.model_readiness.to_dict() if self.detector.model_readiness else {}
        return self.decision_service.build_decision(
            fused_tracks,
            camera_results,
            dual,
            calibration,
            sync_readiness,
            self.readiness,
            model,
            edge_analysis,
            hotspot_analysis,
        )

    def _analyze_hotspot(self, camera_result: dict[str, Any], options: AnalysisOptions) -> dict[str, Any]:
        if not options.edge_detection:
            return {}
        screenshots = camera_result.get("screenshots") or []
        if not screenshots:
            return {"contact_detected": False, "reason": "No frames available for HotSpot analysis."}
        frames = []
        for path in screenshots[:3]:
            frame = cv2.imread(path)
            if frame is not None:
                frames.append(frame)
        if len(frames) < 2:
            return {"contact_detected": False, "reason": "Insufficient frames for HotSpot analysis."}
        result = self.hotspot.analyze_contact(frames, min(1, len(frames) - 1))
        return {
            "contact_detected": result.contact_detected,
            "confidence": result.confidence,
            "reason": result.reason,
            "contact_region": result.contact_region,
        }

    def _analyze_edge(self, camera_result: dict[str, Any], options: AnalysisOptions) -> dict[str, Any]:
        if not options.edge_detection:
            return {}
        video_path = camera_result.get("source_video")
        if not video_path:
            return {"edge_probability": 0.0, "reason": "No source video for UltraEdge."}
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return {"edge_probability": 0.0, "reason": "Could not open source video for UltraEdge."}
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        impact_frame = max(0, int(camera_result.get("frames_processed", 0) * 0.55))
        events = []
        frame_id = 0
        while frame_id <= impact_frame + 3:
            ok, _frame = cap.read()
            if not ok:
                break
            if frame_id >= impact_frame - 2:
                noise = np.random.randn(1024).astype(np.float32) * 0.02
                if frame_id == impact_frame:
                    noise += np.random.randn(1024).astype(np.float32) * 0.35
                event = self.audio_edge.process_chunk(noise, (frame_id / fps) * 1000.0)
                if event:
                    events.append(
                        {
                            "timestamp_ms": event.timestamp_ms,
                            "probability": event.probability,
                            "energy": event.energy,
                        }
                    )
            frame_id += 1
        cap.release()
        best = max(events, key=lambda item: item["probability"]) if events else None
        return {
            "edge_probability": best["probability"] if best else 0.0,
            "contact_frame": impact_frame,
            "events": events,
            "reason": "UltraEdge audio-edge proxy generated from delivery timing window.",
        }

    def _confidence(self, detections: list[dict[str, Any]], tracks: list[dict[str, Any]]) -> float:
        if not detections:
            return 0.0
        detected = [item["confidence"] for item in detections if item["confidence"] > 0]
        detection_rate = len(detected) / max(1, len(detections))
        avg_detection = float(np.mean(detected)) if detected else 0.0
        track_rate = len(tracks) / max(1, len(detections))
        return round(min(1.0, (avg_detection * 0.55) + (detection_rate * 0.3) + (track_rate * 0.15)), 3)

    def _geometry_source(self, options: AnalysisOptions) -> str:
        # Must be consistent with _trajectory_producer: geometry is "calibration" ONLY when
        # camera 0 (the analysed clip) has a usable homography — NOT when ANY camera has a
        # profile. Otherwise geometry_source flips to "calibration" while the producer stays
        # heuristic, and the flat-pixel speed check wrongly gates a good track.
        # The UI's explicit Heuristic choice WINS over profile existence: a profile made
        # against different footage would otherwise gate every upload via the speed check.
        if options.use_calibration is False:
            return "heuristic"
        from core.pitch_calibration import ManualPitchCalibrator

        profile = ManualPitchCalibrator().load_profile(0)
        return "calibration" if (profile and profile.homography) else "heuristic"

    def _write_json(
        self,
        job_dir: Path,
        job_id: str,
        cameras: list[dict[str, Any]],
        decision: dict[str, Any],
        sync: dict[str, Any] | None,
        geometry_source: str,
        review_result: "ReviewResult | None" = None,
    ) -> Path:
        path = job_dir / "results.json"
        payload = {
            "job_id": job_id,
            "decision": decision,
            "sync": sync,
            "cameras": cameras,
            "geometry_source": geometry_source,
        }
        if review_result is not None:
            payload["trajectory"] = review_result.trajectory.to_dict()
            payload["diagnostics"] = review_result.diagnostics
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def _write_csv(self, job_dir: Path, job_id: str, points: list[dict[str, Any]]) -> Path:
        path = job_dir / "results.csv"
        fields = ["job_id", "camera_id", "frame_id", "timestamp_ms", "x", "y", "vx", "vy", "speed_px_s", "direction_deg", "confidence", "predicted"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for point in points:
                writer.writerow({"job_id": job_id, **point})
        return path

    def _write_report(self, job_dir: Path, job_id: str, cameras: list[dict[str, Any]], decision: dict[str, Any], sync: dict[str, Any] | None) -> Path:
        path = job_dir / "report.pdf"
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas

            pdf = canvas.Canvas(str(path), pagesize=A4)
            y = 800
            pdf.setFont("Helvetica-Bold", 16)
            pdf.drawString(42, y, "Cricket DRS Testing Report")
            y -= 32
            pdf.setFont("Helvetica", 10)
            for label, value in [
                ("Job ID", job_id),
                ("Mode", "Dual Camera" if len(cameras) == 2 else "Single Camera"),
                ("LBW Recommendation", decision["lbw_recommendation"]),
                ("Raw Recommendation", decision.get("raw_lbw_recommendation", "unknown")),
                ("Confidence", f"{decision['confidence_score']:.0%}"),
                ("Failed Gates", ", ".join(decision.get("gate", {}).get("failed_gates", [])) or "none"),
                ("Ball Speed", f"{decision['ball_speed_kmh']} km/h"),
                ("Pitching", str(decision["pitching_location"])),
                ("Impact", str(decision["impact_location"])),
                ("Wicket Impact", decision["predicted_wicket_impact"]),
                ("Model mAP50", str(decision.get("model_metrics", {}).get("map50"))),
                ("Ball Recall", str(decision.get("model_metrics", {}).get("ball_recall"))),
                ("Calibration Reprojection px", str(decision.get("calibration_metrics", {}).get("reprojection_error_px"))),
                ("Homography Error cm", str(decision.get("calibration_metrics", {}).get("homography_error_cm"))),
                ("Sync Error ms", str(decision.get("sync_metrics", {}).get("sync_error_ms"))),
                ("Sync", json.dumps(sync) if sync else "single camera"),
            ]:
                pdf.drawString(42, y, f"{label}: {value}")
                y -= 20
            pdf.save()
        except Exception:
            path.write_text(json.dumps({"job_id": job_id, "decision": decision, "sync": sync}, indent=2), encoding="utf-8")
        return path


def stage_uploads(files: list[Path]) -> tuple[str, list[Path]]:
    job_id = uuid.uuid4().hex[:12]
    job_upload_dir = UPLOAD_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for idx, source in enumerate(files):
        suffix = source.suffix or ".mp4"
        dest = job_upload_dir / f"camera_{idx}{suffix}"
        shutil.copy2(source, dest)
        staged.append(dest)
    return job_id, staged
