"""ReplayBuilder — encodes an overlay onto a frame sequence.

Knows nothing about cricket: it delegates every drawing decision to an injected
:class:`~core.overlay_renderer.OverlayRenderer`, so the saved replay and the live
dashboard render through the SAME renderer and stay pixel-identical.

    frames + OverlayPayload ─► ReplayBuilder(OverlayRenderer) ─► replay.mp4
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from config.settings import REPLAY_CLIP_MAX_FRAMES
from core.animation_director import AnimationDirector
from core.camera_director import CameraDirector
from core.overlay_renderer import OverlayRenderer
from core.timelines import timeline_for
from utils.logger import get_logger

log = get_logger("replay_builder")


class ReplayBuilder:
    def __init__(self, renderer: OverlayRenderer | None = None, director: AnimationDirector | None = None,
                 camera: CameraDirector | None = None, fps: int = 30, max_frames: int | None = None):
        self.renderer = renderer or OverlayRenderer()
        self.director = director or AnimationDirector()
        self.camera = camera or CameraDirector()
        self.fps = int(fps)
        # The saved review clip keeps the whole delivery, not just the tail: the old
        # hardcoded 150-frame cap cut real reviews to the "last 10 seconds".
        self.max_frames = int(max_frames if max_frames is not None else REPLAY_CLIP_MAX_FRAMES)

    def build(self, frames: list, payload: dict, output_path: Path) -> dict:
        """Render ``payload`` onto each frame (revealed progressively) and encode mp4."""
        images = self._extract_images(frames)[-self.max_frames:]
        meta = {
            "available": False, "path": None, "frame_count": 0,
            "duration_s": 0.0, "fps": self.fps, "reason": "",
        }
        if not images:
            meta["reason"] = "No frames available for replay."
            return meta
        height, width = images[0].shape[:2]
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (width, height))
        if not writer.isOpened():
            meta["reason"] = "Could not open the video writer."
            return meta
        try:
            # Resolve the review's declarative timeline once; both directors consume it.
            timeline = timeline_for((payload or {}).get("review_type"))
            total = len(images)
            for index, image in enumerate(images):
                progress = (index + 1) / total
                # AnimationDirector reveals the overlay; CameraDirector runs the camera
                # (freeze holds the first frame, then the zoom ramps in).
                state = self.director.state_for_progress(timeline, payload or {}, progress)
                camera = self.camera.state_for_progress(timeline, progress)
                source = images[0] if camera.get("freeze") else image
                rendered = self.renderer.render(source, payload or {}, state)
                writer.write(self._apply_zoom(rendered, camera.get("zoom", 0.0)))
        finally:
            writer.release()
        meta.update(
            available=True, path=str(output_path), frame_count=len(images),
            duration_s=round(len(images) / max(1, self.fps), 2), reason="ok",
        )
        return meta

    @staticmethod
    def _apply_zoom(image, zoom: float):
        """Cinematic slow-zoom (crop-centre + resize) — a generic frame transform,
        driven by the director's state, applied equally to video + overlay."""
        if not zoom or zoom <= 0.001:
            return image
        height, width = image.shape[:2]
        crop_w, crop_h = int(width * (1 - zoom)), int(height * (1 - zoom))
        x0, y0 = (width - crop_w) // 2, (height - crop_h) // 2
        crop = image[y0:y0 + crop_h, x0:x0 + crop_w]
        return cv2.resize(crop, (width, height), interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def _extract_images(frames) -> list:
        images = []
        for item in frames or []:
            image = getattr(item, "frame", None)
            if image is None and isinstance(item, np.ndarray):
                image = item
            if image is not None:
                images.append(image)
        return images
