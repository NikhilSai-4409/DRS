"""Single source of truth for per-camera calibration file paths.

Intrinsics and pitch pose are stored in dedicated files so one can never overwrite
the other; ``calibration_<id>.json`` is the legacy shared name, read only for
backward compatibility. Every reader/writer builds its paths through these helpers
so the naming lives in exactly one place.

Each helper takes an optional ``base`` directory. Callers pass their own module's
``CALIBRATION_DIR`` (which tests monkeypatch), so path construction is centralised
without defeating per-module test isolation. Omitting ``base`` uses the configured
data directory.
"""

from __future__ import annotations

from pathlib import Path

from config.settings import CALIBRATION_DIR


def intrinsics_filename(camera_id: int | str) -> str:
    return f"intrinsics_{camera_id}.json"


def pose_filename(camera_id: int | str) -> str:
    return f"pose_{camera_id}.json"


def legacy_filename(camera_id: int | str) -> str:
    return f"calibration_{camera_id}.json"


def get_intrinsics_path(camera_id: int | str, base: Path | None = None) -> Path:
    return (base or CALIBRATION_DIR) / intrinsics_filename(camera_id)


def get_pose_path(camera_id: int | str, base: Path | None = None) -> Path:
    return (base or CALIBRATION_DIR) / pose_filename(camera_id)


def get_legacy_path(camera_id: int | str, base: Path | None = None) -> Path:
    return (base or CALIBRATION_DIR) / legacy_filename(camera_id)
