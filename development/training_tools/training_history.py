"""Read training history from Ultralytics run directories."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from development.config import project_path


def collect_history(root: Path) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    if not root.exists():
        return history
    for results_csv in root.rglob("results.csv"):
        rows = list(csv.DictReader(results_csv.open("r", encoding="utf-8")))
        last = rows[-1] if rows else {}
        history.append({
            "run": results_csv.parent.name,
            "path": str(results_csv.parent),
            "epochs": len(rows),
            "metrics": last,
        })
    return sorted(history, key=lambda item: str(item["run"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Print training history from model run directories")
    parser.add_argument("--root", default="models/training_runs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    history = collect_history(project_path(args.root))
    if args.json:
        print(json.dumps(history, indent=2))
        return
    print("Training history")
    for run in history:
        print(f"{run['run']}: {run['epochs']} epochs | {run['path']}")


if __name__ == "__main__":
    main()
