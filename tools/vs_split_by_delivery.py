"""Re-split a Vision Studio prepared YOLO dataset BY DELIVERY, not by frame.

Why this exists
---------------
Vision Studio's "Prepare YOLO Dataset" splits train/val by shuffling individual
FRAMES. Consecutive frames of the same delivery are near-identical pictures, so
frame 10 lands in val while frames 9 and 11 sit in train — validation measures
memorization of the same scene and reports fiction (the Frontfoot_v2 run showed
0.99 pose mAP50 this way; on two held-out deliveries the model detected almost
nothing). The honest rule: ALL frames of a delivery go to train or val, whole.

The packaged Vision Studio app can't be changed from here (its pose-capable
source is not in this repo), so this tool runs BETWEEN the app's two steps:

    1. Vision Studio  ->  Prepare YOLO Dataset      (creates the split)
    2. THIS TOOL      ->  re-split by delivery      (fixes the split)
    3. Vision Studio  ->  Train                     (reads the split from disk)

Re-running "Prepare" in the app re-creates the frame split — run this tool
again afterwards.

What it does
------------
* Groups every image by its delivery (the flattened stem Vision Studio writes:
  ``Delivery001_000007.jpg`` -> group ``Delivery001``).
* Moves whole groups between ``images/{train,val}`` + ``labels/{train,val}``
  until val holds ~``--val-ratio`` of the FRAMES using the fewest whole
  deliveries (deterministic under ``--seed``).
* Rewrites the ``path:`` line of ``dataset.yaml`` to the dataset's real
  location (repairs stale absolute paths after a drive-letter change,
  e.g. ``E:`` -> ``F:``). Every other yaml line — ``kpt_shape``, ``flip_idx``,
  ``names`` — is preserved byte-for-byte.
* Reports class instance counts and names every declared class with ZERO
  instances (dead classes pollute the trained model's class map).

Usage
-----
    python tools/vs_split_by_delivery.py "<dataset-dir>" [--val-ratio 0.2]
                                         [--seed 42] [--dry-run]

``<dataset-dir>`` is a prepared dataset folder containing ``dataset.yaml``,
e.g. ``F:/II innigs/CAM A/Vision Studio/Annotations/bowl1_MP4``.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLITS = ("train", "val")
# ``Delivery001_000007`` -> group ``Delivery001``: strip ONE trailing frame
# number. Anything without a ``_<digits>`` tail keeps its full stem (and the
# tool refuses to run if that leaves nothing to group — see split_groups).
FRAME_SUFFIX = re.compile(r"_\d+$")


class SplitError(Exception):
    """Raised for anything that should stop the tool with a clear message."""


def group_of(stem: str) -> str:
    return FRAME_SUFFIX.sub("", stem)


def collect_frames(dataset_dir: Path) -> list[dict]:
    """Every image in images/{train,val} with its label path and group."""
    frames = []
    for split in SPLITS:
        image_dir = dataset_dir / "images" / split
        if not image_dir.is_dir():
            continue
        for image in sorted(image_dir.iterdir()):
            if not image.is_file() or image.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            frames.append({
                "image": image,
                "label": dataset_dir / "labels" / split / f"{image.stem}.txt",
                "split": split,
                "group": group_of(image.stem),
            })
    return frames


def split_groups(frames: list[dict], val_ratio: float, seed: int) -> dict[str, str]:
    """Assign each group to train/val so val holds ~val_ratio of the frames.

    Whole groups only. Deterministic for a given seed. Guarantees both splits
    are non-empty. Refuses to produce a frame-level split in disguise.
    """
    by_group: dict[str, int] = defaultdict(int)
    for frame in frames:
        by_group[frame["group"]] += 1

    groups = sorted(by_group)
    if len(groups) < 2:
        raise SplitError(
            f"Only {len(groups)} delivery group found — a held-out split needs "
            "at least 2. Annotate frames from another delivery/video first."
        )
    if len(groups) == len(frames):
        raise SplitError(
            "Every image is its own group — the stems don't look like "
            "'<Delivery>_<frame>' (e.g. Delivery001_000007). Refusing: this "
            "would silently reproduce the frame-level split."
        )

    total = len(frames)
    target = round(total * max(0.0, min(1.0, val_ratio)))
    rng = random.Random(seed)
    shuffled = groups[:]
    rng.shuffle(shuffled)

    val_groups: set[str] = set()
    val_frames = 0
    for group in shuffled:
        if val_frames >= target and val_groups:
            break
        if len(val_groups) == len(groups) - 1:  # always leave one for train
            break
        val_groups.add(group)
        val_frames += by_group[group]

    if not val_groups:  # target rounded to 0 — still hold out one delivery
        val_groups.add(shuffled[0])

    return {g: ("val" if g in val_groups else "train") for g in groups}


def apply_split(dataset_dir: Path, frames: list[dict], assignment: dict[str, str], dry_run: bool) -> int:
    """Move each frame (image + label) into its group's split. Returns moves."""
    for split in SPLITS:
        (dataset_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    moved = 0
    for frame in frames:
        want = assignment[frame["group"]]
        if frame["split"] == want:
            continue
        moved += 1
        if dry_run:
            continue
        for kind, folder in (("image", "images"), ("label", "labels")):
            source: Path = frame[kind]
            if not source.exists():
                print(f"  WARNING: missing {kind} for {source.stem} — pair is incomplete")
                continue
            destination = dataset_dir / folder / want / source.name
            if destination.exists():
                raise SplitError(f"Refusing to overwrite {destination}")
            source.rename(destination)
    return moved


def rewrite_yaml_path(yaml_path: Path, dataset_dir: Path, dry_run: bool) -> bool:
    """Point the yaml's ``path:`` at the dataset's REAL location.

    Only the ``path:`` line changes; kpt_shape / flip_idx / names and every
    comment survive byte-for-byte. Returns True if the line needed fixing.
    """
    text = yaml_path.read_text(encoding="utf-8")
    actual = dataset_dir.resolve().as_posix()
    lines = text.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines):
        if line.startswith("path:"):
            current = line.split(":", 1)[1].strip()
            if current != actual:
                lines[index] = f"path: {actual}\n"
                changed = True
            break
    if changed and not dry_run:
        yaml_path.write_text("".join(lines), encoding="utf-8")
    return changed


