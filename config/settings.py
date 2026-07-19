"""Global settings for the cricket DRS prototype."""

import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}

try:
    from pydantic_settings import BaseSettings
except Exception:  # pragma: no cover - allows old environments to import before deps are installed
    BaseSettings = object  # type: ignore[misc,assignment]


class DRSSettings(BaseSettings):
    PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = PROJECT_ROOT / "data"
    MODELS_DIR: Path = PROJECT_ROOT / "models"
    LOG_LEVEL: str = "INFO"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8765
    TESTING_API_PORT: int = 8766
    CAMERA_SYNC_TOLERANCE_MS: float = 2.0
    REPLAY_BUFFER_SECONDS: float = 30.0
    BALL_CONFIDENCE_THRESHOLD: float = 0.45
    LBW_PITCH_ZONE_MARGIN_PX: int = 10
    STUMP_WIDTH_MM: float = 228.6
    STUMP_HEIGHT_MM: float = 711.2
    FRAME_HISTORY_SIZE: int = 300

    if BaseSettings is not object:
        model_config = {
            "env_file": ".env",
            "env_file_encoding": "utf-8",
            "extra": "ignore",
        }


settings = DRSSettings()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORTS_DIR = DATA_DIR / "exports"
DECISIONS_DIR = DATA_DIR / "decisions"
CALIBRATION_DIR = DATA_DIR / "calibration"
RECORDINGS_DIR = DATA_DIR / "recordings"
DETECTIONS_DIR = DATA_DIR / "detections"
TRACKING_DIR = DATA_DIR / "tracking"
SYNC_DIR = DATA_DIR / "sync"
AUDIO_DIR = DATA_DIR / "audio"
LOGS_DIR = DATA_DIR / "logs"
REVIEWS_DIR = DATA_DIR / "reviews"

for directory in (
    CALIBRATION_DIR,
    RECORDINGS_DIR,
    DETECTIONS_DIR,
    TRACKING_DIR,
    SYNC_DIR,
    AUDIO_DIR,
    LOGS_DIR,
    REVIEWS_DIR,
    EXPORTS_DIR,
    DECISIONS_DIR,
    BASE_DIR / "models",
):
    directory.mkdir(parents=True, exist_ok=True)

# Live-capture geometry. These are the DEFAULTS for live USB/capture-card
# capture; override per-deployment via environment (or .env) without editing
# code, e.g. DRS_FRAME_WIDTH=1920 DRS_FRAME_HEIGHT=1080 DRS_TARGET_FPS=120.
# NOTE: offline/upload analysis (the testing dashboard) ignores these and uses
# each source video's own native fps/resolution — see core/testing_pipeline.py.
CAMERA_IDS = [0, 1]
FRAME_WIDTH = _env_int("DRS_FRAME_WIDTH", 1280)
FRAME_HEIGHT = _env_int("DRS_FRAME_HEIGHT", 720)
TARGET_FPS = _env_int("DRS_TARGET_FPS", 60)
BUFFER_SECONDS = _env_int("DRS_BUFFER_SECONDS", 30)
SYNC_TOLERANCE_MS = _env_float("DRS_SYNC_TOLERANCE_MS", 8.0)
CAPTURE_QUEUE_SIZE = _env_int("DRS_CAPTURE_QUEUE_SIZE", 4)
# When a camera can't be opened, DON'T fabricate a synthetic "roaming ball" feed —
# report it as not connected instead. Set DRS_SYNTHETIC_CAMERAS=1 to re-enable the
# synthetic demo feed.
SYNTHETIC_CAMERAS = _env_bool("DRS_SYNTHETIC_CAMERAS", False)

VIDEO_CODEC = "mp4v"
VIDEO_EXT = ".mp4"

YOLO_MODEL_PATH = BASE_DIR / "models" / "cricket_ball_yolov8.pt"
YOLO_CONF_THRESH = 0.25
YOLO_IOU_THRESH = 0.45
YOLO_IMG_SIZE = _env_int("DRS_YOLO_IMG_SIZE", 640)

def _select_inference_device() -> str:
    """Auto-detect CUDA GPU; fall back to CPU if unavailable or broken."""
    try:
        from utils.inference_device import resolve_device
        return resolve_device("auto")
    except Exception:
        return "cpu"

INFERENCE_DEVICE = _select_inference_device()
USE_TENSORRT = False

KALMAN_PROCESS_NOISE = 1e-2
KALMAN_MEASUREMENT_NOISE = 1e-1
MAX_MISSING_FRAMES = 10
TRAJECTORY_HISTORY = 90

CHARUCO_SQUARES_X = 10
CHARUCO_SQUARES_Y = 7
CHARUCO_SQUARE_SIZE_MM = 75.0
CHARUCO_MARKER_SIZE_MM = 55.0
CHARUCO_DICTIONARY_ID = "DICT_5X5_1000"
CALIBRATION_MIN_IMAGES = 15

PITCH_LENGTH_M = 20.12
PITCH_WIDTH_M = 3.05
CREASE_TO_STUMPS_M = 1.22
STUMP_WIDTH_M = 0.2286
STUMP_HEIGHT_M = 0.711
BALL_RADIUS_M = 0.0363

# Review-engine analysis tuning -------------------------------------------------
# Lateral distance from the middle stump to the wide guideline (white-ball wide
# line is ~0.889 m / 35"). Configurable per competition.
WIDE_LINE_FROM_MIDDLE_M = 0.889
# A front foot is a no-ball once the back of the foot is this far past the
# popping crease (small tolerance to absorb projection noise).
NO_BALL_CREASE_MARGIN_MM = 0.0
# Cap the number of buffered replay frames a review module runs detection over.
REVIEW_ANALYSIS_MAX_FRAMES = 48
# Max frames written into a saved review replay clip (replay.mp4 per review).
# 600 @ 30fps = 20s — enough for run-up + delivery; override per deployment.
REPLAY_CLIP_MAX_FRAMES = _env_int("DRS_REPLAY_CLIP_MAX_FRAMES", 600)
GRAVITY_MPS2 = 9.81
BOUNCE_RESTITUTION = 0.58
DRAG_COEFFICIENT = 0.47
AIR_DENSITY = 1.225
BALL_MASS_KG = 0.156

AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SIZE = 1024
EDGE_FREQ_LOW_HZ = 1500
EDGE_FREQ_HIGH_HZ = 8000
EDGE_SPIKE_THRESHOLD = 3.5

WORKER_THREADS = 4
