"""Create traceable training dataset versions from CVAT YOLO exports."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from development.config import dataset_classes, project_path
from scripts.validate_yolo_dataset import validate_dataset
from training.dataset_config import CONFIG_PATH, DATASETS_ROOT, write_data_yaml

IMAGE_SUFFIXES = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
SPLIT_ALIASES = {
    "train": "train",
    "training": "train",
    "valid": "val",
    "validation": "val",
    "val": "val",
    "test": "test",
}
DATABASE_PATH = DATASETS_ROOT / "database.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML must contain a mapping: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load_database() -> dict[str, Any]:
    if not DATABASE_PATH.exists():
        return {}
    data = json.loads(DATABASE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Dataset database must be a JSON object: {DATABASE_PATH}")
    return data


def save_database(data: dict[str, Any]) -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def next_version_name() -> str:
    DATASETS_ROOT.mkdir(parents=True, exist_ok=True)
    numbers: list[int] = []
    for path in DATASETS_ROOT.iterdir():
        if not path.is_dir() or not path.name.startswith("dataset_v"):
            continue
        suffix = path.name.removeprefix("dataset_v")
        if suffix.isdigit():
            numbers.append(int(suffix))
    return f"dataset_v{(max(numbers) + 1) if numbers else 1:03d}"


def prepare_source(source: Path) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    source = source.resolve()
    if not source.exists():
        raise SystemExit(f"YOLO export not found: {source}")
    if source.is_dir():
        return source, None
    if source.suffix.lower() != ".zip":
        raise SystemExit(f"Expected a YOLO export directory or .zip file: {source}")
    temp = tempfile.TemporaryDirectory(prefix="drs_yolo_export_")
    with zipfile.ZipFile(source, "r") as archive:
        archive.extractall(temp.name)
    return Path(temp.name), temp


def find_dataset_root(source: Path) -> Path:
    candidates = [source, *[path for path in source.rglob("*") if path.is_dir()]]
    for candidate in candidates:
        has_images = any((candidate / "images" / split).exists() for split in SPLIT_ALIASES)
        has_split_dirs = any((candidate / split).exists() for split in SPLIT_ALIASES)
        if (candidate / "data.yaml").exists() or has_images or has_split_dirs:
            return candidate
    raise SystemExit(f"Could not find YOLO dataset structure under: {source}")


def _copy_tree_files(source: Path, destination: Path, suffixes: set[str] | None = None) -> int:
    if not source.exists():
        return 0
    count = 0
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        if suffixes and item.suffix.lower() not in suffixes:
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def _split_dirs(root: Path, split: str) -> tuple[Path | None, Path | None]:
    aliases = [name for name, canonical in SPLIT_ALIASES.items() if canonical == split]
    for alias in aliases:
        images = root / "images" / alias
        labels = root / "labels" / alias
        if images.exists() or labels.exists():
            return images if images.exists() else None, labels if labels.exists() else None
    for alias in aliases:
        split_root = root / alias
        if not split_root.exists():
            continue
        images = split_root / "images"
        labels = split_root / "labels"
        if images.exists() or labels.exists():
            return images if images.exists() else None, labels if labels.exists() else None
        return split_root, split_root
    return None, None


def write_version_data_yaml(version_dir: Path) -> Path:
    classes = dataset_classes()
    lines = [
        "# AUTO-GENERATED for this immutable dataset version.",
        f"path: {version_dir.as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        f"nc: {len(classes)}",
        "names:",
    ]
    for class_id, name in sorted(classes.items()):
        lines.append(f"  {class_id}: {name}")
    path = version_dir / "data.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def copy_export_to_version(export_root: Path, version_dir: Path) -> dict[str, Any]:
    counts: dict[str, Any] = {"splits": {}}
    for split in ("train", "val", "test"):
        image_source, label_source = _split_dirs(export_root, split)
        image_destination = version_dir / "images" / split
        label_destination = version_dir / "labels" / split
        image_destination.mkdir(parents=True, exist_ok=True)
        label_destination.mkdir(parents=True, exist_ok=True)
        image_count = _copy_tree_files(image_source, image_destination, IMAGE_SUFFIXES) if image_source else 0
        label_count = _copy_tree_files(label_source, label_destination, {".txt"}) if label_source else 0
        counts["splits"][split] = {"images": image_count, "labels": label_count}

    metadata_dir = version_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for candidate in ("data.yaml", "obj.data", "classes.txt", "obj.names", "notes.json"):
        source = export_root / candidate
        if source.exists() and source.is_file():
            shutil.copy2(source, metadata_dir / source.name)
    write_version_data_yaml(version_dir)
    return counts


def label_distribution(version_dir: Path) -> dict[str, int]:
    counts: Counter[int] = Counter()
    for label_file in (version_dir / "labels").rglob("*.txt"):
        for line in label_file.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if not parts:
                continue
            try:
                counts[int(parts[0])] += 1
            except ValueError:
                continue
    return {str(class_id): counts[class_id] for class_id in sorted(dataset_classes())}


def update_dataset_registry(version_name: str, promote: bool) -> None:
    config = _load_yaml(CONFIG_PATH)
    versions = [str(name) for name in config.get("datasets", [])]
    if version_name not in versions:
        versions.append(version_name)
    config["datasets"] = versions
    if promote:
        config["active"] = version_name
    _write_yaml(CONFIG_PATH, config)
    if promote:
        write_data_yaml()


def create_version(source: Path, version_name: str | None, promote: bool, notes: str) -> Path:
    source_root, temp = prepare_source(source)
    try:
        export_root = find_dataset_root(source_root)
        version = version_name or next_version_name()
        version_dir = DATASETS_ROOT / version
        if version_dir.exists():
            raise SystemExit(f"Dataset version already exists and will not be overwritten: {version_dir}")
        version_dir.mkdir(parents=True)
        counts = copy_export_to_version(export_root, version_dir)
        data_yaml = version_dir / "data.yaml"
        validation_report = version_dir / "metadata" / "validation_report.json"
        validation_status = validate_dataset(data_yaml, validation_report)
        if validation_status != 0:
            shutil.rmtree(version_dir, ignore_errors=True)
            raise SystemExit("Dataset validation failed. Version was not created.")

        classes = dataset_classes()
        record = {
            "created": datetime.now().isoformat(timespec="seconds"),
            "source": str(source.resolve()),
            "path": str(version_dir),
            "classes": [classes[index] for index in sorted(classes)],
            "splits": counts["splits"],
            "images": sum(item["images"] for item in counts["splits"].values()),
            "labels": sum(item["labels"] for item in counts["splits"].values()),
            "class_distribution": label_distribution(version_dir),
            "validation_report": str(validation_report),
            "notes": notes,
            "promoted_to_active": promote,
        }
        database = load_database()
        database[version] = record
        save_database(database)
        update_dataset_registry(version, promote)
        print(f"Created dataset version: {version_dir}")
        print(f"Dataset database: {DATABASE_PATH}")
        if promote:
            print(f"Promoted active training dataset to: {version}")
        return version_dir
    finally:
        if temp is not None:
            temp.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create training dataset versions from CVAT YOLO exports")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Validate and create a new immutable dataset version")
    create_parser.add_argument("source", help="CVAT YOLO export .zip or extracted directory")
    create_parser.add_argument("--version", default=None, help="Version name, default: next dataset_vNNN")
    create_parser.add_argument("--promote", action="store_true", help="Make this version the active training dataset")
    create_parser.add_argument("--notes", default="")

    subparsers.add_parser("list", help="List dataset versions from database.json")

    args = parser.parse_args()
    if args.command == "create":
        create_version(project_path(args.source), args.version, args.promote, args.notes)
    elif args.command == "list":
        print(json.dumps(load_database(), indent=2))


if __name__ == "__main__":
    main()
