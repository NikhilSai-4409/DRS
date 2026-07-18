"""Detector model selection and measurable model readiness metadata."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_CANDIDATES = [
    ("drs_production", Path("models/production/best.pt")),
    ("custom_cricket_yolo11l_real", Path("models/cricket_ball_yolo11l_real.pt")),
    ("yolo11x", Path("yolo11x.pt")),
    ("yolo11x", Path("models/yolo11x.pt")),
    ("yolo11l", Path("yolo11l.pt")),
    ("yolo11l", Path("models/yolo11l.pt")),
    ("yolov8x", Path("models/yolov8x.pt")),
    ("custom_cricket", Path("models/cricket_ball_yolov8.pt")),
]


@dataclass(slots=True)
class ModelReadiness:
    selected_model: str
    model_path: str
    detector_family: str
    map50: float | None
    map50_95: float | None
    ball_recall: float | None
    precision: float | None
    inference_ms: float | None
    usable: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DetectorModelSelector:
    """Prefers local YOLO11x/YOLO11l, then YOLOv8x, then the existing custom model."""

    def __init__(self, metrics_path: Path = Path("models/model_evaluation.json")) -> None:
        self.metrics_path = metrics_path

    def select(self, requested: Path | str | None = None) -> tuple[Path, ModelReadiness]:
        metrics = self._load_metrics()
        if requested:
            path = Path(requested)
            readiness = self._readiness_for(path, "requested", metrics)
            return path, readiness

        # The production model is whatever the registry says is production — the
        # single source of truth, not a hardcoded path. Falls through to the legacy
        # candidate list only if the registry has no production model.
        production = self._registry_production()
        if production is not None and production.exists():
            return production, self._readiness_for(production, "drs_production", metrics)

        for family, path in MODEL_CANDIDATES:
            if path.exists():
                readiness = self._readiness_for(path, family, metrics)
                return path, readiness

        fallback = Path("models/cricket_ball_yolov8.pt")
        return fallback, ModelReadiness(
            selected_model="missing",
            model_path=str(fallback),
            detector_family="none",
            map50=None,
            map50_95=None,
            ball_recall=None,
            precision=None,
            inference_ms=None,
            usable=False,
            reason="No local YOLO11/YOLOv8/custom model file found. Add a trained model under models/.",
        )

    def _readiness_for(self, path: Path, family: str, metrics: dict[str, Any]) -> ModelReadiness:
        record = metrics.get(path.name) or metrics.get(str(path)) or metrics
        map50 = _float_or_none(record.get("map50"))
        map50_95 = _float_or_none(record.get("map50_95"))
        ball_recall = _float_or_none(record.get("ball_recall", record.get("recall")))
        precision = _float_or_none(record.get("precision"))
        inference_ms = _float_or_none(record.get("inference_ms"))
        usable = bool(
            map50 is not None
            and ball_recall is not None
            and map50 >= 0.88
            and ball_recall >= 0.90
        )
        reason = "Model evaluation passes state-tournament thresholds." if usable else (
            "Model file is present, but validation metrics are missing or below thresholds."
        )
        return ModelReadiness(
            selected_model=path.name,
            model_path=str(path),
            detector_family=family,
            map50=map50,
            map50_95=map50_95,
            ball_recall=ball_recall,
            precision=precision,
            inference_ms=inference_ms,
            usable=usable,
            reason=reason,
        )

    def _registry_production(self) -> Path | None:
        """Ask the registry for the production model (lazy import avoids any cycle)."""
        try:
            from core.model_registry import ModelRegistry

            return ModelRegistry().production_model_path()
        except Exception:
            return None

    def _load_metrics(self) -> dict[str, Any]:
        if not self.metrics_path.exists():
            return {}
        try:
            return json.loads(self.metrics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