def declared_classes(yaml_path: Path) -> dict[int, str]:
    """The ``names:`` block of dataset.yaml as {class_id: name}."""
    classes: dict[int, str] = {}
    in_names = False
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "names:":
            in_names = True
            continue
        if in_names:
            match = re.match(r"\s+(\d+):\s*(\S+)", line)
            if not match:
                break
            classes[int(match.group(1))] = match.group(2)
    return classes


def class_instance_counts(frames: list[dict]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for frame in frames:
        label: Path = frame["label"]
        try:
            content = label.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            parts = line.split()
            if parts and parts[0].isdigit():
                counts[int(parts[0])] += 1
    return counts


def run(dataset_dir: Path, val_ratio: float, seed: int, dry_run: bool) -> None:
    dataset_dir = Path(dataset_dir)
    yaml_path = dataset_dir / "dataset.yaml"
    if not yaml_path.is_file():
        raise SplitError(
            f"No dataset.yaml in {dataset_dir} — run Vision Studio's "
            "'Prepare YOLO Dataset' first."
        )

    frames = collect_frames(dataset_dir)
    if not frames:
        raise SplitError(f"No images found under {dataset_dir / 'images'}.")

    assignment = split_groups(frames, val_ratio, seed)
    # Count label instances BEFORE moving — the frames list holds pre-move
    # paths, and label content is unaffected by which split a file sits in.
    counts = class_instance_counts(frames)
    moved = apply_split(dataset_dir, frames, assignment, dry_run)
    path_fixed = rewrite_yaml_path(yaml_path, dataset_dir, dry_run)

    groups_val = sorted(g for g, s in assignment.items() if s == "val")
    groups_train = sorted(g for g, s in assignment.items() if s == "train")
    frames_val = sum(1 for f in frames if assignment[f["group"]] == "val")
    frames_train = len(frames) - frames_val

    tag = "[dry run] " if dry_run else ""
    print(f"{tag}{dataset_dir}")
    print(f"  {len(frames)} frames in {len(assignment)} deliveries")
    print(f"  train: {len(groups_train)} deliveries, {frames_train} frames")
    print(f"  val:   {len(groups_val)} deliveries, {frames_val} frames "
          f"({frames_val / len(frames):.0%}) — held out WHOLE: {', '.join(groups_val)}")
    print(f"  moved {moved} frame pairs" + (" (nothing to do)" if moved == 0 else ""))
    if path_fixed:
        print(f"  dataset.yaml path: -> {dataset_dir.resolve().as_posix()}"
              + (" (would fix)" if dry_run else " (fixed)"))

    declared = declared_classes(yaml_path)
    if declared:
        present = ", ".join(
            f"{name}={counts.get(cid, 0)}" for cid, name in sorted(declared.items()) if counts.get(cid, 0)
        )
        print(f"  labels: {present or 'none'}")
        dead = [name for cid, name in sorted(declared.items()) if not counts.get(cid, 0)]
        if dead:
            print(f"  WARNING: declared classes with ZERO instances: {', '.join(dead)} "
                  "— they pollute the trained model's class map")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Prepared dataset folder (contains dataset.yaml)")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="Fraction of FRAMES to hold out (default 0.2)")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic group shuffle seed (default 42)")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan without touching any file")
    args = parser.parse_args(argv)
    try:
        run(Path(args.dataset), args.val_ratio, args.seed, args.dry_run)
    except SplitError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
