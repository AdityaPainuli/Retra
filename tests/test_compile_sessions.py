"""The nightly compile describes a coding session as it was on the day being
compiled: a session runs for weeks, and its latest state would hand an old
day a later decision (e.g. when catchup recompiles it)."""

import os
from datetime import date, datetime, time, timedelta

from export.compile_input import _session_records
from mcp_server import store
from storage.database import Database

D = date.today() - timedelta(days=3)
LATER = D + timedelta(days=2)


def save(db, sid, turns, ended):
    db.save_agent_session({
        "session_id": sid, "source": "claude-code", "cwd": "/work/retra", "project": "retra", "repo": None,
        "branch": "main", "title": "Launch", "started_at": turns[0][0], "ended_at": ended,
        "prompt_count": sum(k == "prompt" for _, k, _ in turns), "files": '["/work/retra/later.py"]',
        "prs": '["https://github.com/o/r/pull/30"]', "last_status": "Launch moved to Monday.",
        "last_reply": None, "log_path": f"/{sid}.jsonl", "log_size": 1, "log_mtime": 1.0,
    }, turns)


def records(day):
    start = datetime.combine(day, time.min)
    return {r.ref: r.text for r in _session_records(store.get_db(), start, start + timedelta(days=1))}


def test_a_long_session_is_described_as_it_was_that_day(retra):
    db = Database(os.environ["RETRA_DB_PATH"])
    save(db, "long", [(f"{D}T10:00:00", "prompt", "plan the launch"),
                      (f"{D}T10:05:00", "recap", "Launch set for Friday."),
                      (f"{LATER}T09:00:00", "prompt", "move the launch"),
                      (f"{LATER}T09:05:00", "recap", "Launch moved to Monday.")], f"{LATER}T09:05:00")
    text = records(D)["retra://session/long"]
    assert "Where it stopped: Launch set for Friday." in text and "plan the launch" in text
    assert "Monday" not in text and "move the launch" not in text and "1 prompts" in text
    assert "later.py" not in text and "pull/30" not in text, "whole-session files and PRs wait for its end"
    assert "retra://session/long" not in records(D + timedelta(days=1)), "no turns that day"
    assert "pull/30" in records(LATER)["retra://session/long"]


def test_reply_stands_in_when_a_day_has_no_recap(retra):
    db = Database(os.environ["RETRA_DB_PATH"])
    save(db, "chat", [(f"{D}T11:00:00", "prompt", "what's next?"),
                      (f"{D}T11:01:00", "reply", "Next: D3 wiki search.")], f"{D}T11:01:00")
    assert "Where it stopped: Next: D3 wiki search." in records(D)["retra://session/chat"]
