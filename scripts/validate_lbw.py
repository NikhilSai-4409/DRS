"""Run the LBW ground-truth validation set and report accuracy + regressions.

Headless — no backend, no Electron. Drives the real DeliveryTestingPipeline for
each labelled clip in the manifest, compares the DRS verdict against the known
outcome, and writes a per-run report (+ a diff against the previous run).

Examples
--------
    python scripts/validate_lbw.py
    python scripts/validate_lbw.py --model models/production/best.pt
    python scripts/validate_lbw.py --limit 5 --fail-on-regression
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/validate_lbw.py` from the project root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.lbw_validation import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    HISTORY_PATH,
    RUNS_DIR,
    ClipSpec,
    LbwValidator,
    latest_run,
    load_manifest,
    write_run,
)

ICON = {"correct": "PASS", "incorrect": "FAIL", "error": "ERR "}


def _progress(index: int, total: int, spec: ClipSpec) -> None:
    print(f"  [{index + 1}/{total}] {spec.id} ({spec.path}) ...", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate LBW decisions against ground truth.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH),
                        help="Path to the validation manifest JSON.")
    parser.add_argument("--model", default=None,
                        help="Override the detection model (.pt) for this run.")
    parser.add_argument("--calibration", default=None,
                        help="Override the intended calibration profile for this run.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N clips.")
    parser.add_argument("--runs-dir", default=str(RUNS_DIR),
                        help="Directory to write per-run reports into.")
    parser.add_argument("--history", default=str(HISTORY_PATH),
                        help="History file to diff against and append to.")
    parser.add_argument("--no-write", action="store_true",
                        help="Do not persist the report or append history.")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="Exit non-zero if any clip regressed vs the previous run.")
    args = parser.parse_args()

    try:
        manifest = load_manifest(args.manifest)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not manifest.clips:
        print("Manifest has no clips. Add labelled deliveries to", args.manifest)
        return 2

    print(f"Validating {len(manifest.clips)} clip(s) from {args.manifest}")
    print(f"Model: {args.model or manifest.defaults.get('model_path') or 'default'}")
    print()

    validator = LbwValidator()
    previous = latest_run(args.history)
    run = validator.run(
        manifest,
        model_override=args.model,
        calibration_override=args.calibration,
        limit=args.limit,
        previous=previous,
        progress=_progress,
    )

    # ---- console report ---------------------------------------------------- #
    print()
    print("=" * 78)
    print(f"  Run {run.run_id}   accuracy {run.correct}/{run.scored} = {run.accuracy * 100:.1f}%"
          + (f"   ({run.errors} errored)" if run.errors else ""))
    print("=" * 78)
    header = f"  {'RESULT':6} {'CLIP':18} {'EXPECTED':14} {'ACTUAL':14} {'CONF':5}  REASON"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for c in run.clips:
        print(f"  {ICON.get(c.status, '?'):6} {c.id[:18]:18} {c.expected_verdict[:14]:14} "
              f"{c.actual_verdict[:14]:14} {c.detection_confidence:4.2f}  {c.reason_for_failure}")

    print()
    print(f"  Avg detection confidence : {run.avg_detection_confidence:.2f}")
    print(f"  Avg processing time      : {run.avg_processing_time_s:.2f}s")
    print(f"  Replays generated        : {run.replay_success}/{run.total}")

    if run.regressions:
        print()
        print(f"  !! {len(run.regressions)} REGRESSION(S) vs run {run.previous_run_id}:")
        for r in run.regressions:
            print(f"     - {r['id']}: {r['was']} -> {r['now']} (expected {r['expected']})")
    if run.improvements:
        print()
        print(f"  ++ {len(run.improvements)} improvement(s) vs run {run.previous_run_id}:")
        for r in run.improvements:
            print(f"     - {r['id']}: {r['was']} -> {r['now']} (expected {r['expected']})")

    if not args.no_write:
        paths = write_run(run, runs_dir=args.runs_dir, history_path=args.history)
        print()
        print(f"  Report: {paths['markdown']}")

    if args.fail_on_regression and run.regressions:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
