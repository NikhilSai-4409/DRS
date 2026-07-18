"""End-to-end dataset import: CVAT YOLO export -> immutable training dataset version.

Accepts a YOLO ZIP export or an already-extracted YOLO dataset folder. Supported
layouts: CVAT "YOLO 1.1" (obj_*_data with image+txt siblings), the Ultralytics
images/labels split layout, and a flat folder of image+txt pairs. The dataset is
validated, copied into a new immutable training/datasets/dataset_vNNN version
(never overwriting an existing one), registered in training/datasets/database.json,
and training/data.yaml is regenerated from config/classes.yaml. Optionally the new
version is promoted to the active training dataset.

No manual copying of images or labels is required.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.dataset_config import DATASETS_ROOT, class_names, write_data_yaml  # noqa: E402

try:
    from scripts.validate_yolo_dataset import validate_dataset
except ModuleNotFoundError:
    from validate_yolo_dataset import validate_dataset

DATASET_CONFIG_PATH = PROJECT_ROOT / "config" / "dataset.yaml"
DATABASE_PATH = DATASETS_ROOT / "database.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SPLITS = ("train", "val", "test")


def _is_zip(path: Path) -> bool:
    return path.is_file() and zipfile.is_zipfile(path)


def _detect_split(relative: Path) -> str | None:
    """Infer a split from path segments (handles images/val, obj_validation_data, etc.)."""
    for part in relative.parts:
        lowered = part.lower()
        if "val" in lowered:  # validation, valid, val
            return "val"
        if "test" in lowered:
            return "test"
        if "train" in lowered:
            return "train"
    return None


def _find_label(image: Path, root: Path) -> Path | None:
    """Locate the YOLO .txt for an image: sibling first, then a parallel labels/ tree."""
    sibling = image.with_suffix(".txt")
    if sibling.exists():
        return sibling
    parts = list(image.relative_to(root).parts)
    for index, part in enumerate(parts):
        if part.lower() == "images":
            candidate = root.joinpath(*parts[:index], "labels", *parts[index + 1:]).with_suffix(".txt")
            if candidate.exists():
                return candidate
    return None


def _partition(stems: list[str], train: float, val: float, seed: int) -> dict[str, str]:
    """Deterministically map stems to train/val/test with non-empty splits.

    Uses a stable hash ordering then proportional slicing, guaranteeing at least
    one item in train and val (and test when there are >= 3 items) so a small
    export never yields an empty split that fails validation.
    """
    ordered = sorted(set(stems), key=lambda stem: hashlib.sha256(f"{seed}:{stem}".encode()).hexdigest())
    count = len(ordered)
    if count == 0:
        return {}
    if count == 1:
        return {ordered[0]: "train"}
    if count == 2:
        return {ordered[0]: "train", ordered[1]: "val"}
    test_ratio = max(0.0, 1.0 - train - val)
    n_val = max(1, round(count * val))
    n_test = max(1, round(count * test_ratio)) if test_ratio > 0 else 0
    if n_val + n_test >= count:  # always leave at least one for train
        n_test = max(0, count - n_val - 1)
    n_train = count - n_val - n_test
    mapping: dict[str, str] = {}
    for index, stem in enumerate(ordered):
        if index < n_train:
            mapping[stem] = "train"
        elif index < n_train + n_val:
            mapping[stem] = "val"
        else:
            mapping[stem] = "test"
    return mapping


def collect_pairs(root: Path) -> list[tuple[Path, Path | None, str | None]]:
    images = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    return [(image, _find_label(image, root), _detect_split(image.relative_to(root))) for image in images]


def next_version_name() -> str:
    """Lowest unused dataset_vNNN, scanning every existing dataset_v<int> directory."""
    DATASETS_ROOT.mkdir(parents=True, exist_ok=True)
    highest = 0
    for path in DATASETS_ROOT.glob("dataset_v*"):
        match = re.match(r"dataset_v0*(\d+)$", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"dataset_v{highest + 1:03d}"


def _write_version_yaml(version_dir: Path, names: dict[int, str]) -> Path:
    lines = [
        f"path: {version_dir.as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        f"nc: {len(names)}",
        "names:",
    ]
    for class_id in sorted(names):
        lines.append(f"  {class_id}: {names[class_id]}")
    path = version_dir / "data.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _activate_version(version: str) -> None:
    """Set the active dataset to `version` in config/dataset.yaml, preserving comments."""
    if DATASET_CONFIG_PATH.exists():
        text = DATASET_CONFIG_PATH.read_text(encoding="utf-8")
    else:
        text = "active: production\ndatasets:\n  - production\n"
    out: list[str] = []
    in_datasets = False
    have_version = False
    appended = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("active:"):
            out.append(f"active: {version}")
            continue
        if stripped.startswith("datasets:"):
            in_datasets = True
            out.append(line)
            continue
        if in_datasets and stripped.startswith("- "):
            if stripped[2:].strip() == version:
                have_version = True
            out.append(line)
            continue
        if in_datasets and not stripped.startswith("- "):
            if not have_version and not appended:
                out.append(f"  - {version}")
                appended = True
            in_datasets = False
        out.append(line)
    if in_datasets and not have_version and not appended:
        out.append(f"  - {version}")
    DATASET_CONFIG_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def _register(version: str, metadata: dict) -> None:
    database: dict = {}
    if DATABASE_PATH.exists():
        try:
            loaded = json.loads(DATABASE_PATH.read_text(encoding="utf-8"))
            database = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            database = {}
    database.setdefault("datasets", {})
    database["datasets"][version] = metadata
    database["updated_at"] = metadata["created_at"]
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH.write_text(json.dumps(database, indent=2), encoding="utf-8")


def import_annotations(
    source: Path | str,
    activate: bool = False,
    train: float = 0.8,
    val: float = 0.1,
    seed: int = 42,
    source_project: str = "cvat",
) -> dict:
    source = Path(source)
    if not source.exists():
        return {"ok": False, "message": f"Source not found: {source}"}

    names = class_names()
    temp_dir: Path | None = None
    try:
        if _is_zip(source):
            temp_dir = Path(tempfile.mkdtemp(prefix="drs_import_"))
            with zipfile.ZipFile(source) as archive:
                archive.extractall(temp_dir)
            root, import_source = temp_dir, source.name
        elif source.is_dir():
            root, import_source = source, str(source)
        else:
            return {"ok": False, "message": f"Source must be a .zip or a folder: {source}"}

        pairs = collect_pairs(root)
        usable = [(img, lbl, split) for (img, lbl, split) in pairs if lbl is not None]
        skipped = len(pairs) - len(usable)
        if not usable:
            return {"ok": False, "message": "No image/label pairs found in the export."}

        version = next_version_name()
        version_dir = DATASETS_ROOT / version
        if version_dir.exists():
            return {"ok": False, "message": f"Refusing to overwrite existing version: {version_dir}"}
        for split in SPLITS:
            (version_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (version_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        (version_dir / "metadata").mkdir(parents=True, exist_ok=True)

        # Only trust an export's own split layout when it actually has 2+ splits
        # (e.g. Ultralytics images/train + images/val). A single-bucket export
        # such as CVAT "YOLO 1.1" obj_train_data is re-split deterministically so
        # val/test are never empty.
        present_splits = {split for (_img, _lbl, split) in usable if split}
        honor_splits = len(present_splits) >= 2
        to_split = [image.stem for (image, _lbl, split) in usable if not (honor_splits and split)]
        partition = _partition(to_split, train, val, seed)

        counts = {"train": 0, "val": 0, "test": 0}
        labeled = 0
        annotations = 0
        used: dict[str, set[str]] = {split: set() for split in SPLITS}
        for image, label, split in usable:
            resolved = split if (honor_splits and split) else partition.get(image.stem, "train")
            name = image.name
            counter = 1
            while name in used[resolved]:
                name = f"{image.stem}_{counter}{image.suffix}"
                counter += 1
            used[resolved].add(name)
            label_name = Path(name).with_suffix(".txt").name
            shutil.copy2(image, version_dir / "images" / resolved / name)
            shutil.copy2(label, version_dir / "labels" / resolved / label_name)
            counts[resolved] += 1
            content = label.read_text(encoding="utf-8").strip()
            if content:
                labeled += 1
                annotations += sum(1 for line in content.splitlines() if line.strip())

        image_count = sum(counts.values())
        validation_score = round(100.0 * labeled / max(image_count, 1), 1)

        _write_version_yaml(version_dir, names)

        # Validate the freshly-built version; capture the validator's report.
        report_buffer = io.StringIO()
        with contextlib.redirect_stdout(report_buffer):
            status = validate_dataset(version_dir / "data.yaml")
        report_text = report_buffer.getvalue()
        validation_passed = status == 0

        if not validation_passed:
            # Never leave a broken version on disk; keep the report for diagnosis.
            (DATASETS_ROOT / "last_failed_import_report.txt").write_text(report_text, encoding="utf-8")
            shutil.rmtree(version_dir, ignore_errors=True)
            return {
                "ok": False,
                "message": "Dataset validation failed; import aborted (no version created).",
                "validation_passed": False,
                "validation_score": validation_score,
                "image_count": image_count,
                "label_count": labeled,
                "report": report_text,
            }

        created_at = datetime.now().isoformat(timespec="seconds")
        metadata = {
            "version": version,
            "created_at": created_at,
            "source_project": source_project,
            "import_source": import_source,
            "image_count": image_count,
            "label_count": labeled,
            "annotation_count": annotations,
            "class_count": len(names),
            "splits": counts,
            "skipped_without_labels": skipped,
            "validation_score": validation_score,
            "validation_passed": True,
        }
        (version_dir / "metadata" / "dataset_version.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (version_dir / "metadata" / "validation_report.txt").write_text(report_text, encoding="utf-8")
        _register(version, metadata)

        if activate:
            _activate_version(version)
        data_yaml = write_data_yaml()

        next_command = (
            "python scripts/train_yolo_drs.py"
            if activate
            else f"python scripts/import_annotations.py \"{source}\" --activate   (then: python scripts/train_yolo_drs.py)"
        )
        return {
            "ok": True,
            "version": version,
            "version_dir": str(version_dir),
            "activated": activate,
            "image_count": image_count,
            "label_count": labeled,
            "annotation_count": annotations,
            "class_count": len(names),
            "splits": counts,
            "skipped_without_labels": skipped,
            "validation_passed": True,
            "validation_score": validation_score,
            "data_yaml": str(data_yaml),
            "next_command": next_command,
            "report": report_text,
        }
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _print_summary(result: dict) -> None:
    print("=" * 60)
    if not result.get("ok"):
        print("IMPORT FAILED")
        print(result.get("message", "Unknown error"))
        if result.get("report"):
            print("-" * 60)
            print(result["report"].rstrip())
        print("=" * 60)
        return
    print("DATASET IMPORT COMPLETE")
    print(f"Version          : {result['version']}")
    print(f"Active dataset   : {'yes' if result['activated'] else 'no (use --activate to train on it)'}")
    print(f"Validation       : PASSED  (score {result['validation_score']}/100)")
    print(f"Images           : {result['image_count']}  (train {result['splits']['train']} / val {result['splits']['val']} / test {result['splits']['test']})")
    print(f"Labels (files)   : {result['label_count']}  |  annotations: {result['annotation_count']}")
    print(f"Classes          : {result['class_count']}")
    if result["skipped_without_labels"]:
        print(f"Skipped (no label): {result['skipped_without_labels']}")
    print(f"data.yaml        : {result['data_yaml']}")
    print(f"Next             : {result['next_command']}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a CVAT YOLO export into a new immutable training dataset version")
    parser.add_argument("source", help="YOLO export .zip or extracted dataset folder")
    parser.add_argument("--activate", action="store_true", help="Promote the imported dataset to the active training dataset")
    parser.add_argument("--train", type=float, default=0.8, help="Train ratio for exports without an explicit split")
    parser.add_argument("--val", type=float, default=0.1, help="Val ratio for exports without an explicit split (rest is test)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--source-project", default="cvat", help="Label recorded as the annotation source")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON result (for the dashboard)")
    args = parser.parse_args()

    if not 0.0 < args.train < 1.0 or not 0.0 <= args.val < 1.0 or args.train + args.val >= 1.0:
        raise SystemExit("Invalid ratios: require 0<train<1, 0<=val<1, train+val<1 (remainder is test)")

    result = import_annotations(
        args.source,
        activate=args.activate,
        train=args.train,
        val=args.val,
        seed=args.seed,
        source_project=args.source_project,
    )
    if args.json:
        print(json.dumps({key: value for key, value in result.items() if key != "report"}, indent=2))
    else:
        _print_summary(result)
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
