"""Per-review-type accuracy harness over the shared ReviewEngine.

Point it at a manifest of labelled clips; it runs each through the shared engine
(one detector, reused) and reports, per review type, the numbers that tell you
objectively where the system is weakest:

    deliveries tested, ball detected %, analysis produced %, correct verdict %,
    average confidence, average processing time.

It works for EVERY review type because it drives the same ``ReviewEngine`` the
live path uses — so an accuracy number measured here is the number you get live.

Manifest (JSON)::

    {
      "clips": [
        {"path": "clips/d001.mp4", "review_type": "wide",  "expected_verdict": "WIDE",
         "camera_id": 0, "calibration_profile": 0},
        {"path": "clips/d002.mp4", "review_type": "noball", "expected_verdict": "NO BALL",
         "camera_id": 0, "calibration_profile": 0}
      ]
    }
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from core.review_engine import ReviewEngine, calibrators_for, frames_from_video, get_shared_detector

_ANALYSIS_FIELD = {
    "lbw": None,
    "wide": "wide_analysis",
    "noball": "no_ball_analysis", "no_ball": "no_ball_analysis", "front_foot": "no_ball_analysis",
    "runout": "run_out_analysis", "run_out": "run_out_analysis",
    "edge": "edge_analysis", "ultraedge": "edge_analysis",
}
_AWAITING = {"", "AWAITING", "INCONCLUSIVE", "--"}


def _norm(v: Any) -> str:
    return str(v or "").strip().upper().replace("_", " ")


def evaluate_clip(clip: dict, detector: Any = None, camera_roles: dict | None = None) -> dict:
    """Run one labelled clip through the shared engine and score it."""
    path = clip["path"]
    review_type = str(clip.get("review_type", "lbw")).lower()
    camera_id = int(clip.get("camera_id", 0))
    expected = _norm(clip.get("expected_verdict"))

    calibrators: dict[int, Any] = {}
    profile = clip.get("calibration_profile")
    if profile is not None:
        calibrators = {camera_id: calibrators_for([int(profile)]).get(int(profile))}
        calibrators = {k: v for k, v in calibrators.items() if v is not None}

    frames = frames_from_video(path, camera_id=camera_id, max_frames=clip.get("max_frames"))
    started = time.perf_counter()
    analysis = ReviewEngine.run(
        review_type,
        frames={camera_id: frames},
        detector=detector or get_shared_detector(),
        calibrators=calibrators,
        camera_roles=camera_roles or {},
        primary_camera_id=camera_id,
        timestamps={camera_id: [f.timestamp_ms for f in frames]},
    )
    elapsed = time.perf_counter() - started

    result = (analysis or {}).get("review_result", {}) if analysis else {}
    field = _ANALYSIS_FIELD.get(review_type)
    block = (analysis or {}).get(field) or {} if field else {}
    reason = block.get("reason") if isinstance(block, dict) else None
    verdict = _norm(result.get("verdict"))
    confidence = result.get("confidence")
    if confidence is None and analysis:
        confidence = analysis.get("overall_confidence")

    ball_detected = bool(frames) and not (reason and "no ball" in reason.lower())
    produced = verdict not in _AWAITING and confidence is not None
    scored = bool(produced and expected)
    correct = bool(scored and verdict == expected)

    return {
        "clip": Path(path).name, "review_type": review_type, "frames": len(frames),
        "expected": expected or None, "verdict": verdict or None,
        "confidence": round(confidence, 3) if confidence is not None else None,
        "ball_detected": ball_detected, "analysis_produced": produced,
        "scored": scored, "correct": correct, "processing_s": round(elapsed, 3),
        "reason": reason,
    }


def aggregate(rows: list[dict]) -> dict:
    """Fold per-clip rows into per-review-type accuracy statistics."""
    out: dict[str, dict] = {}
    for review_type in sorted({r["review_type"] for r in rows}):
        group = [r for r in rows if r["review_type"] == review_type]
        n = len(group)
        scored = [r for r in group if r["scored"]]
        detected = sum(r["ball_detected"] for r in group)
        produced = sum(r["analysis_produced"] for r in group)
        correct = sum(r["correct"] for r in group)
        confs = [r["confidence"] for r in group if r["confidence"] is not None]
        out[review_type] = {
            "deliveries_tested": n,
            "ball_detected": f"{detected}/{n}",
            "ball_detected_pct": round(100 * detected / n, 1) if n else None,
            "analysis_produced": f"{produced}/{n}",
            "analysis_pct": round(100 * produced / n, 1) if n else None,
            "correct_verdict": f"{correct}/{len(scored)}",
            "verdict_accuracy_pct": round(100 * correct / len(scored), 1) if scored else None,
            "avg_confidence_pct": round(100 * sum(confs) / len(confs), 1) if confs else None,
            "avg_processing_s": round(sum(r["processing_s"] for r in group) / n, 3) if n else None,
        }
    return out


def run_manifest(manifest_path: str | Path, detector: Any = None) -> dict:
    """Run every clip in a manifest through the shared engine (one detector, reused)."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    clips = data.get("clips", [])
    detector = detector or get_shared_detector()   # load the model ONCE for the whole set
    rows = [evaluate_clip(clip, detector=detector) for clip in clips]
    return {"clips": rows, "by_type": aggregate(rows), "total_clips": len(rows)}


def format_report(result: dict) -> str:
    lines = [f"Review accuracy report — {result.get('total_clips', 0)} deliveries", ""]
    for review_type, m in result["by_type"].items():
        pct = lambda v: "--" if v is None else f"{v}%"
        lines += [
            review_type.upper(),
            f"  Deliveries tested:   {m['deliveries_tested']}",
            f"  Ball detected:       {m['ball_detected']}  ({pct(m['ball_detected_pct'])})",
            f"  Analysis produced:   {m['analysis_produced']}  ({pct(m['analysis_pct'])})",
            f"  Correct verdict:     {m['correct_verdict']}  ({pct(m['verdict_accuracy_pct'])})",
            f"  Average confidence:  {pct(m['avg_confidence_pct'])}",
            f"  Avg processing time: {m['avg_processing_s']} s",
            "",
        ]
    return "\n".join(lines)
