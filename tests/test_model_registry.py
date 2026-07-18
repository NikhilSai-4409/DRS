"""Unit tests for the model registry — filesystem/json only, no torch."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from core.model_registry import ModelRegistry

FIXED = lambda: datetime(2026, 7, 4, 9, 30, 0)  # noqa: E731


def write_model(path: Path, content: bytes, sidecar: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if sidecar is not None:
        path.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")


@pytest.fixture()
def registry(tmp_path: Path) -> ModelRegistry:
    models = tmp_path / "models"
    # production active model with a Vision-Studio-style sidecar (mAP50/recall/training_date)
    write_model(models / "production" / "best.pt", b"OLD_PROD", sidecar={
        "model_name": "ball_v5", "dataset": "nets_v1", "training_date": "2026-07-02T14:40:16",
        "epochs": 100, "images": 1138, "mAP50": 0.967, "mAP50_95": 0.69,
        "precision": 0.93, "recall": 0.929, "notes": "prod",
    })
    # candidate with metrics only in model_evaluation.json (map50/ball_recall)
    write_model(models / "candidates" / "cand1.pt", b"CAND1")
    write_model(models / "archive" / "old.pt", b"ARCH")
    write_model(models / "loose.pt", b"LOOSE")
    (models / "model_evaluation.json").write_text(json.dumps({
        "cand1.pt": {"source": "real_mts", "map50": 0.42, "ball_recall": 0.5,
                     "precision": 0.6, "epochs": 50, "usable": False, "reason": "candidate"},
    }), encoding="utf-8")
    # validation history: a default-model run (→ production) and one for cand1
    history = tmp_path / "validation_history.json"
    history.write_text(json.dumps([
        {"run_id": "r1", "model": None, "accuracy": 0.90},
        {"run_id": "r2", "model": str(models / "candidates" / "cand1.pt"), "accuracy": 0.80},
    ]), encoding="utf-8")
    return ModelRegistry(models_dir=models, history_path=history, clock=FIXED)


def test_list_types_and_normalised_metadata(registry: ModelRegistry):
    records = {r.id: r for r in registry.list()}
    assert "production/best.pt" in records
    prod = records["production/best.pt"]
    assert prod.type == "production" and prod.is_production is True
    assert prod.name == "ball_v5" and prod.dataset == "nets_v1"
    assert prod.map50 == pytest.approx(0.967) and prod.recall == pytest.approx(0.929)
    assert prod.epochs == 100 and prod.notes == "prod"

    # candidate metrics come from model_evaluation.json (map50 / ball_recall aliases)
    cand = records["candidates/cand1.pt"]
    assert cand.type == "candidate" and cand.is_production is False
    assert cand.map50 == pytest.approx(0.42) and cand.recall == pytest.approx(0.5)
    assert cand.usable is False and cand.dataset == "real_mts"

    assert records["archive/old.pt"].type == "archive"
    assert records["loose.pt"].type == "other"


def test_validation_score_linked(registry: ModelRegistry):
    records = {r.id: r for r in registry.list()}
    assert records["production/best.pt"].validation_score == pytest.approx(0.90)
    assert records["production/best.pt"].validation_run_id == "r1"
    assert records["candidates/cand1.pt"].validation_score == pytest.approx(0.80)
    assert records["candidates/cand1.pt"].validation_run_id == "r2"
    assert records["loose.pt"].validation_score is None


def test_promote_backs_up_logs_and_updates_sidecar(registry: ModelRegistry):
    new_prod = registry.promote("candidates/cand1.pt")
    best = registry.production / "best.pt"
    assert best.read_bytes() == b"CAND1"                 # active model replaced
    assert (registry.production / "previous_best.pt").read_bytes() == b"OLD_PROD"
    assert list(registry.archive.glob("best_*.pt"))       # old prod archived
    assert new_prod.is_production and new_prod.id == "production/best.pt"
    # production sidecar now reflects the promoted model's metadata (from eval json)
    side = json.loads((registry.production / "best.json").read_text())
    assert side.get("map50") == pytest.approx(0.42)
    log = registry.deployment_history()
    assert log and log[-1]["action"] == "promote"


def test_rollback_restores_previous(registry: ModelRegistry):
    registry.promote("candidates/cand1.pt")
    restored = registry.rollback()
    assert (registry.production / "best.pt").read_bytes() == b"OLD_PROD"
    assert restored.is_production
    assert registry.deployment_history()[-1]["action"] == "rollback"


def test_rollback_without_previous_raises(registry: ModelRegistry):
    with pytest.raises(ValueError):
        registry.rollback()


def test_archive_moves_candidate_and_refuses_production(registry: ModelRegistry):
    rec = registry.archive_model("candidates/cand1.pt")
    assert rec.type == "archive"
    assert not (registry.candidates / "cand1.pt").exists()      # moved out
    assert (registry.archive / "cand1.pt").exists()
    with pytest.raises(ValueError):
        registry.archive_model("production/best.pt")


def test_delete_guards_production(registry: ModelRegistry):
    registry.delete("loose.pt")
    assert not (registry.models_dir / "loose.pt").exists()
    with pytest.raises(ValueError):
        registry.delete("production/best.pt")


def test_set_notes_writes_sidecar(registry: ModelRegistry):
    rec = registry.set_notes("candidates/cand1.pt", "promising on yorkers")
    assert rec.notes == "promising on yorkers"
    side = json.loads((registry.candidates / "cand1.json").read_text())
    assert side["notes"] == "promising on yorkers"
    # existing metrics preserved alongside the new note
    assert side.get("map50") == pytest.approx(0.42)


def test_production_model_path(registry: ModelRegistry):
    assert registry.production_model_path() == registry.production / "best.pt"


def test_promote_records_replaced_validation_and_by(registry: ModelRegistry):
    registry.promote("candidates/cand1.pt", reason="higher accuracy", by="Operator")
    entry = registry.deployment_history()[-1]
    assert entry["action"] == "promote"
    assert entry["replaced"] == "ball_v5"                    # previous production's model_name
    assert entry["by"] == "Operator" and entry["reason"] == "higher accuracy"
    assert entry["validation_score"] == pytest.approx(0.80)  # cand1's linked validation score
    assert entry["dataset"] == "real_mts" and entry["epochs"] == 50  # from cand1 metadata


def test_promote_source_external_file(registry: ModelRegistry, tmp_path: Path):
    ext = tmp_path / "external_model.pt"
    ext.write_bytes(b"EXT")
    rec = registry.promote_source(ext, by="Operator")
    assert (registry.production / "best.pt").read_bytes() == b"EXT"
    assert rec.is_production
    assert registry.deployment_history()[-1]["action"] == "promote"


def test_unknown_and_traversal_ids_are_safe(registry: ModelRegistry):
    assert registry.get("candidates/does_not_exist.pt") is None
    assert registry.get("../../../etc/passwd") is None
    with pytest.raises((ValueError, FileNotFoundError)):
        registry.promote("../../secret.pt")
