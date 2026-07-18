"""Operator activity log — the audit trail every review workstation needs.

A single append-only stream of events (camera connect, calibration loaded, session
started, review requested, decision confirmed, replay exported, model promoted, …).
Kept in memory for instant `/api/activity` reads and mirrored to an ndjson file so it
survives restarts and can be attached to a diagnostic report.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from config.settings import DATA_DIR
from utils.logger import get_logger

log = get_logger("activity")

# Keep the tail in RAM for cheap reads; the ndjson file is the durable record.
_MAX_MEMORY_EVENTS = 500
_LOG_PATH = DATA_DIR / "logs" / "activity.ndjson"
_LOCK = threading.Lock()
_EVENTS: "deque[dict]" = deque(maxlen=_MAX_MEMORY_EVENTS)
_LOADED = False


def _load_tail() -> None:
    """Warm the in-memory ring from the ndjson tail once, so the log the operator
    sees after a restart is continuous rather than starting empty."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        if _LOG_PATH.exists():
            lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()[-_MAX_MEMORY_EVENTS:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    _EVENTS.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:  # never let a corrupt log break startup
        log.debug("Activity log tail unavailable: {}", exc)


def record(kind: str, message: str, **detail) -> dict:
    """Append one event. `kind` is a short machine slug (e.g. "camera_connected",
    "session_started", "decision_confirmed"); `message` is human-readable."""
    event = {
        "ts_ms": time.time() * 1000.0,
        "kind": str(kind),
        "message": str(message),
        "detail": {k: v for k, v in detail.items() if v is not None},
    }
    with _LOCK:
        _load_tail()
        _EVENTS.append(event)
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
        except Exception as exc:
            log.debug("Activity log append failed: {}", exc)
    return event


def recent(limit: int = 100) -> list[dict]:
    """Most recent events first."""
    with _LOCK:
        _load_tail()
        items = list(_EVENTS)
    items.reverse()
    if limit and limit > 0:
        items = items[:limit]
    return items


def clear() -> None:
    """Test helper — wipe the in-memory ring (does not truncate the file)."""
    with _LOCK:
        _EVENTS.clear()
