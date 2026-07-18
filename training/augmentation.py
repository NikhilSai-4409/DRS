"""Configurable augmentation pipeline for DRS detector training.

Reads config/augmentation.yaml. Native augmentations map onto Ultralytics
train() kwargs and are applied during training. Photometric "extra"
augmentations (contrast, gamma, blur, noise, shadow) map onto an optional
Albumentations pipeline for offline augmentation when albumentations is
installed; everything degrades gracefully when it is not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "augmentation.yaml"

DEFAULTS: dict[str, dict[str, Any]] = {
    "scale": {"enabled": True, "magnitude": 0.35},
    "rotation": {"enabled": True, "magnitude": 2.0},
    "perspective": {"enabled": False, "magnitude": 0.0005},
    "horizontal_shift": {"enabled": True, "magnitude": 0.04},
    "vertical_shift": {"enabled": True, "magnitude": 0.04},
    "random_crop": {"enabled": False, "magnitude": 0.1},
    "mosaic": {"enabled": True, "magnitude": 0.65},
    "mixup": {"enabled": True, "magnitude": 0.05},
    "horizontal_flip": {"enabled": True, "magnitude": 0.5},
    "brightness": {"enabled": True, "magnitude": 0.35},
    "hue": {"enabled": True, "magnitude": 0.015},
    "saturation": {"enabled": True, "magnitude": 0.55},
    "contrast": {"enabled": False, "magnitude": 0.2},
    "gamma": {"enabled": False, "magnitude": 0.2},
    "motion_blur": {"enabled": False, "magnitude": 7},
    "gaussian_blur": {"enabled": False, "magnitude": 3},
    "noise": {"enabled": False, "magnitude": 0.02},
    "shadow": {"enabled": False, "magnitude": 0.5},
}

EXTRA_KEYS = ("contrast", "gamma", "motion_blur", "gaussian_blur", "noise", "shadow")


def load_augmentation_config(path: Path = CONFIG_PATH) -> dict[str, dict[str, Any]]:
    """Load augmentation.yaml merged over the defaults."""
    config = {key: dict(value) for key, value in DEFAULTS.items()}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if isinstance(value, dict):
                    merged = dict(DEFAULTS.get(key, {}))
                    merged.update(value)
                    config[key] = merged
    return config


def _magnitude_if_enabled(config: dict[str, dict[str, Any]], key: str) -> float:
    entry = config.get(key, {})
    return float(entry.get("magnitude", 0.0)) if entry.get("enabled") else 0.0


def native_kwargs(config: dict[str, dict[str, Any]] | None = None) -> dict[str, float]:
    """Translate the config into Ultralytics train() augmentation kwargs.

    Disabled augmentations resolve to 0.0 (off). Ultralytics applies a single
    symmetric translate for both axes, so the larger of the horizontal/vertical
    shifts is used.
    """
    config = config or load_augmentation_config()
    translate = max(
        _magnitude_if_enabled(config, "horizontal_shift"),
        _magnitude_if_enabled(config, "vertical_shift"),
    )
    return {
        "hsv_h": _magnitude_if_enabled(config, "hue"),
        "hsv_s": _magnitude_if_enabled(config, "saturation"),
        "hsv_v": _magnitude_if_enabled(config, "brightness"),
        "degrees": _magnitude_if_enabled(config, "rotation"),
        "translate": translate,
        "scale": _magnitude_if_enabled(config, "scale"),
        "perspective": _magnitude_if_enabled(config, "perspective"),
        "fliplr": _magnitude_if_enabled(config, "horizontal_flip"),
        "mosaic": _magnitude_if_enabled(config, "mosaic"),
        "mixup": _magnitude_if_enabled(config, "mixup"),
    }


def has_extra_augmentations(config: dict[str, dict[str, Any]] | None = None) -> bool:
    """True if any albumentations-backed photometric extra is enabled."""
    config = config or load_augmentation_config()
    return any(config.get(key, {}).get("enabled") for key in EXTRA_KEYS)


def build_extra_transforms(config: dict[str, dict[str, Any]] | None = None):
    """Return an albumentations.Compose for enabled photometric extras, or None.

    Returns None when no extra is enabled or albumentations is not installed.
    """
    config = config or load_augmentation_config()
    if not has_extra_augmentations(config):
        return None
    try:
        import albumentations as A
    except Exception:
        return None

    transforms = []
    if config.get("contrast", {}).get("enabled"):
        magnitude = float(config["contrast"]["magnitude"])
        transforms.append(A.RandomBrightnessContrast(brightness_limit=0.0, contrast_limit=magnitude, p=0.5))
    if config.get("gamma", {}).get("enabled"):
        magnitude = float(config["gamma"]["magnitude"])
        low = max(1, int(round(100 * (1.0 - magnitude))))
        high = int(round(100 * (1.0 + magnitude)))
        transforms.append(A.RandomGamma(gamma_limit=(low, high), p=0.5))
    if config.get("motion_blur", {}).get("enabled"):
        kernel = max(3, int(config["motion_blur"]["magnitude"]) | 1)
        transforms.append(A.MotionBlur(blur_limit=(3, kernel), p=0.3))
    if config.get("gaussian_blur", {}).get("enabled"):
        kernel = max(3, int(config["gaussian_blur"]["magnitude"]) | 1)
        transforms.append(A.GaussianBlur(blur_limit=(3, kernel), p=0.3))
    if config.get("noise", {}).get("enabled"):
        magnitude = float(config["noise"]["magnitude"])
        transforms.append(A.GaussNoise(var_limit=(0.0, magnitude * 255.0 * 255.0), p=0.3))
    if config.get("shadow", {}).get("enabled"):
        probability = float(config["shadow"]["magnitude"])
        transforms.append(A.RandomShadow(p=probability))

    return A.Compose(transforms) if transforms else None


def apply_offline(image, config: dict[str, dict[str, Any]] | None = None):
    """Apply enabled photometric extras to a numpy image; identity if unavailable."""
    compose = build_extra_transforms(config)
    if compose is None:
        return image
    return compose(image=image)["image"]
