"""Report dataset inventory and class statistics from configured paths."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from development.config import config_path, dataset_classes

IMAGE_SUFFIXES = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mts", ".m2ts"}


def _count(path: Path, suffixes: set[str]) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file() and item.suffix.lower() in suffixes)


def _label_distribution(path: Path) -> Counter[int]:
    counts: Counter[int] = Counter()
    if not path.exists():
        return counts
    for label in path.rglob("*.txt"):
        for line in label.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if parts:
                try:
                    counts[int(parts[0])] += 1
                except ValueError:
                    continue
    return counts


def build_statistics() -> dict[str, object]:
    classes = dataset_classes()
    raw_videos = config_path("dataset", "raw_videos")
    frame_output = config_path("dataset", "frame_output")
    versions_root = config_path("dataset", "versions")
    distribution = _label_distribution(versions_root)
    return {
        "raw_videos": _count(raw_videos, VIDEO_SUFFIXES),
        "extracted_frames": _count(frame_output, IMAGE_SUFFIXES),
        "dataset_versions": sorted(path.name for path in versions_root.iterdir() if path.is_dir()) if versions_root.exists() else [],
        "class_distribution": {
            str(class_id): {"name": classes[class_id], "instances": distribution[class_id]}
            for class_id in sorted(classes)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Print configured dataset statistics")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    stats = build_statistics()
    if args.json:
        print(json.dumps(stats, indent=2))
        return
    print("Dataset statistics")
    print(f"Raw videos: {stats['raw_videos']}")
    print(f"Extracted frames: {stats['extracted_frames']}")
    print("Versions: " + (", ".join(stats["dataset_versions"]) if stats["dataset_versions"] else "none"))
    print("Class distribution:")
    for class_id, info in stats["class_distribution"].items():
        print(f"  {class_id} {info['name']}: {info['instances']}")


if __name__ == "__main__":
    main()
