"""Train and validate a multi-class YOLO model for DRS."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from scripts.validate_yolo_dataset import validate_dataset
except ModuleNotFoundError:
    from validate_yolo_dataset import validate_dataset

from training.augmentation import (
    build_extra_transforms,
    has_extra_augmentations,
    load_augmentation_config,
    native_kwargs,
)


def auto_device(requested: str = "auto") -> str:
    """Resolve the training device.

    When set to 'auto', pick the CUDA GPU with the most free memory so multi-GPU
    boxes use the least-loaded card; fall back to CPU if CUDA is unavailable.
    """
    if requested and str(requested).lower() != "auto":
        return str(requested)
    try:
        import torch

        if torch.cuda.is_available():
            best_index, best_free = 0, -1
            for index in range(torch.cuda.device_count()):
                try:
                    free, _total = torch.cuda.mem_get_info(index)
                except Exception:
                    free = 0
                if free > best_free:
                    best_index, best_free = index, free
            return str(best_index)
    except Exception:
        pass
    return "cpu"


def resolve_batch(value: str) -> int:
    """Map 'auto'/-1 to Ultralytics auto-batch (-1); otherwise a fixed size."""
    if str(value).strip().lower() in {"auto", "-1"}:
        return -1
    return int(value)


def cleanup_checkpoints(weights_dir: Path, keep: int) -> int:
    """Delete old periodic epoch checkpoints, keeping best.pt, last.pt and newest `keep`."""
    if keep < 0:
        return 0
    epoch_checkpoints = sorted(weights_dir.glob("epoch*.pt"), key=lambda path: path.stat().st_mtime)
    removed = 0
    for checkpoint in epoch_checkpoints[: max(0, len(epoch_checkpoints) - keep)]:
        try:
            checkpoint.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def metrics_to_dict(metrics: object) -> dict[str, float | str]:
    """Extract stable validation metrics from an Ultralytics metrics object."""
    box = getattr(metrics, "box", None)
    results = {
        "map50": float(getattr(box, "map50", 0.0) or 0.0),
        "map50_95": float(getattr(box, "map", 0.0) or 0.0),
        "precision": float(getattr(box, "mp", 0.0) or 0.0),
        "recall": float(getattr(box, "mr", 0.0) or 0.0),
    }
    save_dir = getattr(metrics, "save_dir", None)
    if save_dir:
        results["validation_dir"] = str(save_dir)
    return results


def _dataset_version_from_yaml(data_path: Path) -> str:
    try:
        import yaml

        data = yaml.safe_load(data_path.read_text(encoding="utf-8")) or {}
        return Path(str(data.get("path", ""))).name or "unknown"
    except Exception:
        return "unknown"


def write_experiment_record(
    run_dir: Path,
    args: argparse.Namespace,
    metrics: dict[str, float | str],
    training_seconds: float,
    experiment_model: Path | None,
    evaluation_status: str,
) -> Path:
    database_path = Path("training") / "experiments" / "database.json"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database = {}
    if database_path.exists():
        try:
            database = json.loads(database_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            database = {}
    run_name = run_dir.name
    precision = float(metrics.get("precision", 0.0) or 0.0)
    recall = float(metrics.get("recall", 0.0) or 0.0)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    database[run_name] = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "candidate_model": str(experiment_model) if experiment_model else None,
        "dataset_version": _dataset_version_from_yaml(Path(args.data)),
        "base_model": args.base_model,
        "epochs": args.epochs,
        "batch_size": args.batch,
        "image_size": args.imgsz,
        "device": args.device,
        "training_seconds": round(training_seconds, 2),
        "metrics": {
            **metrics,
            "f1": round(f1, 4),
        },
        "evaluation_status": evaluation_status,
        "deployment_status": "candidate",
    }
    database_path.write_text(json.dumps(database, indent=2), encoding="utf-8")
    return database_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train YOLO11l multi-class detector for DRS")
    parser.add_argument("--data", default="training/data.yaml", help="YOLO data YAML")
    parser.add_argument("--base-model", default="yolo11l.pt", help="YOLO11l starting model")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--batch", default="auto", help="Batch size, or 'auto'/-1 for automatic batch-size optimization")
    parser.add_argument("--device", default="auto", help="CUDA device id, cpu, or auto (auto = GPU with most free memory)")
    parser.add_argument("--scheduler", default="cosine", choices=("cosine", "linear"), help="Learning-rate scheduler")
    parser.add_argument("--lr0", type=float, default=None, help="Initial learning rate (override)")
    parser.add_argument("--lrf", type=float, default=None, help="Final LR fraction (override)")
    parser.add_argument("--keep-checkpoints", type=int, default=3, help="Periodic epoch checkpoints to retain (best/last always kept)")
    parser.add_argument("--project", default="models/training_runs")
    parser.add_argument("--name", default="drs_multiclass_yolo11l")
    parser.add_argument(
        "--export-best",
        default="models/drs_multiclass_yolo11l.pt",
        help="Destination for best.pt after validation",
    )
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--exist-ok", action="store_true", help="Allow reusing an existing run directory")
    parser.add_argument("--save-period", type=int, default=10, help="Save a checkpoint every N epochs")
    parser.add_argument("--resume", action="store_true", help="Resume the most recent interrupted run from its last.pt")
    parser.add_argument("--skip-evaluation", action="store_true", help="Skip post-training gate evaluation")
    parser.add_argument("--amp", dest="amp", action="store_true", default=True, help="Enable mixed-precision (AMP) training (default)")
    parser.add_argument("--no-amp", dest="amp", action="store_false", help="Disable mixed-precision training")
    parser.add_argument("--backup-dir", default="models/checkpoint_backups", help="Directory for checkpoint backups")
    args = parser.parse_args()

    data_path = Path(args.data)
    project_path = Path(args.project)
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset YAML not found: {data_path}")
    project_path.mkdir(parents=True, exist_ok=True)
    validation_status = validate_dataset(data_path)
    if validation_status != 0:
        raise SystemExit("Dataset validation failed. Training aborted.")

    from ultralytics import YOLO

    device = auto_device(args.device)
    batch = resolve_batch(args.batch)
    cos_lr = args.scheduler == "cosine"

    # Resume support: continue from the last checkpoint of the named run if present.
    resume_target = project_path / args.name / "weights" / "last.pt"
    resuming = bool(args.resume and resume_target.exists())
    if args.resume and not resuming:
        print(f"--resume requested but no checkpoint at {resume_target}; starting a fresh run.")

    print(f"Training device: {device}")
    print(f"Batch size: {'auto (-1)' if batch == -1 else batch}")
    print(f"LR scheduler: {args.scheduler}")
    print(f"Mixed precision (AMP): {'on' if args.amp else 'off'}")
    print(f"Dataset: {data_path}")
    print(f"Base model: {resume_target if resuming else args.base_model}")
    print("Per-epoch validation, confusion matrix, PR/mAP curves and TensorBoard "
          "logs are written to the run directory (view with: tensorboard --logdir "
          f"{project_path}).")

    lr_overrides: dict[str, float] = {}
    if args.lr0 is not None:
        lr_overrides["lr0"] = args.lr0
    if args.lrf is not None:
        lr_overrides["lrf"] = args.lrf

    # Configurable augmentation pipeline (config/augmentation.yaml).
    aug_config = load_augmentation_config()
    augmentation = native_kwargs(aug_config)
    print("Augmentations (native): " + ", ".join(f"{key}={value}" for key, value in augmentation.items()))
    if has_extra_augmentations(aug_config):
        extra = build_extra_transforms(aug_config)
        if extra is None:
            print("Extra photometric augmentations enabled in config but albumentations is not installed; skipping them.")
        else:
            print(f"Extra photometric augmentations ready for offline use: {len(extra.transforms)} transform(s).")

    started_at = time.perf_counter()
    model = YOLO(str(resume_target) if resuming else args.base_model)
    result = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=batch,
        device=device,
        project=args.project,
        name=args.name,
        patience=args.patience,
        amp=args.amp,
        val=True,
        resume=resuming,
        cos_lr=cos_lr,
        close_mosaic=15,
        shear=1.0,
        cache=False,
        workers=args.workers,
        plots=True,
        exist_ok=args.exist_ok,
        save_period=args.save_period,
        **augmentation,
        **lr_overrides,
    )

    best = Path(result.save_dir) / "weights" / "best.pt"
    if not best.exists():
        raise FileNotFoundError(f"Training finished but best.pt was not found: {best}")

    print(f"Training complete. Best model: {best}")

    # Back up best.pt and last.pt outside the run directory so a later run or an
    # accidental clean cannot destroy a good checkpoint.
    backup_root = Path(args.backup_dir) / Path(result.save_dir).name
    backup_root.mkdir(parents=True, exist_ok=True)
    for checkpoint in ("best.pt", "last.pt"):
        source = Path(result.save_dir) / "weights" / checkpoint
        if source.exists():
            shutil.copy2(source, backup_root / checkpoint)
    print(f"Checkpoints backed up to: {backup_root}")

    removed = cleanup_checkpoints(Path(result.save_dir) / "weights", args.keep_checkpoints)
    if removed:
        print(f"Cleaned up {removed} old epoch checkpoint(s); kept best.pt, last.pt and newest {args.keep_checkpoints}.")

    trained_model = YOLO(str(best))
    validation = trained_model.val(
        data=args.data,
        imgsz=args.imgsz,
        batch=(batch if batch and batch > 0 else 8),
        device=device,
        project=args.project,
        name=f"{args.name}_validation",
        plots=True,
        exist_ok=args.exist_ok,
    )

    metrics = metrics_to_dict(validation)
    training_seconds = time.perf_counter() - started_at
    metrics_path = Path(result.save_dir) / "validation_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    destination = Path(args.export_best)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, destination)

    # Register a copy of this run's best model in the experiments registry so it
    # can later be promoted to production with backup (scripts/manage_models.py).
    experiment_copy: Path | None = None
    try:
        from scripts.manage_models import save_experiment

        experiment_copy = save_experiment(best, Path(result.save_dir).name)
        print(f"Experiment model registered: {experiment_copy}")
    except Exception as exc:  # registry copy is best-effort, never fails training
        print(f"(experiment registry copy skipped: {exc})")

    evaluation_status = "skipped"
    if not args.skip_evaluation:
        evaluation_report = Path(result.save_dir) / "evaluation_report.txt"
        evaluation_command = [
            sys.executable,
            "scripts/evaluate_yolo_drs.py",
            "--model",
            str(best),
            "--data",
            args.data,
            "--imgsz",
            str(args.imgsz),
            "--device",
            device,
        ]
        evaluation = subprocess.run(evaluation_command, text=True, capture_output=True)
        evaluation_report.write_text(evaluation.stdout + evaluation.stderr, encoding="utf-8")
        evaluation_status = "passed" if evaluation.returncode == 0 else "failed"
        print(f"Post-training evaluation {evaluation_status}: {evaluation_report}")

    experiment_database = write_experiment_record(
        Path(result.save_dir),
        args,
        metrics,
        training_seconds,
        experiment_copy,
        evaluation_status,
    )

    print("Validation complete")
    print(f"mAP50: {metrics['map50']:.4f}")
    print(f"mAP50-95: {metrics['map50_95']:.4f}")
    print(f"Precision: {metrics['precision']:.4f}")
    print(f"Recall: {metrics['recall']:.4f}")
    print(f"Validation metrics: {metrics_path}")
    print(f"Confusion matrix and plots: {metrics.get('validation_dir', Path(args.project) / (args.name + '_validation'))}")
    print(f"Best model copied to: {destination}")
    print(f"Experiment database: {experiment_database}")


if __name__ == "__main__":
    main()
