"""Electron-facing command adapter for Vision Studio tooling.

Every command exposed here is also available as a normal command-line script.
This keeps the Electron dashboard thin and avoids coupling dataset or training
dependencies into the live DRS backend.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from development.config import PROJECT_ROOT, config_path, ensure_development_dirs, load_config, project_path


def _run(command: list[str]) -> int:
    import subprocess

    process = subprocess.run(command, cwd=PROJECT_ROOT)
    return int(process.returncode)


def _capture(command: list[str]) -> dict[str, Any]:
    import subprocess

    process = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True)
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "command": command,
    }


def _count_files(path: Path, suffixes: set[str]) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file() and item.suffix.lower() in suffixes)


def _gpu_status() -> dict[str, str]:
    import subprocess

    gpu = "Unavailable"
    cuda = "Unavailable"
    try:
        process = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=1.5,
        )
        if process.returncode == 0 and process.stdout.strip():
            gpu = process.stdout.strip().splitlines()[0]
    except Exception:
        pass

    try:
        import torch  # type: ignore

        cuda = "Available" if torch.cuda.is_available() else "Unavailable"
        if gpu == "Unavailable" and torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
    except Exception:
        pass

    return {"gpu": gpu, "cuda": cuda}


def _project_list(values: list[Any]) -> list[str]:
    return [str(project_path(value)) for value in values if str(value).strip()]


def _vision_version(project: Path) -> str:
    theme_path = project / "theme.py"
    try:
        for line in theme_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return "Unknown"


def _pid_running(pid_file: Path) -> tuple[bool, int | None]:
    try:
        if not pid_file.exists():
            return False, None
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True, pid
    except Exception:
        return False, None


def status() -> dict[str, Any]:
    ensure_development_dirs()
    config = load_config()
    vision_config = config.get("vision_studio", {})
    project = project_path(vision_config.get("project_path", "development/vision_studio"))
    executable = project_path(vision_config.get("executable", "development/vision_studio/dist/VisionStudio/VisionStudio.exe"))
    entry_point = project_path(vision_config.get("entry_point", "development/vision_studio/main.py"))
    launch_path = executable if executable.exists() else entry_point
    workspace = project_path(vision_config.get("workspace", "development/vision_studio"))
    model_path = project_path(vision_config.get("model", "models/cricket_ball_yolo11l_real.pt"))
    dataset_folder = project_path(vision_config.get("dataset_folder", config["dataset_root"]))
    training_folder = project_path(vision_config.get("training_folder", config.get("training_root", "training")))
    models_folder = project_path(vision_config.get("models_folder", config["models"]["production"]))
    exports_folder = config_path("dataset", "exports")
    pid_file = project_path(vision_config.get("pid_file", "data/vision_studio.pid"))
    running, pid = _pid_running(pid_file)
    recent_projects = _project_list(vision_config.get("recent_projects", []))
    hardware = _gpu_status()
    dataset_root = config_path("dataset_root")
    versions = config_path("dataset", "versions")
    production_models = project_path(config["models"]["production"])
    candidate_models = project_path(config["models"]["candidates"])
    return {
        "ok": True,
        "vision_studio": {
            "status": "Ready" if launch_path.exists() else "Not configured",
            "running": running,
            "pid": pid,
            "project": workspace.name,
            "version": _vision_version(project),
            "project_path": str(project),
            "executable": str(executable),
            "entry_point": str(entry_point),
            "launch_path": str(launch_path),
            "launch_type": "executable" if executable.exists() else "python",
            "workspace": str(workspace),
            "dataset": str(vision_config.get("dataset", "Production")),
            "model": str(model_path),
            "model_name": model_path.name,
            "dataset_folder": str(dataset_folder),
            "training_folder": str(training_folder),
            "models_folder": str(models_folder),
            "exports_folder": str(exports_folder),
            "pid_file": str(pid_file),
            "recent_projects": recent_projects,
            "gpu": hardware["gpu"],
            "cuda": hardware["cuda"],
        },
        "dataset": {
            "root": str(dataset_root),
            "raw_videos": _count_files(config_path("dataset", "raw_videos"), {".mp4", ".mov", ".avi", ".mts", ".m2ts"}),
            "versions": sorted(path.name for path in versions.iterdir() if path.is_dir()) if versions.exists() else [],
            "frames": _count_files(config_path("dataset", "frame_output"), {".jpg", ".jpeg", ".png"}),
            "exports": _count_files(config_path("dataset", "exports"), {".zip", ".yaml", ".yml"}),
        },
        "models": {
            "production": sorted(path.name for path in production_models.glob("*.pt")) if production_models.exists() else [],
            "candidates": sorted(path.name for path in candidate_models.glob("*.pt")) if candidate_models.exists() else [],
        },
    }


COMMANDS = {
    "dataset-versions": [sys.executable, "development/dataset_tools/dataset_version_manager.py", "list"],
    "dataset-stats": [sys.executable, "development/dataset_tools/dataset_statistics.py"],
    "validate-dataset": [sys.executable, "scripts/validate_yolo_dataset.py", "--data", "training/data.yaml"],
    "model-status": [sys.executable, "scripts/manage_models.py", "status"],
    "model-rollback": [sys.executable, "scripts/manage_models.py", "rollback"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-development dashboard command adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("name", choices=sorted(COMMANDS))
    args = parser.parse_args()

    if args.command == "status":
        print(json.dumps(status(), indent=2))
        return
    if args.command == "run":
        raise SystemExit(_run(COMMANDS[args.name]))


if __name__ == "__main__":
    main()
