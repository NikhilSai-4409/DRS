"""Configuration helpers for AI-development tooling.

The live DRS runtime should not import this module. It exists for command-line
tools and the Electron development dashboard only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "development.yaml"
CLASSES_CONFIG_PATH = PROJECT_ROOT / "config" / "classes.yaml"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Development config not found: {CONFIG_PATH}")
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Development config must be a mapping: {CONFIG_PATH}")
    return data


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def config_path(*keys: str) -> Path:
    data: Any = load_config()
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            raise KeyError(".".join(keys))
        data = data[key]
    return project_path(str(data))


def dataset_classes() -> dict[int, str]:
    data = yaml.safe_load(CLASSES_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Class config must be a mapping: {CLASSES_CONFIG_PATH}")
    classes = data.get("classes", [])
    if isinstance(classes, list):
        return {index: str(name) for index, name in enumerate(classes)}
    if isinstance(classes, dict):
        return {int(class_id): str(name) for class_id, name in classes.items()}
    raise ValueError("config/classes.yaml `classes` must be a list or mapping")


def ensure_development_dirs() -> None:
    config = load_config()
    paths = [
        config["development_root"],
        config["dataset_root"],
        config.get("training_root", "training"),
        config.get("vision_studio", {}).get("project_path", "development/vision_studio"),
        config.get("vision_studio", {}).get("dataset_folder", config["dataset_root"]),
        config.get("vision_studio", {}).get("training_folder", config.get("training_root", "training")),
        config.get("vision_studio", {}).get("models_folder", config["models"]["production"]),
        Path(config.get("vision_studio", {}).get("pid_file", "data/vision_studio.pid")).parent,
        config["dataset"]["raw_videos"],
        config["dataset"]["versions"],
        config["dataset"]["calibration"],
        config["dataset"]["frame_output"],
        config["dataset"]["exports"],
        config["dataset"]["metadata"],
        config["models"]["production"],
        config["models"]["candidates"],
        config["models"]["archive"],
        config["models"]["evaluation_reports"],
        config["reports"]["validation"],
        config["reports"]["training"],
    ]
    for raw_path in paths:
        try:
            project_path(raw_path).mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
