"""Dataset pipeline stage 3: split approved frames into the active dataset.

Takes human-approved image/label pairs and distributes them deterministically
into the active dataset's train/val/test splits. The split is keyed by a stable
hash of the file stem so re-running never reshuffles existing data, and old
datasets are never overwritten (a different active version is a different dir).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.dataset_config import active_dataset_dir  # noqa: E402

STAGING = PROJECT_ROOT / "training" / "staging"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def _split_for(stem: str, seed: int, train: float, val: float) -> str:
    bucket = int(hashlib.sha256(f"{seed}:{stem}".encode()).hexdigest(), 16) % 10000 / 10000.0
    if bucket < train:
        return "train"
    if bucket < train + val:
        return "val"
    return "test"


def build_dataset(
    images_dir: Path | str,
    labels_dir: Path | str,
    dataset_dir: Path | str,
    train: float = 0.8,
    val: float = 0.1,
    seed: int = 42,
    move: bool = False,
) -> dict[str, int]:
    images_dir = Path(images_dir)
    labels_dir = Path(labels_dir)
    dataset_dir = Path(dataset_dir)
    images = sorted(p for p in images_dir.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        raise SystemExit(f"No approved images in: {images_dir}")

    counts = {"train": 0, "val": 0, "test": 0, "missing_label": 0}
    transfer = shutil.move if move else shutil.copy2
    for image_path in images:
        label_path = labels_dir / f"{image_path.stem}.txt"
        if not label_path.exists():
            counts["missing_label"] += 1
            continue
        split = _split_for(image_path.stem, seed, train, val)
        (dataset_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        transfer(str(image_path), str(dataset_dir / "images" / split / image_path.name))
        transfer(str(label_path), str(dataset_dir / "labels" / split / label_path.name))
        counts[split] += 1

    print(f"train={counts['train']} val={counts['val']} test={counts['test']} "
          f"skipped (no label)={counts['missing_label']}")
    print(f"Dataset: {dataset_dir}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Split approved data into the active dataset (dataset pipeline stage 3)")
    parser.add_argument("--images", default=str(STAGING / "approved" / "images"))
    parser.add_argument("--labels", default=str(STAGING / "approved" / "labels"))
    parser.add_argument("--dataset", default=None, help="Target dataset dir (default: active dataset)")
    parser.add_argument("--train", type=float, default=0.8)
    parser.add_argument("--val", type=float, default=0.1, help="Test ratio is the remainder")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--move", action="store_true", help="Move instead of copy")
    args = parser.parse_args()

    if not 0.0 < args.train < 1.0 or not 0.0 <= args.val < 1.0 or args.train + args.val >= 1.0:
        raise SystemExit("Invalid ratios: require 0<train<1, 0<=val<1, train+val<1 (remainder is test)")
    dataset = Path(args.dataset) if args.dataset else active_dataset_dir()
    build_dataset(args.images, args.labels, dataset, args.train, args.val, args.seed, args.move)


if __name__ == "__main__":
    main()
