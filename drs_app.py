"""Command-line entry point for the cricket DRS system."""

from __future__ import annotations

import argparse
import time

import cv2

from config.settings import settings
from core.integration import DRSPipeline
from ui.dashboard import run_dashboard


RUNTIME_DIRECTORIES = (
    settings.DATA_DIR / "recordings",
    settings.DATA_DIR / "exports",
    settings.DATA_DIR / "calibration",
    settings.DATA_DIR / "decisions",
    settings.PROJECT_ROOT / "logs",
    settings.MODELS_DIR,
)


def ensure_runtime_directories() -> None:
    for directory in RUNTIME_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)


def parse_camera_ids(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def list_cameras(scan_limit: int) -> None:
    print("Scanning cameras...")
    found = []
    for camera_id in range(scan_limit):
        capture = cv2.VideoCapture(camera_id, cv2.CAP_ANY)
        ok, frame = capture.read() if capture.isOpened() else (False, None)
        if ok and frame is not None:
            height, width = frame.shape[:2]
            fps = capture.get(cv2.CAP_PROP_FPS)
            print(f"camera {camera_id}: OK  {width}x{height}  fps={fps:.1f}")
            found.append(camera_id)
        else:
            print(f"camera {camera_id}: unavailable")
        capture.release()
    if found:
        print("Use: python drs_app.py --cameras " + ",".join(str(item) for item in found))
    else:
        print("No cameras opened. Check USB/capture-card permissions and cables.")


def run_headless(camera_ids: list[int], seconds: float, record: bool) -> None:
    pipeline = DRSPipeline(camera_ids=camera_ids, record=record)
    pipeline.start()
    started = time.perf_counter()
    quit_requested = False
    try:
        while not quit_requested and (time.perf_counter() - started < seconds):
            state = pipeline.process_once()
            for camera_id, output in state.frames.items():
                cv2.imshow(f"DRS cam {camera_id}", output.annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                quit_requested = True
    finally:
        pipeline.export_tracking()
        pipeline.detector.flush("csv")
        pipeline.stop()
        cv2.destroyAllWindows()


def main() -> None:
    ensure_runtime_directories()
    parser = argparse.ArgumentParser(description="Cricket DRS prototype")
    parser.add_argument("--cameras", default="0,1", help="Comma-separated camera indices")
    parser.add_argument("--record", action="store_true", help="Save synchronized camera streams")
    parser.add_argument("--headless", action="store_true", help="Use OpenCV windows instead of Tkinter dashboard")
    parser.add_argument("--seconds", type=float, default=60.0, help="Runtime for headless mode")
    parser.add_argument("--list-cameras", action="store_true", help="Scan available camera indices and exit")
    parser.add_argument("--scan-limit", type=int, default=10, help="Number of camera indices to scan")
    parser.add_argument("--api", action="store_true", help="Run the unified FastAPI backend (live cameras + review engine + calibration + testing platform) for the Electron dashboard")
    parser.add_argument("--testing-api", action="store_true", help="Alias for --api: both run the same unified backend (kept for compatibility)")
    parser.add_argument("--training-app", action="store_true", help="Open the desktop YOLO training application")
    parser.add_argument("--host", default="127.0.0.1", help="API host")
    parser.add_argument("--port", type=int, default=8765, help="API port")
    parser.add_argument("--live", action="store_true", help="Start the N-camera live DRS pipeline (OpenCV window)")
    parser.add_argument("--primary-camera", type=int, default=None, help="Index of the primary camera in --live mode (defaults to the first camera)")
    args = parser.parse_args()

    if args.list_cameras:
        list_cameras(args.scan_limit)
        return

    camera_ids = parse_camera_ids(args.cameras)
    if args.training_app:
        from ui.training_app import run_training_app

        run_training_app()
    elif args.testing_api or args.api:
        # Single unified backend: real camera pipeline + review engine + 5-marker
        # calibration + the offline testing platform, all served from one API.
        from core.api_server import run_api

        run_api(camera_ids, args.record, args.host, args.port)
    elif args.live:
        from core.live_pipeline import MultiCameraLivePipeline

        pipeline = MultiCameraLivePipeline(
            camera_indices=camera_ids,
            primary_camera_index=args.primary_camera,
        )
        pipeline.start()
    elif args.headless:
        run_headless(camera_ids, args.seconds, args.record)
    else:
        run_dashboard(camera_ids, args.record)


if __name__ == "__main__":
    main()
