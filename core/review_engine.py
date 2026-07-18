"""Shared review-execution engine.

One engine, two input sources. Both the live camera path (replay buffer) and the
offline path (recorded delivery video) build a :class:`ReviewContext` and call
:meth:`ReviewEngine.execute`, so every review type runs *identical* code regardless
of where the frames came from — no live/offline logic drift.

    Live replay buffer ─┐
                        ├─► ReviewContext ─► ReviewEngine.execute(type) ─► *_analysis
    Recorded video ─────┘

The live path (``core/api_server.py``) builds the context from a synchronized
snapshot; the offline path (testing platform / CLI) builds it from a video file via
:func:`frames_from_video`. Only the input source changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.review_modules import ReviewContext, run_review


class ReviewEngine:
    """The single review-execution entry point for every input source."""

    @staticmethod
    def build_context(
        review_type: str,
        *,
        frames: dict[int, list],
        detector: Any,
        calibrators: dict[int, Any] | None = None,
        camera_roles: dict[int, str] | None = None,
        primary_camera_id: int | None = None,
        timestamps: dict[int, list] | None = None,
        telemetry: dict[int, Any] | None = None,
        reference_timestamp_ms: float | None = None,
    ) -> ReviewContext:
        """Assemble the deterministic ReviewContext shared by every module."""
        return ReviewContext(
            review_type=str(review_type or "lbw").lower(),
            frames=frames,
            detector=detector,
            calibrators=calibrators or {},
            camera_roles=camera_roles or {},
            primary_camera_id=primary_camera_id,
            timestamps=timestamps or {},
            telemetry=telemetry or {},
            reference_timestamp_ms=reference_timestamp_ms,
        )

    @staticmethod
    def execute(review_type: str, ctx: ReviewContext) -> dict | None:
        """Run the module for ``review_type`` on ``ctx``. Returns its analysis dict."""
        return run_review(review_type, ctx)

    @staticmethod
    def run(review_type: str, **context_kwargs: Any) -> dict | None:
        """Convenience: build the context and execute in one call."""
        ctx = ReviewEngine.build_context(review_type, **context_kwargs)
        return ReviewEngine.execute(review_type, ctx)


# --------------------------------------------------------------------------- #
# Detector reuse: load the YOLO model ONCE and share it across clips/reviews so
# batch-validating many recorded deliveries doesn't reload the model per clip.
# (Single-threaded reuse; a parallel worker pool would give each worker its own.)
# --------------------------------------------------------------------------- #
_SHARED_DETECTOR: Any = None


def get_shared_detector() -> Any:
    """Return a process-wide BallDetector, loading the model on first use only."""
    global _SHARED_DETECTOR
    if _SHARED_DETECTOR is None:
        from core.ball_detector import BallDetector

        _SHARED_DETECTOR = BallDetector()
    return _SHARED_DETECTOR


# --------------------------------------------------------------------------- #
# Offline source adapter: recorded video -> VideoFrames -> shared engine
# --------------------------------------------------------------------------- #
def frames_from_video(
    video_path: str | Path,
    camera_id: int = 0,
    max_frames: int | None = None,
    stride: int = 1,
    safety_cap: int = 900,
) -> list:
    """Read a recorded delivery clip into ``VideoFrame`` objects.

    Timestamps are derived from the clip's own FPS, so 120/240 FPS recordings keep
    their true inter-frame timing — the same shape the live replay buffer produces.
    """
    import cv2

    from core.camera_manager import VideoFrame

    frames: list = []
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 240.0
    idx = 0
    kept = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % max(1, stride) == 0:
                ts = (idx / fps) * 1000.0
                frames.append(VideoFrame(camera_id=camera_id, frame_id=kept, timestamp_ms=ts, frame=frame))
                kept += 1
                if max_frames and kept >= max_frames:
                    break
                if kept >= safety_cap:
                    break
            idx += 1
    finally:
        cap.release()
    return frames


def calibrators_for(camera_ids: list[int], factory: Any = None) -> dict[int, Any]:
    """Load each camera's saved calibration profile — same source the live path uses."""
    from core.pitch_calibration import ManualPitchCalibrator

    factory = factory or ManualPitchCalibrator
    loaded: dict[int, Any] = {}
    for camera_id in camera_ids:
        calibrator = factory()
        try:
            if calibrator.load_profile(camera_id):
                loaded[camera_id] = calibrator
        except Exception:
            pass
    return loaded


def run_review_on_video(
    video_path: str | Path,
    review_type: str,
    camera_id: int = 0,
    detector: Any = None,
    calibrator: Any = None,
    camera_roles: dict[int, str] | None = None,
    max_frames: int | None = None,
) -> dict | None:
    """Run one review type on a recorded clip through the shared engine.

    This is the offline twin of the live ``request_review`` — identical module code,
    just fed from a file. Returns the module's ``*_analysis`` block (or ``None`` if no
    frames / unknown type). Without a calibration profile the module honestly reports
    ``requires_calibration`` — the same as live.
    """
    frames = frames_from_video(video_path, camera_id=camera_id, max_frames=max_frames)
    if not frames:
        return None
    calibrators = {camera_id: calibrator} if calibrator is not None else calibrators_for([camera_id])
    return ReviewEngine.run(
        review_type,
        frames={camera_id: frames},
        detector=detector or get_shared_detector(),   # reuse the loaded model
        calibrators=calibrators,
        camera_roles=camera_roles or {},
        primary_camera_id=camera_id,
        timestamps={camera_id: [f.timestamp_ms for f in frames]},
        reference_timestamp_ms=frames[0].timestamp_ms if frames else None,
    )
