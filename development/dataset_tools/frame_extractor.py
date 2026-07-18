"""Config-driven frame extraction for AI-development datasets."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from development.config import config_path, project_path
from scripts.extract_frames import extract_frames


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract frames into the configured development dataset area")
    parser.add_argument("--source", default=None, help="Video file/folder; defaults to dataset.raw_videos")
    parser.add_argument("--split", default="train", choices=("train", "validation", "test"))
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--fps-interval", type=float, default=None, help="Seconds between frames; converted to stride when FPS is known later")
    parser.add_argument("--min-sharpness", type=float, default=40.0)
    parser.add_argument("--max-per-video", type=int, default=None)
    parser.add_argument("--resize-width", type=int, default=None)
    args = parser.parse_args()

    source = project_path(args.source) if args.source else config_path("dataset", "raw_videos")
    out_dir = config_path("dataset", "frame_output") / args.split
    metadata_dir = config_path("dataset", "metadata") / "frame_extraction"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_dir / f"extraction_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    result = extract_frames(
        source=source,
        out_dir=out_dir,
        stride=args.stride,
        min_sharpness=args.min_sharpness,
        max_per_video=args.max_per_video,
        resize_width=args.resize_width,
        fps_interval=args.fps_interval,
        metadata_path=metadata_path,
    )
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source),
        "output": str(out_dir),
        "split": args.split,
        "stride": args.stride,
        "fps_interval": args.fps_interval,
        "result": result,
    }
    sidecar_path = metadata_dir / f"extraction_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    sidecar_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    latest = metadata_dir / "latest.json"
    shutil.copy2(metadata_path, latest)
    print(f"Frame extraction metadata: {metadata_path}")


if __name__ == "__main__":
    main()
