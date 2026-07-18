"""Resolve the active production dataset and generate training/data.yaml.

The dataset registry lives in config/dataset.yaml and the stable detector class
list lives in config/classes.yaml. This module is the programmatic entry point
that keeps training/data.yaml in sync without hardcoded class IDs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "dataset.yaml"
CLASSES_CONFIG_PATH = PROJECT_ROOT / "config" / "classes.yaml"
DATASETS_ROOT = PROJECT_ROOT / "training" / "datasets"
DATA_YAML_PATH = PROJECT_ROOT / "training" / "data.yaml"

DEFAULT_CLASSES = {
    0: "cricket_ball",
    1: "bat",
    2: "pad",
    3: "stumps",
    4: "batter",
    5: "bowler",
    6: "wicketkeeper",
    7: "umpire",
    8: "popping_crease",
    9: "bowling_crease",
}
DEFAULT_VERSIONS = ["dataset_v1", "dataset_v2", "dataset_v3", "production"]
DEFAULT_ACTIVE = "production"


def load_dataset_config() -> dict[str, Any]:
    """Load config/dataset.yaml, falling back to safe defaults if absent."""
    if not CONFIG_PATH.exists():
        return {
            "active": DEFAULT_ACTIVE,
            "datasets": list(DEFAULT_VERSIONS),
            "classes": dict(DEFAULT_CLASSES),
        }
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Dataset config must be a mapping: {CONFIG_PATH}")
    data.setdefault("active", DEFAULT_ACTIVE)
    data.setdefault("datasets", list(DEFAULT_VERSIONS))
    return data


def load_classes_config() -> dict[int, str]:
    """Load stable class IDs from config/classes.yaml.

    Backward compatibility: if config/classes.yaml is absent, accept the legacy
    config/dataset.yaml `classes` mapping.
    """
    if CLASSES_CONFIG_PATH.exists():
        data = yaml.safe_load(CLASSES_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Class config must be a mapping: {CLASSES_CONFIG_PATH}")
        classes = data.get("classes", DEFAULT_CLASSES)
        if isinstance(classes, list):
            return {index: str(name) for index, name in enumerate(classes)}
        if isinstance(classes, dict):
            return {int(class_id): str(name) for class_id, name in classes.items()}
        raise ValueError("config/classes.yaml `classes` must be a list or mapping")

    legacy_classes = load_dataset_config().get("classes", DEFAULT_CLASSES)
    if isinstance(legacy_classes, list):
        return {index: str(name) for index, name in enumerate(legacy_classes)}
    return {int(class_id): str(name) for class_id, name in legacy_classes.items()}


def class_names() -> dict[int, str]:
    """Return the active detector classes as an ordered {id: name} mapping."""
    return load_classes_config()


def dataset_versions() -> list[str]:
    """Return every dataset version slot, always including the active one."""
    config = load_dataset_config()
    versions = [str(name) for name in config.get("datasets", DEFAULT_VERSIONS)]
    active = str(config.get("active", DEFAULT_ACTIVE))
    if active not in versions:
        versions.append(active)
    return versions


def active_dataset_name() -> str:
    return str(load_dataset_config().get("active", DEFAULT_ACTIVE))


def active_dataset_dir() -> Path:
    """Absolute path of the dataset version currently in use."""
    return DATASETS_ROOT / active_dataset_name()


def write_data_yaml(path: Path = DATA_YAML_PATH) -> Path:
    """Regenerate training/data.yaml from the active dataset and class list."""
    names = class_names()
    dataset_dir = active_dataset_dir()
    lines = [
        "# AUTO-GENERATED from config/dataset.yaml and config/classes.yaml.",
        "# Edit those config files, then regenerate with:",
        "#   python scripts/init_production_dataset.py",
        f"path: {dataset_dir.as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        f"nc: {len(names)}",
        "names:",
    ]
    for class_id in sorted(names):
        lines.append(f"  {class_id}: {names[class_id]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
