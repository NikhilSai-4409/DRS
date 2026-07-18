"""Per-review logging: one folder per appeal with everything needed to replay or
debug it.

    Review_001/
      review.json        full decision + ReviewResult + camera_sync + calibration
      trajectory.json    trajectory points + predicted extension
      ultraedge.json     edge analysis (Edge reviews only)
      replay.mp4         broadcast replay (when frames are available)
      frames/            first / mid / last key frames
      logs.txt           human-readable summary

Saving is best-effort: a logging failure must never break a live review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import cv2

from config.settings import REVIEWS_DIR
from core.replay_builder import ReplayBuilder
from utils.helpers import save_json
from utils.logger import get_logger

log = get_logger("review_logger")


class ReviewLogger:
    def __init__(self, root: Path = REVIEWS_DIR, replay_builder: Optional[ReplayBuilder] = None):
        self.root = Path(root)
        self.replay_builder = replay_builder or ReplayBuilder()

    def _next_dir(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        numbers = []
        for path in self.root.glob("Review_*"):
            suffix = path.name.split("_", 1)[-1]
            if path.is_dir() and suffix.isdigit():
                numbers.append(int(suffix))
        review_dir = self.root / f"Review_{(max(numbers) + 1) if numbers else 1:03d}"
        review_dir.mkdir(parents=True, exist_ok=True)
        return review_dir

    def log(
        self,
        decision: dict,
        frames: Optional[list] = None,
        calibration: Any = None,
        frame_timestamps: Optional[dict] = None,
        save_frames: bool = True,
        save_replay: bool = True,
    ) -> dict:
        try:
            return self._log(decision, frames, calibration, frame_timestamps, save_frames, save_replay)
        except Exception as exc:  # logging must never break a review
            log.warning("ReviewLogger failed: {}", exc)
            return {"saved": False, "reason": str(exc)}

    def _log(self, decision, frames, calibration, frame_timestamps, save_frames, save_replay) -> dict:
        review_dir = self._next_dir()
        review_result = decision.get("review_result") or {}
        artifacts: list[str] = []

        record = {
            "review_id": review_dir.name,
            "review_type": decision.get("review_type"),
            "status": decision.get("status"),
            "review_result": review_result,
            "camera_sync": decision.get("camera_sync"),
            "confidence": review_result.get("confidence", decision.get("overall_confidence")),
            "calibration": calibration,
            "frame_timestamps": {str(k): v for k, v in (frame_timestamps or {}).items()},
            "decision": decision,
        }
        save_json(record, review_dir / "review.json")
        artifacts.append("review.json")

        save_json(
            {"trajectory": decision.get("trajectory", []),
             "predicted_extension": decision.get("predicted_extension", [])},
            review_dir / "trajectory.json",
        )
        artifacts.append("trajectory.json")

        if decision.get("edge_analysis") is not None:
            save_json(decision["edge_analysis"], review_dir / "ultraedge.json")
            artifacts.append("ultraedge.json")

        if save_frames and frames:
            images = ReplayBuilder._extract_images(frames)
            if images:
                frames_dir = review_dir / "frames"
                frames_dir.mkdir(exist_ok=True)
                for label, index in (("first", 0), ("mid", len(images) // 2), ("last", len(images) - 1)):
                    if 0 <= index < len(images):
                        cv2.imwrite(str(frames_dir / f"{label}.jpg"), images[index])
                artifacts.append("frames/")

        replay_meta = {"available": False, "reason": "frames not provided"}
        if save_replay and frames:
            # Render the projected overlay payload (falls back to a banner-only
            # payload from the ReviewResult when no geometry was projected).
            payload = decision.get("overlay") or {
                "review_type": decision.get("review_type"),
                "verdict": review_result.get("verdict"),
                "confidence": review_result.get("confidence"),
                "measurements": review_result.get("measurements") or [],
            }
            replay_meta = self.replay_builder.build(frames, payload, review_dir / "replay.mp4")
            if replay_meta.get("available"):
                artifacts.append("replay.mp4")

        self._write_log_txt(review_dir, decision, review_result, replay_meta, artifacts)
        artifacts.append("logs.txt")
        return {
            "saved": True,
            "review_id": review_dir.name,
            "dir": str(review_dir),
            "artifacts": artifacts,
            "replay": replay_meta,
        }

    @staticmethod
    def _write_log_txt(review_dir: Path, decision: dict, review_result: dict, replay_meta: dict, artifacts: list) -> None:
        sync = decision.get("camera_sync") or {}
        lines = [
            f"Review:      {review_dir.name}",
            f"Type:        {decision.get('review_type')}",
            f"Verdict:     {review_result.get('verdict')}",
            f"Confidence:  {review_result.get('confidence')}",
            f"Status:      {decision.get('status')}",
            f"Camera sync: in_sync={sync.get('in_sync')} max_offset_ms={sync.get('max_offset_ms')}",
            f"Replay:      {'saved' if replay_meta.get('available') else replay_meta.get('reason', 'not generated')}",
            f"Artifacts:   {', '.join(artifacts)}",
            "",
            "Warnings:",
            *[f"  - {warning}" for warning in (review_result.get("warnings") or [])],
        ]
        (review_dir / "logs.txt").write_text("\n".join(lines), encoding="utf-8")
