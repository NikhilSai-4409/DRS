"""Tests for the configurable augmentation pipeline (no heavy deps required)."""

from __future__ import annotations

from training.augmentation import (
    has_extra_augmentations,
    load_augmentation_config,
    native_kwargs,
)

NATIVE_KEYS = (
    "hsv_h", "hsv_s", "hsv_v", "degrees", "translate",
    "scale", "perspective", "fliplr", "mosaic", "mixup",
)


def test_native_kwargs_has_all_keys_as_floats():
    kwargs = native_kwargs()
    for key in NATIVE_KEYS:
        assert key in kwargs
        assert isinstance(kwargs[key], float)


def test_disabled_augmentation_is_zero():
    config = load_augmentation_config()
    config["rotation"] = {"enabled": False, "magnitude": 5.0}
    assert native_kwargs(config)["degrees"] == 0.0


def test_enabled_augmentation_uses_magnitude():
    config = load_augmentation_config()
    config["scale"] = {"enabled": True, "magnitude": 0.4}
    assert native_kwargs(config)["scale"] == 0.4


def test_translate_is_max_of_horizontal_and_vertical_shift():
    config = load_augmentation_config()
    config["horizontal_shift"] = {"enabled": True, "magnitude": 0.03}
    config["vertical_shift"] = {"enabled": True, "magnitude": 0.07}
    assert native_kwargs(config)["translate"] == 0.07


def test_has_extra_augmentations_toggle():
    config = load_augmentation_config()
    for key in ("contrast", "gamma", "motion_blur", "gaussian_blur", "noise", "shadow"):
        config[key] = {"enabled": False, "magnitude": 1}
    assert has_extra_augmentations(config) is False
    config["gamma"] = {"enabled": True, "magnitude": 0.2}
    assert has_extra_augmentations(config) is True
