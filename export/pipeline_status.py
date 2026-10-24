"""
Records the outcome of each nightly pipeline stage (journal, ai_summary, wiki)
to a small JSON file so the menubar and `status` can tell when the pipeline
is failing instead of it failing silently in a log nobody reads.
"""

import fcntl
import json
import os
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
    error = detail or _last_error or "unknown error"
    _last_error = None
    now = datetime.now().isoformat(timespec="seconds")
    try:
        PIPELINE_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # The journal job, dashboard and menubar all write this file, so the
        # read/update/write runs under an exclusive lock, and the new JSON is
        # swapped in atomically so readers never see a half-written file.
        lock_path = PIPELINE_STATUS_PATH.with_suffix(".lock")
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            status = read()
            entry = status.get(stage, {})
            entry["ok"] = ok
            entry["last_run"] = now
            if ok:
                entry["last_success"] = now
                entry.pop("error", None)
            else:
                entry["error"] = error
            status[stage] = entry
            tmp = PIPELINE_STATUS_PATH.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(status, indent=2))
            os.replace(tmp, PIPELINE_STATUS_PATH)
    except OSError as e:
        print(f"[Retra] Could not write pipeline status: {e}")


def read() -> dict:
    try:
        return json.loads(PIPELINE_STATUS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
