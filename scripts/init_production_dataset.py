"""Create and verify the versioned production Cricket DRS dataset structure.

Builds a long-term, multi-version dataset layout under training/datasets/ and
regenerates training/data.yaml from config/dataset.yaml. Idempotent: safe to
run repeatedly; only missing directories are created and no data is touched.

Per-dataset internal layout:

    images/{train,val,test}                 detector frames
    labels/{train,val,test}                 YOLO txt labels (one per image)
    videos/raw/match01..03                  original high-bitrate 120 FPS footage
    videos/validation                       held-out validation footage
    videos/tournament                       live tournament captures
    calibration/charuco                     ChArUco board captures
    calibration/intrinsics                  per-camera intrinsic matrices
    calibration/extrinsics                  per-camera extrinsic / pitch poses
    calibration/ground_profiles             per-ground pitch profiles
    audio/{raw,processed,synchronized}      UltraEdge audio capture pipeline
    exports/                                packaged datasets / model bundles
    metadata/                               provenance, fps, bowler/batter tags
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.dataset_config import (  # noqa: E402
    DATASETS_ROOT,
    active_dataset_name,
    dataset_versions,
    write_data_yaml,
)

SPLITS = ("train", "val", "test")

# Internal layout created inside every dataset version.
INTERNAL_LAYOUT = (
    *(f"images/{split}" for split in SPLITS),
    *(f"labels/{split}" for split in SPLITS),
    "videos/raw/match01",
    "videos/raw/match02",
    "videos/raw/match03",
    "videos/validation",
    "videos/tournament",
    "calibration/charuco",
    "calibration/intrinsics",
    "calibration/extrinsics",
    "calibration/ground_profiles",
    "audio/raw",
    "audio/processed",
    "audio/synchronized",
    "exports",
    "metadata",
)


def ensure_dataset(dataset_dir: Path) -> list[Path]:
    """Create the internal layout for one dataset version. Returns new dirs."""
    created: list[Path] = []
    for relative in INTERNAL_LAYOUT:
        directory = dataset_dir / relative
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(directory)
        keep = directory / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the versioned production DRS dataset structure")
    parser.add_argument("--datasets-root", default=str(DATASETS_ROOT), help="Root for dataset versions")
    args = parser.parse_args()
    root = Path(args.datasets_root)

    total_created = 0
    for version in dataset_versions():
        created = ensure_dataset(root / version)
        total_created += len(created)
        marker = "  (active)" if version == active_dataset_name() else ""
        print(f"dataset '{version}': {len(created)} new directories{marker}")

    data_yaml = write_data_yaml()
    print(f"Datasets root: {root}")
    print(f"Active dataset: {active_dataset_name()}")
    print(f"Total new directories: {total_created}")
    print(f"Generated data config: {data_yaml}")
    print("Dataset structure ready.")


if __name__ == "__main__":
    main()
