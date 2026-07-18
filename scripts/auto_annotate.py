"""Dataset pipeline stage 2: pre-annotate extracted frames with the detector.

Runs the current production detector over staged frames and writes candidate
YOLO labels. Frames with no detections or only low-confidence detections are
copied into a review queue for a human to correct. This is genuine model-
assisted pre-labelling, not synthetic ground truth.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.dataset_config import class_names  # noqa: E402

STAGING = PROJECT_ROOT / "training" / "staging"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def _auto_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch

        return "0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def resolve_model(model: str | Path | None) -> Path:
    """Return the requested detector, or the best one model_selector finds."""
    if model:
        return Path(model)
    from core.model_selector import DetectorModelSelector

    path, _ = DetectorModelSelector().select(None)
    return path


def auto_annotate(
    frames_dir: Path | str,
    labels_dir: Path | str,
    review_dir: Path | str,
    model_path: Path | str,
    conf: float = 0.25,
    uncertain_conf: float = 0.5,
    imgsz: int = 1280,
    device: str = "auto",
) -> dict[str, int]:
    from ultralytics import YOLO

    frames_dir = Path(frames_dir)
    labels_dir = Path(labels_dir)
    review_dir = Path(review_dir)
    labels_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(p for p in frames_dir.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        raise SystemExit(f"No frames to annotate in: {frames_dir}")

    class_count = len(class_names())
    device = _auto_device(device)
    model = YOLO(str(model_path))
    print(f"Annotating {len(images)} frames with {Path(model_path).name} on device {device}")

    annotated = labelled = flagged = 0
    for image_path in images:
        result = model.predict(str(image_path), conf=conf, imgsz=imgsz, device=device, verbose=False)[0]
        lines: list[str] = []
        best_conf = 0.0
        for box in (result.boxes or []):
            class_id = int(box.cls[0])
            confidence = float(box.conf[0])
            # Only keep detections that map onto our class schema.
            if class_id >= class_count:
                continue
            x_center, y_center, width, height = (float(v) for v in box.xywhn[0])
            lines.append(f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")
            best_conf = max(best_conf, confidence)

        (labels_dir / f"{image_path.stem}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
        annotated += 1
        if lines:
            labelled += 1
        if not lines or best_conf < uncertain_conf:
            shutil.copy2(image_path, review_dir / image_path.name)
            flagged += 1

    print(f"Annotated {annotated} frames | with labels: {labelled} | flagged for review: {flagged}")
    print(f"Candidate labels: {labels_dir}")
    print(f"Review queue (empty / low confidence): {review_dir}")
    return {"annotated": annotated, "labelled": labelled, "flagged": flagged}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-annotate frames with the current detector (dataset pipeline stage 2)")
    parser.add_argument("--frames-dir", default=str(STAGING / "frames"))
    parser.add_argument("--labels-dir", default=str(STAGING / "labels"))
    parser.add_argument("--review-dir", default=str(STAGING / "review"))
    parser.add_argument("--model", default=None, help="Detector weights (default: model_selector best)")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--uncertain-conf", type=float, default=0.5, help="Below this max confidence a frame goes to review")
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    auto_annotate(
        args.frames_dir,
        args.labels_dir,
        args.review_dir,
        resolve_model(args.model),
        args.conf,
        args.uncertain_conf,
        args.imgsz,
        args.device,
    )


if __name__ == "__main__":
    main()
