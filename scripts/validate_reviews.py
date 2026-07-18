#!/usr/bin/env python
"""Run the per-review-type accuracy harness over a manifest of labelled clips.

    python scripts/validate_reviews.py data/testing/review_validation_set.json
    python scripts/validate_reviews.py <manifest> --json > report.json

The manifest lists clips with a review_type, expected_verdict and (optionally) a
calibration_profile. Every clip runs through the shared ReviewEngine — the same
engine the live 'Request Review' uses — so the accuracy you measure here is the
accuracy you get live.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.review_validation import format_report, run_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-review-type accuracy over the shared review engine")
    parser.add_argument("manifest", help="Path to a JSON manifest of labelled clips")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON result instead of the text report")
    args = parser.parse_args()

    result = run_manifest(args.manifest)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_report(result))


if __name__ == "__main__":
    main()
