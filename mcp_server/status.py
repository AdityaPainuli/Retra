"""
How fresh Retra's data is, so agents can say where it stops instead of
treating old data as current.

Raw sources (activity, screen text, coding sessions) are only as fresh as the
capture daemon; the wiki and daily journal are compiled nightly, so they lag
raw activity by up to a day by design.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

from capture.health import _is_pid_alive
from capture.screen_text import redact
from config.settings import HEARTBEAT_PATH, PID_PATH
from export import pipeline_status
from mcp_server.store import db_path, get_db, redact_text

# Heartbeat is written every ~15s; 45s allows two missed beats (as capture/health.py).
HEARTBEAT_STALE_SEC = 45
# Compiles run nightly at 23:00, so >36h without a success means a missed night.
COMPILE_STALE_HOURS = 36

RAW_SOURCES = ("activity", "screen_text", "coding_sessions")
# Nightly pipeline stages, each with the name used in warnings. ai_summary can
# fail while the journal and wiki succeed, leaving day_summary without a reflection.
COMPILED_SOURCES = {"journal": "daily journal", "ai_summary": "AI reflection", "wiki": "wiki"}

_LATEST_SQL = {
    "activity": "SELECT MAX(timestamp) FROM window_events",
    "screen_text": "SELECT timestamp FROM screenshots WHERE ocr_text IS NOT NULL AND ocr_text != '' "
                   "ORDER BY timestamp DESC LIMIT 1",
    "coding_sessions": "SELECT MAX(ended_at) FROM agent_sessions",
}


def _fmt(ts: Optional[str]) -> Optional[str]:
    return ts[:16].replace("T", " ") if ts else None


def _latest(source: str) -> Optional[str]:
    try:
        with get_db()._conn() as conn:
            row = conn.execute(_LATEST_SQL[source]).fetchone()
    except (sqlite3.Error, OSError):
        # No database yet (daemon never ran) or a table the daemon hasn't
        # created yet: report no data rather than failing the whole status.
        return None
    return _fmt(row[0]) if row else None


def capture_state() -> dict:
    running, pid, beat, uptime = False, None, None, None
    try:
        pid = int(PID_PATH.read_text().strip())
        running = _is_pid_alive(pid)
    except (OSError, ValueError):
        pid = None
    try:
        data = json.loads(HEARTBEAT_PATH.read_text())
        beat = datetime.fromisoformat(data["timestamp"])
        uptime = data.get("uptime_seconds")
        if data.get("pid") and _is_pid_alive(data["pid"]):
            running, pid = True, data["pid"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    age = int((datetime.now() - beat).total_seconds()) if beat else None
    return {
        "recording": running and age is not None and age < HEARTBEAT_STALE_SEC,
        "daemon_running": running,
        "last_heartbeat": beat.strftime("%Y-%m-%d %H:%M:%S") if beat else None,
        "heartbeat_age_sec": age,
        "uptime_minutes": round(uptime / 60) if uptime else None,
    }


def _pipeline() -> dict:
    out = {}
    for stage, entry in pipeline_status.read().items():
        e = {"ok": entry.get("ok"), "last_run": _fmt(entry.get("last_run")),
             "last_success": _fmt(entry.get("last_success"))}
        if entry.get("error"):
            e["error"] = redact(redact_text(str(entry["error"])))[:300]
        out[stage] = e
    return out


def _compile_warning(source: str, entry: Optional[dict]) -> Optional[str]:
    source = COMPILED_SOURCES[source]
    if not entry or not entry.get("last_success"):
        return f"The {source} has never compiled successfully."
    if entry.get("ok") is False:
        return f"The last {source} compile failed ({entry.get('last_run')}); it's as of {entry['last_success']}."
    age = datetime.now() - datetime.fromisoformat(entry["last_success"].replace(" ", "T"))
    if age.total_seconds() > COMPILE_STALE_HOURS * 3600:
        return f"The {source} was last compiled {entry['last_success']}, so it misses activity since then."
    return None


def freshness(sources: tuple[str, ...]) -> dict:
    """Stamp for a tool result: when each source it draws on was last
    observed (raw) or compiled, plus warnings when data is stale."""
    out: dict = {}
    warnings = []
    raw = [s for s in sources if s in RAW_SOURCES]
    for s in raw:
        out[f"{s}_until"] = _latest(s)
    if raw:
        cap = capture_state()
        if not cap["recording"]:
            since = cap["last_heartbeat"] or "unknown"
            warnings.append(f"Capture isn't recording (last heartbeat {since}): data stops at the times above.")
    compiled = [s for s in sources if s in COMPILED_SOURCES]
    if compiled:
        pipe = _pipeline()
        for s in compiled:
            entry = pipe.get(s)
            out[f"{s}_compiled_at"] = entry.get("last_success") if entry else None
            if w := _compile_warning(s, entry):
                warnings.append(w)
    if warnings:
        out["warnings"] = warnings
    return out


def status() -> dict:
    cap = capture_state()
    sources = {s: _latest(s) for s in RAW_SOURCES}
    pipe = _pipeline()
    warnings = []
    if not cap["recording"]:
        warnings.append(f"Capture isn't recording (last heartbeat {cap['last_heartbeat'] or 'never'}). "
                        "Activity, screen text and coding sessions stop at the times in 'latest'.")
    for s in COMPILED_SOURCES:
        if w := _compile_warning(s, pipe.get(s)):
            warnings.append(w)
    try:
        size_mb = round(db_path().stat().st_size / 1_048_576, 1)
    except OSError:
        size_mb = None
    return {
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "capture": cap,
        "latest": sources,
        "pipeline": pipe,
        "db_size_mb": size_mb,
        "warnings": warnings,
    }
