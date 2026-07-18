"""Validate the production YOLO dataset before training."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from PIL import Image


IMAGE_SUFFIXES = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Dataset YAML must contain a mapping: {path}")
    return data


def _resolve_dataset_path(config_path: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    base = config_path.parent
    return (base / path).resolve()


def _iter_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)


def _image_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_image(path: Path) -> str | None:
    try:
        with Image.open(path) as image:
            image.verify()
        return None
    except Exception as exc:
        return f"{path}: {exc}"


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None


def _parse_label(path: Path, class_count: int) -> tuple[Counter[int], list[str], list[tuple[int, float, float]]]:
    distribution: Counter[int] = Counter()
    errors: list[str] = []
    boxes: list[tuple[int, float, float]] = []
    if not path.exists():
        return distribution, [f"{path}: missing label file"], boxes

    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            errors.append(f"{path}:{line_number}: expected 5 YOLO fields, got {len(parts)}")
            continue
        try:
            class_id = int(parts[0])
            coords = [float(value) for value in parts[1:]]
        except ValueError:
            errors.append(f"{path}:{line_number}: non-numeric YOLO field")
            continue
        if class_id < 0 or class_id >= class_count:
            errors.append(f"{path}:{line_number}: class {class_id} outside 0..{class_count - 1}")
            continue
        if any(value < 0.0 or value > 1.0 for value in coords):
            errors.append(f"{path}:{line_number}: coordinates must be normalized to 0..1")
            continue
        if coords[2] <= 0.0 or coords[3] <= 0.0:
            errors.append(f"{path}:{line_number}: width and height must be positive")
            continue
        distribution[class_id] += 1
        boxes.append((class_id, coords[2], coords[3]))
    return distribution, errors, boxes


def _star_score(value: float) -> str:
    stars = max(0, min(5, int(round(value))))
    return ("*" * stars).ljust(5, "-")


def validate_dataset(config_path: Path, report_path: Path | None = None) -> int:
    config_path = config_path.resolve()
    data = _load_yaml(config_path)
    dataset_root = _resolve_dataset_path(config_path, data.get("path", config_path.parent))
    names = data.get("names", {})
    if isinstance(names, list):
        class_names = {idx: name for idx, name in enumerate(names)}
    elif isinstance(names, dict):
        class_names = {int(idx): str(name) for idx, name in names.items()}
    else:
        raise ValueError("Dataset YAML names must be a list or mapping")

    class_count = int(data.get("nc", len(class_names)))
    splits = {"train": data.get("train"), "val": data.get("val"), "test": data.get("test")}
    image_count = 0
    label_count = 0
    empty_labels: list[Path] = []
    missing_labels: list[Path] = []
    corrupted_images: list[str] = []
    label_errors: list[str] = []
    distribution: Counter[int] = Counter()
    split_image_counts: dict[str, int] = {}
    hashes: dict[str, list[Path]] = defaultdict(list)
    resolutions: Counter[tuple[int, int]] = Counter()
    boxes_by_class: dict[int, list[tuple[float, float]]] = defaultdict(list)
    label_hashes: dict[str, list[Path]] = defaultdict(list)
    orphan_labels: list[Path] = []

    for split, relative_image_dir in splits.items():
        if not relative_image_dir:
            # train and val are mandatory; test is recommended but not fatal.
            if split in ("train", "val"):
                label_errors.append(f"{split}: image directory not configured")
            else:
                split_image_counts[split] = 0
            continue
        image_dir = dataset_root / str(relative_image_dir)
        label_dir = dataset_root / str(relative_image_dir).replace("images", "labels", 1)
        images = _iter_images(image_dir)
        image_count += len(images)
        split_image_counts[split] = len(images)

        image_stems: set[str] = set()
        for image_path in images:
            image_stems.add(image_path.stem)
            image_error = _validate_image(image_path)
            if image_error:
                corrupted_images.append(image_error)
            size = _image_size(image_path)
            if size is not None:
                resolutions[size] += 1
            hashes[_image_hash(image_path)].append(image_path)

            label_path = label_dir / image_path.relative_to(image_dir).with_suffix(".txt")
            if not label_path.exists():
                missing_labels.append(label_path)
                continue
            label_count += 1
            if label_path.stat().st_size == 0:
                empty_labels.append(label_path)
            labels, errors, label_boxes = _parse_label(label_path, class_count)
            distribution.update(labels)
            label_errors.extend(errors)
            for class_id, box_w, box_h in label_boxes:
                boxes_by_class[class_id].append((box_w, box_h))
            content = " ".join(sorted(label_path.read_text(encoding="utf-8").split()))
            if content:
                label_hashes[content].append(label_path)

        # Orphan labels: label files in this split with no matching image.
        if label_dir.exists():
            for label_file in label_dir.rglob("*.txt"):
                if label_file.stem not in image_stems:
                    orphan_labels.append(label_file)

    duplicate_images = [paths for paths in hashes.values() if len(paths) > 1]
    duplicate_labels = [paths for paths in label_hashes.values() if len(paths) > 1]
    total_boxes = sum(len(boxes) for boxes in boxes_by_class.values())

    # Split presence: train and val must be non-empty; test is recommended.
    missing_splits = [
        split for split in ("train", "val") if split_image_counts.get(split, 0) == 0
    ]

    # Class balance: every declared class should be represented; flag a class
    # whose instance count is a tiny fraction of the most common class.
    populated = {cid: distribution[cid] for cid in range(class_count) if distribution[cid] > 0}
    absent_classes = [cid for cid in range(class_count) if distribution[cid] == 0]
    balance_ratio = (max(populated.values()) / min(populated.values())) if len(populated) > 1 else 1.0

    print("YOLO dataset validation")
    print(f"Config: {config_path}")
    print(f"Dataset root: {dataset_root}")
    print(f"Number of images: {image_count}")
    print(f"Number of labels: {label_count}")
    print("Split image counts:")
    for split in ("train", "val", "test"):
        print(f"  {split}: {split_image_counts.get(split, 0)}")
    print("Class distribution:")
    for class_id in range(class_count):
        print(f"  {class_id} {class_names.get(class_id, '<unnamed>')}: {distribution[class_id]}")
    print(f"Class balance ratio (max/min populated): {balance_ratio:.1f}")
    if absent_classes:
        print("Classes with zero instances: " + ", ".join(
            f"{cid} {class_names.get(cid, '<unnamed>')}" for cid in absent_classes
        ))
    if balance_ratio > 50.0:
        print("WARNING: severe class imbalance (>50x). Collect more of the rare classes.")
    if split_image_counts.get("test", 0) == 0:
        print("WARNING: no test split images. A held-out test set is required for honest evaluation.")
    if missing_splits:
        print("Missing required splits: " + ", ".join(missing_splits))

    # Image resolution consistency.
    print(f"Distinct image resolutions: {len(resolutions)}")
    for (width, height), count in resolutions.most_common(5):
        print(f"  {width}x{height}: {count}")
    if len(resolutions) > 1:
        print("WARNING: mixed image resolutions present (training will letterbox to imgsz).")

    # Bounding-box statistics (normalized width/height/area/aspect ratio).
    print(f"Total bounding boxes: {total_boxes}")
    if total_boxes:
        all_w = [w for boxes in boxes_by_class.values() for (w, _h) in boxes]
        all_h = [h for boxes in boxes_by_class.values() for (_w, h) in boxes]
        all_area = [w * h for w, h in zip(all_w, all_h)]
        all_ratio = [(w / h) if h > 0 else 0.0 for w, h in zip(all_w, all_h)]
        print(f"  box width  (norm): mean {statistics.mean(all_w):.4f} median {statistics.median(all_w):.4f}")
        print(f"  box height (norm): mean {statistics.mean(all_h):.4f} median {statistics.median(all_h):.4f}")
        print(f"  box area   (norm): mean {statistics.mean(all_area):.5f} median {statistics.median(all_area):.5f}")
        print(f"  aspect ratio (w/h): mean {statistics.mean(all_ratio):.3f} median {statistics.median(all_ratio):.3f}")
        print("  average object size by class:")
        for class_id in sorted(boxes_by_class):
            widths = [w for w, _ in boxes_by_class[class_id]]
            heights = [h for _, h in boxes_by_class[class_id]]
            print(f"    {class_id} {class_names.get(class_id, '<unnamed>')}: "
                  f"n={len(widths)} mean {statistics.mean(widths):.4f}x{statistics.mean(heights):.4f}")

    print(f"Empty labels: {len(empty_labels)}")
    print(f"Corrupted images: {len(corrupted_images)}")
    print(f"Duplicate images: {sum(len(paths) for paths in duplicate_images)}")
    print(f"Duplicate label files: {sum(len(paths) for paths in duplicate_labels)}")
    print(f"Orphan labels (no image): {len(orphan_labels)}")
    print(f"Missing labels: {len(missing_labels)}")

    if empty_labels:
        print("Empty label files:")
        for path in empty_labels[:25]:
            print(f"  {path}")
    if corrupted_images:
        print("Corrupted images:")
        for error in corrupted_images[:25]:
            print(f"  {error}")
    if duplicate_images:
        print("Duplicate image groups:")
        for paths in duplicate_images[:10]:
            print("  " + " | ".join(str(path) for path in paths))
    if missing_labels:
        print("Missing label files:")
        for path in missing_labels[:25]:
            print(f"  {path}")
    if duplicate_labels:
        print("Duplicate label groups (identical content):")
        for paths in duplicate_labels[:10]:
            print("  " + " | ".join(path.name for path in paths))
    if orphan_labels:
        print("Orphan label files (no matching image):")
        for path in orphan_labels[:25]:
            print(f"  {path}")
    if label_errors:
        print("Label errors:")
        for error in label_errors[:50]:
            print(f"  {error}")

    failed = (
        image_count == 0
        or label_count == 0
        or bool(empty_labels)
        or bool(corrupted_images)
        or bool(duplicate_images)
        or bool(missing_labels)
        or bool(label_errors)
        or bool(missing_splits)
    )
    quality_components = {
        "pairs": 5.0 if not missing_labels and not orphan_labels else 2.0,
        "duplicates": 5.0 if not duplicate_images else 1.0,
        "annotations": 5.0 if not label_errors and not empty_labels else 1.0,
        "corruption": 5.0 if not corrupted_images else 1.0,
        "balance": 5.0 if balance_ratio <= 10 else 3.0 if balance_ratio <= 50 else 1.0,
        "splits": 5.0 if not missing_splits else 1.0,
    }
    quality_score = statistics.mean(quality_components.values())
    print("Dataset quality:")
    for name, score in quality_components.items():
        print(f"  {name}: {_star_score(score)} ({score:.1f}/5)")
    print(f"  overall: {_star_score(quality_score)} ({quality_score:.1f}/5)")
    print("Status: " + ("FAILED" if failed else "PASSED"))

    if report_path:
        report = {
            "config": str(config_path),
            "dataset_root": str(dataset_root),
            "status": "FAILED" if failed else "PASSED",
            "images": image_count,
            "labels": label_count,
            "splits": split_image_counts,
            "class_distribution": {str(class_id): distribution[class_id] for class_id in range(class_count)},
            "empty_labels": [str(path) for path in empty_labels],
            "corrupted_images": corrupted_images,
            "duplicate_images": [[str(path) for path in paths] for paths in duplicate_images],
            "duplicate_labels": [[str(path) for path in paths] for paths in duplicate_labels],
            "orphan_labels": [str(path) for path in orphan_labels],
            "missing_labels": [str(path) for path in missing_labels],
            "label_errors": label_errors,
            "missing_splits": missing_splits,
            "quality": {
                "overall": quality_score,
                "components": quality_components,
            },
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Validation report: {report_path}")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate YOLO dataset before training")
    parser.add_argument("--data", default="training/data.yaml", help="Path to YOLO data YAML")
    parser.add_argument("--report", default=None, help="Optional JSON report output path")
    args = parser.parse_args()
    report_path = Path(args.report) if args.report else None
    raise SystemExit(validate_dataset(Path(args.data), report_path))


if __name__ == "__main__":
    main()
