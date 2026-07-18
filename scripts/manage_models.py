"""Model management: organize trained detectors into production/experiments/archive.

models/
    production/   best.pt, latest.pt, previous_best.pt  (the served model + backups)
    experiments/  <run_name>/best.pt                     (per-training-run copies)
    archive/      best_<stamp>.pt                         (retired production models)

Promotion never overwrites the current production model without first copying it
to previous_best.pt and into archive/, so a regression is always recoverable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODELS_DIR = PROJECT_ROOT / "models"
PRODUCTION_DIR = MODELS_DIR / "production"
EXPERIMENTS_DIR = MODELS_DIR / "experiments"
CANDIDATES_DIR = MODELS_DIR / "candidates"
ARCHIVE_DIR = MODELS_DIR / "archive"
DEPLOYMENT_DB = MODELS_DIR / "deployment_history.json"


def ensure_dirs() -> None:
    for directory in (PRODUCTION_DIR, EXPERIMENTS_DIR, CANDIDATES_DIR, ARCHIVE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")


def save_experiment(model_path: Path | str, run_name: str) -> Path:
    """Copy a trained model into experiments and candidate registries."""
    ensure_dirs()
    destination_dir = EXPERIMENTS_DIR / run_name
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / "best.pt"
    shutil.copy2(str(model_path), str(destination))
    candidate = CANDIDATES_DIR / f"{run_name}.pt"
    shutil.copy2(str(model_path), str(candidate))
    return destination


def _record_deployment(action: str, payload: dict) -> None:
    history = []
    if DEPLOYMENT_DB.exists():
        try:
            existing = json.loads(DEPLOYMENT_DB.read_text(encoding="utf-8"))
            history = existing if isinstance(existing, list) else []
        except json.JSONDecodeError:
            history = []
    history.append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        **payload,
    })
    DEPLOYMENT_DB.write_text(json.dumps(history, indent=2), encoding="utf-8")


def promote(model_path: Path | str, label: str | None = None) -> Path:
    """Promote a model to production, backing up any existing production model."""
    ensure_dirs()
    model_path = Path(model_path)
    if not model_path.exists():
        raise SystemExit(f"Model not found: {model_path}")

    production_best = PRODUCTION_DIR / "best.pt"
    production_latest = PRODUCTION_DIR / "latest.pt"
    production_previous = PRODUCTION_DIR / "previous_best.pt"

    if production_best.exists():
        shutil.copy2(production_best, production_previous)
        stamp = label or datetime.now().strftime("%Y%m%d_%H%M%S")
        archived = ARCHIVE_DIR / f"best_{stamp}.pt"
        shutil.copy2(production_best, archived)
        print(f"Backed up current production model -> previous_best.pt and archive/{archived.name}")

    shutil.copy2(model_path, production_best)
    shutil.copy2(model_path, production_latest)
    _record_deployment("deploy", {
        "model": str(model_path),
        "production": str(production_best),
        "label": label,
    })
    print(f"Promoted {model_path} -> production/best.pt and production/latest.pt")
    return production_best


def rollback() -> Path:
    """Restore previous_best.pt as production best/latest with archival backup."""
    ensure_dirs()
    production_best = PRODUCTION_DIR / "best.pt"
    production_latest = PRODUCTION_DIR / "latest.pt"
    production_previous = PRODUCTION_DIR / "previous_best.pt"
    if not production_previous.exists():
        raise SystemExit(f"No rollback model found: {production_previous}")
    if production_best.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archived = ARCHIVE_DIR / f"rollback_replaced_{stamp}.pt"
        shutil.copy2(production_best, archived)
    shutil.copy2(production_previous, production_best)
    shutil.copy2(production_previous, production_latest)
    _record_deployment("rollback", {
        "model": str(production_previous),
        "production": str(production_best),
    })
    print("Rolled back production model to previous_best.pt")
    return production_best


def status() -> None:
    ensure_dirs()
    print(f"Production directory: {PRODUCTION_DIR}")
    for name in ("best.pt", "latest.pt", "previous_best.pt"):
        path = PRODUCTION_DIR / name
        print(f"  {name}: {'present' if path.exists() else 'absent'}")
    experiments = sorted(p.name for p in EXPERIMENTS_DIR.iterdir() if p.is_dir()) if EXPERIMENTS_DIR.exists() else []
    candidates = sorted(p.name for p in CANDIDATES_DIR.glob("*.pt")) if CANDIDATES_DIR.exists() else []
    archived = sorted(p.name for p in ARCHIVE_DIR.glob('*.pt')) if ARCHIVE_DIR.exists() else []
    print(f"  experiments: {len(experiments)}")
    print(f"  candidates: {len(candidates)}")
    print(f"  archived models: {len(archived)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage trained DRS detector models")
    subparsers = parser.add_subparsers(dest="command", required=True)

    promote_parser = subparsers.add_parser("promote", help="Promote a model to production with backup")
    promote_parser.add_argument("model", help="Path to the model to promote (e.g. models/experiments/<run>/best.pt)")
    promote_parser.add_argument("--label", default=None, help="Optional label for the archived backup")

    experiment_parser = subparsers.add_parser("save-experiment", help="Copy a model into experiments/<run_name>")
    experiment_parser.add_argument("model")
    experiment_parser.add_argument("run_name")

    subparsers.add_parser("status", help="Show production model status")
    subparsers.add_parser("rollback", help="Restore production/previous_best.pt")

    args = parser.parse_args()
    if args.command == "promote":
        promote(args.model, args.label)
    elif args.command == "save-experiment":
        destination = save_experiment(args.model, args.run_name)
        print(f"Saved experiment model: {destination}")
    elif args.command == "status":
        status()
    elif args.command == "rollback":
        rollback()


if __name__ == "__main__":
    main()
