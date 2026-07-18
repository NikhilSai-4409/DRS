"""Dataset pipeline stage 1: extract frames from raw match video.

Reads high-FPS match footage and writes sharp, evenly-sampled frames into a
staging area ready for annotation. Blurry frames (low Laplacian variance) are
rejected so the dataset is not polluted with motion-smeared images.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.dataset_config import active_dataset_dir  # noqa: E402

VIDEO_SUFFIXES = {".mp4", ".mov", ".mts", ".m2ts", ".avi", ".mkv"}
STAGING = PROJECT_ROOT / "training" / "staging"


def find_videos(source: Path) -> list[Path]:
    if source.is_file():
        return [source] if source.suffix.lower() in VIDEO_SUFFIXES else []
    return sorted(p for p in source.rglob("*") if p.suffix.lower() in VIDEO_SUFFIXES)


def sharpness(frame) -> float:
    """Laplacian variance: a simple, robust focus/motion-blur metric."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_frames(
    source: Path | str,
    out_dir: Path | str,
    stride: int = 4,
    min_sharpness: float = 40.0,
    max_per_video: int | None = None,
    ext: str = "jpg",
    resize_width: int | None = None,
    fps_interval: float | None = None,
    metadata_path: Path | str | None = None,
) -> dict[str, int]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = find_videos(Path(source))
    if not videos:
        print(f"No videos found under: {source}")
    total_written = total_seen = total_blurry = 0
    metadata: dict[str, object] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source),
        "output": str(out_dir),
        "stride": stride,
        "fps_interval": fps_interval,
        "videos": [],
    }
    for video in videos:
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            print(f"WARNING: cannot open {video}")
            continue
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        effective_stride = max(1, int(round(fps * fps_interval))) if fps_interval and fps > 0 else max(1, stride)
        frame_index = written = 0
        video_metadata: dict[str, object] = {
            "path": str(video),
            "fps": fps,
            "stride": effective_stride,
            "frames": [],
        }
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % effective_stride == 0:
                total_seen += 1
                if resize_width and frame.shape[1] > resize_width:
                    height = int(frame.shape[0] * resize_width / frame.shape[1])
                    frame = cv2.resize(frame, (resize_width, height), interpolation=cv2.INTER_AREA)
                if min_sharpness > 0 and sharpness(frame) < min_sharpness:
                    total_blurry += 1
                else:
                    name = f"{video.stem}_f{frame_index:06d}.{ext}"
                    cv2.imwrite(str(out_dir / name), frame)
                    timestamp_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
                    video_metadata["frames"].append({
                        "file": name,
                        "frame_index": frame_index,
                        "timestamp_ms": float(timestamp_ms if timestamp_ms >= 0 else 0.0),
                    })
                    written += 1
                    total_written += 1
                    if max_per_video and written >= max_per_video:
                        break
            frame_index += 1
        capture.release()
        video_metadata["written"] = written
        metadata["videos"].append(video_metadata)
        print(f"{video.name}: wrote {written} frames")
    print(f"Frames sampled: {total_seen} | written: {total_written} | rejected (blurry): {total_blurry}")
    if metadata_path:
        metadata_target = Path(metadata_path)
        metadata_target.parent.mkdir(parents=True, exist_ok=True)
        metadata["summary"] = {
            "videos": len(videos),
            "seen": total_seen,
            "written": total_written,
            "blurry": total_blurry,
        }
        metadata_target.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {"videos": len(videos), "seen": total_seen, "written": total_written, "blurry": total_blurry}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract frames from raw match video (dataset pipeline stage 1)")
    parser.add_argument("--source", default=None, help="Video file or folder (default: active dataset videos/raw)")
    parser.add_argument("--out", default=str(STAGING / "frames"), help="Output frames directory")
    parser.add_argument("--stride", type=int, default=4, help="Keep every Nth frame")
    parser.add_argument("--fps-interval", type=float, default=None, help="Seconds between extracted frames; overrides stride when FPS is available")
    parser.add_argument("--min-sharpness", type=float, default=40.0, help="Reject frames below this Laplacian variance (0 disables)")
    parser.add_argument("--max-per-video", type=int, default=None)
    parser.add_argument("--resize-width", type=int, default=None, help="Downscale to this width if larger")
    parser.add_argument("--ext", default="jpg", choices=("jpg", "png"))
    args = parser.parse_args()

    source = Path(args.source) if args.source else (active_dataset_dir() / "videos" / "raw")
    if not source.exists():
        raise SystemExit(f"Source not found: {source}")
    metadata_path = Path(args.out) / "metadata" / "frame_extraction.json"
    extract_frames(
        source,
        args.out,
        args.stride,
        args.min_sharpness,
        args.max_per_video,
        args.ext,
        args.resize_width,
        args.fps_interval,
        metadata_path,
    )


if __name__ == "__main__":
    main()
