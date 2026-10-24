"""
Records the outcome of each nightly pipeline stage (journal, ai_summary, wiki)
to a small JSON file so the menubar and `status` can tell when the pipeline
is failing instead of it failing silently in a log nobody reads.
"""

import json
from datetime import datetime
from typing import Optional

from config.settings import PIPELINE_STATUS_PATH

_last_error: Optional[str] = None


def note_error(message: str):
    """Remember the most recent error so the next failed record() can show it."""
    global _last_error
    _last_error = message


def record(stage: str, ok: bool, detail: str = ""):
    global _last_error
    status = read()
    entry = status.get(stage, {})
    now = datetime.now().isoformat(timespec="seconds")
    entry["ok"] = ok
    entry["last_run"] = now
    if ok:
        entry["last_success"] = now
        entry.pop("error", None)
    else:
        entry["error"] = detail or _last_error or "unknown error"
    _last_error = None
    status[stage] = entry
    try:
        PIPELINE_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PIPELINE_STATUS_PATH.write_text(json.dumps(status, indent=2))
    except OSError as e:
        print(f"[Retra] Could not write pipeline status: {e}")


def read() -> dict:
    try:
        return json.loads(PIPELINE_STATUS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
