"""End-to-end production dataset pipeline orchestrator.

    raw video -> frame extraction -> automatic annotation -> [human review]
              -> approved dataset -> train (scripts/train_yolo_drs.py)

By default the pipeline stops after annotation so a human can correct the
flagged frames. Move approved image/label pairs into
training/staging/approved/{images,labels} and re-run with --build, or pass
--approve-all to promote every annotated frame without review (smoke tests only).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.dataset_config import active_dataset_dir  # noqa: E402
from scripts.extract_frames import extract_frames  # noqa: E402
from scripts.auto_annotate import auto_annotate, resolve_model  # noqa: E402
from scripts.build_dataset import build_dataset  # noqa: E402

STAGING = PROJECT_ROOT / "training" / "staging"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full DRS dataset pipeline")
    parser.add_argument("--source", default=None, help="Raw video folder (default: active dataset videos/raw)")
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--min-sharpness", type=float, default=40.0)
    parser.add_argument("--model", default=None, help="Detector for pre-annotation (default: best available)")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--build", action="store_true", help="Skip extract/annotate; build splits from approved/")
    parser.add_argument("--approve-all", action="store_true", help="Promote all annotated frames without review (smoke only)")
    args = parser.parse_args()

    frames = STAGING / "frames"
    labels = STAGING / "labels"
    review = STAGING / "review"
    approved_images = STAGING / "approved" / "images"
    approved_labels = STAGING / "approved" / "labels"
    dataset = active_dataset_dir()

    if not args.build:
        source = Path(args.source) if args.source else (dataset / "videos" / "raw")
        print("== Stage 1: frame extraction ==")
        extract_frames(source, frames, args.stride, args.min_sharpness)
        print("\n== Stage 2: automatic annotation ==")
        auto_annotate(frames, labels, review, resolve_model(args.model), args.conf)

        if args.approve_all:
            approved_images.mkdir(parents=True, exist_ok=True)
            approved_labels.mkdir(parents=True, exist_ok=True)
            for image_path in frames.glob("*"):
                if image_path.suffix.lower() in IMAGE_SUFFIXES:
                    shutil.copy2(image_path, approved_images / image_path.name)
            for label_path in labels.glob("*.txt"):
                shutil.copy2(label_path, approved_labels / label_path.name)
            print("Auto-approved all annotated frames (smoke mode).")
        else:
            print("\n== Stage 3: human review required ==")
            print(f"Review the flagged frames in: {review}")
            print("Move approved image/label pairs into:")
            print(f"  {approved_images}")
            print(f"  {approved_labels}")
            print("Then re-run this command with --build to create the dataset splits.")
            return

    print("\n== Stage 4: build dataset splits ==")
    build_dataset(approved_images, approved_labels, dataset)
    print("\n== Next: validate + train with scripts/train_yolo_drs.py ==")


if __name__ == "__main__":
    main()
