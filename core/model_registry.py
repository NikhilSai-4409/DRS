"""Model registry — the single source of truth for every detector model.

DRS had model info scattered: metrics in `models/model_evaluation.json` (keyed by
filename, using `map50`/`ball_recall`) AND in per-model sidecar json (Vision Studio,
using `mAP50`/`recall`), with promote/rollback implemented twice (the API archived to
`archive/best_*.pt`, `scripts/manage_models.py` used `previous_best.pt`). This module
unifies the READ side (one normalised `ModelRecord` per model, wherever it lives, with
its latest validation score attached) and owns the WRITE lifecycle (promote / rollback /
archive / delete / notes) with safe backups + a deployment log.

Everything is filesystem/json only — no torch — so it imports cheaply and unit-tests
without a model. Directories, history path, and clock are injectable for tests.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from core import lbw_validation  # cheap: no torch

try:
    from config.settings import BASE_DIR

    MODELS_DIR = BASE_DIR / "models"
except Exception:  # pragma: no cover
    MODELS_DIR = Path("models")

EVAL_METRICS_PATH = MODELS_DIR / "model_evaluation.json"
DEPLOYMENT_LOG = MODELS_DIR / "deployment_history.json"

# production/latest.pt is a byte-identical duplicate of best.pt — hide it so the
# manager shows one active model, not two.
_HIDDEN_PRODUCTION = {"latest.pt"}


@dataclass(slots=True)
class ModelRecord:
    id: str                     # posix relpath under models/, stable (e.g. "production/best.pt")
    name: str
    path: str
    type: str                   # production | previous | candidate | experiment | archive | other
    is_production: bool
    size_mb: float
    date_trained: str | None = None
    dataset: str | None = None
    epochs: int | None = None
    images: int | None = None
    map50: float | None = None
    map50_95: float | None = None
    precision: float | None = None
    recall: float | None = None
    inference_ms: float | None = None
    usable: bool | None = None
    notes: str = ""
    validation_score: float | None = None
    validation_run_id: str | None = None
    has_sidecar: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pick(meta: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in meta and meta[k] is not None:
            return meta[k]
    return None


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class ModelRegistry:
    def __init__(
        self,
        models_dir: str | Path = MODELS_DIR,
        history_path: str | Path = lbw_validation.HISTORY_PATH,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.models_dir = Path(models_dir)
        self.production = self.models_dir / "production"
        self.candidates = self.models_dir / "candidates"
        self.experiments = self.models_dir / "experiments"
        self.archive = self.models_dir / "archive"
        self.eval_metrics_path = self.models_dir / "model_evaluation.json"
        self.deployment_log = self.models_dir / "deployment_history.json"
        self.history_path = Path(history_path)
        self._clock = clock or datetime.now

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def list(self) -> list[ModelRecord]:
        by_name, production_score = self._validation_index()
        eval_metrics = self._eval_metrics()
        records: list[ModelRecord] = []
        seen: set[str] = set()

        def add(path: Path, type_: str, is_prod: bool) -> None:
            resolved = str(path.resolve())
            if resolved in seen:
                return
            seen.add(resolved)
            records.append(self._record(path, type_, is_prod, by_name, production_score, eval_metrics))

        # production (active + previous), latest.pt hidden
        for p in self._scan(self.production):
            if p.name in _HIDDEN_PRODUCTION:
                continue
            if p.name == "best.pt":
                add(p, "production", True)
            elif p.name == "previous_best.pt":
                add(p, "previous", False)
            else:
                add(p, "production", False)
        for p in self._scan(self.candidates):
            add(p, "candidate", False)
        for p in self._scan(self.experiments, recursive=True):
            add(p, "experiment", False)
        for p in self._scan(self.archive):
            add(p, "archive", False)
        for p in self._scan(self.models_dir):  # top-level *.pt
            add(p, "other", False)

        order = {"production": 0, "previous": 1, "candidate": 2, "experiment": 3, "other": 4, "archive": 5}
        records.sort(key=lambda r: (order.get(r.type, 9), r.name.lower()))
        return records

    def get(self, model_id: str) -> ModelRecord | None:
        try:
            path = self._resolve(model_id)
        except (ValueError, FileNotFoundError):
            return None
        by_name, production_score = self._validation_index()
        type_, is_prod = self._classify(path)
        return self._record(path, type_, is_prod, by_name, production_score, self._eval_metrics())

    # ------------------------------------------------------------------ #
    # Lifecycle (write)
    # ------------------------------------------------------------------ #
    def production_model_path(self) -> Path | None:
        """The active served model, or None. The one place anything should ask
        'which model is production' — no hardcoded models/production/best.pt."""
        best = self.production / "best.pt"
        return best if best.exists() else None

    def promote(self, model_id: str, reason: str | None = None, by: str | None = None) -> ModelRecord:
        """Promote a model that is already in the registry (by id)."""
        src = self._resolve(model_id)
        rec = self.get(model_id)
        return self._promote_src(src, reason, by, rec.validation_score if rec else None)

    def promote_source(self, source: str | Path, reason: str | None = None, by: str | None = None) -> ModelRecord:
        """Promote an arbitrary .pt (e.g. a browsed/external file) — same core path
        as promote(), so there is ONE promotion implementation."""
        src = Path(source)
        if src.suffix.lower() != ".pt" or not src.exists():
            raise FileNotFoundError(f"Model not found: {source}")
        return self._promote_src(src, reason, by, None)

    def _promote_src(self, src: Path, reason: str | None, by: str | None, val_score: float | None) -> ModelRecord:
        best = self.production / "best.pt"
        if best.exists() and src.resolve() == best.resolve():
            return self.get("production/best.pt")  # already the active model
        self.production.mkdir(parents=True, exist_ok=True)
        self.archive.mkdir(parents=True, exist_ok=True)
        replaced = self._production_name()  # capture BEFORE overwrite
        stamp = self._stamp()
        archived = None
        if best.exists():
            # back up current production so a regression is recoverable
            shutil.copy2(best, self.production / "previous_best.pt")
            self._copy_sidecar(best, self.production / "previous_best.pt")
            archived = f"best_{stamp}.pt"
            shutil.copy2(best, self.archive / archived)
            self._copy_sidecar(best, self.archive / archived)
        shutil.copy2(src, best)
        shutil.copy2(src, self.production / "latest.pt")
        # production sidecar reflects the promoted model's metadata
        meta = self._metadata(src)
        (self.production / "best.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self._log("promote", {
            "model": src.name, "replaced": replaced, "validation_score": val_score,
            "dataset": _pick(meta, "dataset", "source"), "epochs": _i(_pick(meta, "epochs")),
            "reason": reason or None, "by": by or None, "archived_previous": archived,
        })
        return self.get("production/best.pt")

    def rollback(self, by: str | None = None) -> ModelRecord:
        best = self.production / "best.pt"
        prev = self.production / "previous_best.pt"
        if not prev.exists():
            raise ValueError("No previous_best.pt to roll back to")
        self.archive.mkdir(parents=True, exist_ok=True)
        replaced = self._production_name()
        if best.exists():
            stamp = self._stamp()
            dest = self.archive / f"rollback_replaced_{stamp}.pt"
            shutil.copy2(best, dest)
            self._copy_sidecar(best, dest)
        shutil.copy2(prev, best)
        shutil.copy2(prev, self.production / "latest.pt")
        self._copy_sidecar(prev, best)  # restore metadata if previous_best.json exists
        self._log("rollback", {"model": "previous_best.pt", "replaced": replaced, "by": by or None})
        return self.get("production/best.pt")

    def _production_name(self) -> str | None:
        best = self.production / "best.pt"
        if not best.exists():
            return None
        return str(_pick(self._metadata(best), "model_name") or "best.pt")

    def archive_model(self, model_id: str) -> ModelRecord:
        src = self._resolve(model_id)
        type_, is_prod = self._classify(src)
        if is_prod:
            raise ValueError("Cannot archive the active production model — use rollback to retire it")
        if type_ == "archive":
            raise ValueError("Model is already archived")
        self.archive.mkdir(parents=True, exist_ok=True)
        dest = self._unique(self.archive / src.name)
        shutil.move(str(src), str(dest))
        sidecar = src.with_suffix(".json")
        if sidecar.exists():
            shutil.move(str(sidecar), str(dest.with_suffix(".json")))
        self._log("archive", {"model": str(src), "dest": str(dest)})
        return self.get(self._rel(dest))

    def delete(self, model_id: str) -> dict[str, Any]:
        src = self._resolve(model_id)
        _, is_prod = self._classify(src)
        if is_prod:
            raise ValueError("Cannot delete the active production model")
        src.unlink()
        sidecar = src.with_suffix(".json")
        if sidecar.exists():
            sidecar.unlink()
        self._log("delete", {"model": str(src)})
        return {"deleted": model_id}

    def set_notes(self, model_id: str, notes: str) -> ModelRecord:
        src = self._resolve(model_id)
        meta = self._metadata(src)
        meta["notes"] = notes
        src.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self._log("notes", {"model": str(src)})
        return self.get(model_id)

    def deployment_history(self) -> list[dict[str, Any]]:
        if not self.deployment_log.exists():
            return []
        try:
            data = json.loads(self.deployment_log.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _scan(self, folder: Path, recursive: bool = False) -> Iterator[Path]:
        if not folder.exists():
            return
        globber = folder.rglob if recursive else folder.glob
        for p in sorted(globber("*.pt")):
            if p.is_file():
                yield p

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.models_dir.resolve()).as_posix()

    def _resolve(self, model_id: str) -> Path:
        """Map a registry id (relpath) to a real .pt under models/, guarding traversal."""
        candidate = (self.models_dir / model_id).resolve()
        root = self.models_dir.resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError(f"Invalid model id: {model_id}")
        if candidate.suffix.lower() != ".pt" or not candidate.exists():
            raise FileNotFoundError(f"Model not found: {model_id}")
        return candidate

    def _classify(self, path: Path) -> tuple[str, bool]:
        rel = self._rel(path)
        parts = rel.split("/")
        top = parts[0] if len(parts) > 1 else ""
        name = path.name
        if top == "production":
            if name == "best.pt":
                return "production", True
            if name == "previous_best.pt":
                return "previous", False
            return "production", False
        return {"candidates": "candidate", "experiments": "experiment", "archive": "archive"}.get(top, "other"), False

    def _record(
        self,
        path: Path,
        type_: str,
        is_prod: bool,
        by_name: dict[str, dict],
        production_score: dict | None,
        eval_metrics: dict[str, Any],
    ) -> ModelRecord:
        meta = self._metadata(path, eval_metrics)
        try:
            size_mb = round(path.stat().st_size / 1_000_000, 1)
        except OSError:
            size_mb = 0.0
        date = _pick(meta, "training_date", "date", "trained")
        if not date:
            try:
                date = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            except OSError:
                date = None
        val = by_name.get(path.name)
        if val is None and is_prod and production_score:
            val = production_score  # a default-model (model=None) run tested production
        return ModelRecord(
            id=self._rel(path),
            name=str(_pick(meta, "model_name") or path.stem),
            path=str(path),
            type=type_,
            is_production=is_prod,
            size_mb=size_mb,
            date_trained=date,
            dataset=_pick(meta, "dataset", "source"),
            epochs=_i(_pick(meta, "epochs")),
            images=_i(_pick(meta, "images")),
            map50=_f(_pick(meta, "mAP50", "map50")),
            map50_95=_f(_pick(meta, "mAP50_95", "map50_95")),
            precision=_f(_pick(meta, "precision")),
            recall=_f(_pick(meta, "recall", "ball_recall")),
            inference_ms=_f(_pick(meta, "inference_ms")),
            usable=meta.get("usable") if isinstance(meta.get("usable"), bool) else None,
            notes=str(_pick(meta, "notes", "reason") or ""),
            validation_score=(val or {}).get("accuracy"),
            validation_run_id=(val or {}).get("run_id"),
            has_sidecar=path.with_suffix(".json").exists(),
        )

    def _metadata(self, path: Path, eval_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        sidecar = path.with_suffix(".json")
        if sidecar.exists():
            try:
                return json.loads(sidecar.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        metrics = eval_metrics if eval_metrics is not None else self._eval_metrics()
        return dict(metrics.get(path.name, {}))

    def _eval_metrics(self) -> dict[str, Any]:
        if not self.eval_metrics_path.exists():
            return {}
        try:
            data = json.loads(self.eval_metrics_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _validation_index(self) -> tuple[dict[str, dict], dict | None]:
        """Latest validation run per model basename; runs with model=None → production."""
        history = lbw_validation.load_history(self.history_path)
        by_name: dict[str, dict] = {}
        production: dict | None = None
        for run in history:  # chronological → last wins
            info = {"accuracy": run.get("accuracy"), "run_id": run.get("run_id")}
            model = run.get("model")
            if not model:
                production = info
            else:
                by_name[Path(str(model)).name] = info
        return by_name, production

    def _copy_sidecar(self, src: Path, dest: Path) -> None:
        s = src.with_suffix(".json")
        if s.exists():
            shutil.copy2(s, dest.with_suffix(".json"))

    def _unique(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        n = 1
        while (path.parent / f"{stem}_{n}{suffix}").exists():
            n += 1
        return path.parent / f"{stem}_{n}{suffix}"

    def _stamp(self) -> str:
        return self._clock().strftime("%Y%m%d_%H%M%S")

    def _log(self, action: str, payload: dict[str, Any]) -> None:
        history = self.deployment_history()
        history.append({"time": self._clock().isoformat(timespec="seconds"), "action": action, **payload})
        self.deployment_log.parent.mkdir(parents=True, exist_ok=True)
        self.deployment_log.write_text(json.dumps(history, indent=2), encoding="utf-8")
