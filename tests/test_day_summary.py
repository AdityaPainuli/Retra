"""day_summary: live stats and timeline, and a reflection that says when it
was written, since one written before the day ended quotes other numbers."""

import asyncio
import json
import os
import sqlite3
from datetime import date, timedelta

from mcp import Client

from storage.database import Database
from storage.models import DailySummary


def _day(retra):
    async def go():
        async with Client(retra.mcp) as client:
            return json.loads((await client.call_tool("day_summary", {"day": "today"})).content[0].text)
    return asyncio.run(go())


def _save_reflection(at):
    # The MCP server opens the database read-only; the pipeline writes it.
    Database(os.environ["RETRA_DB_PATH"]).save_daily_summary(DailySummary(date=date.today(), ai_summary="Focus was 90.", ai_summary_at=at))


def test_timeline_merges_one_window_into_a_block(retra):
    t = _day(retra)["timeline"]
    assert len(t) == 1
    assert t[0]["app"] == "Code" and t[0]["title"] == "server.py - retra" and t[0]["project"] == "retra"
    assert t[0]["start"] == (retra.now - timedelta(minutes=30)).strftime("%H:%M")
    assert t[0]["minutes"] >= 25


def test_no_daily_note_in_the_result(retra):
    assert "obsidian_note" not in _day(retra)


def test_reflection_written_before_the_last_activity_says_so(retra):
    _save_reflection(retra.now - timedelta(minutes=20))
    d = _day(retra)
    assert d["ai_summary"] == "Focus was 90."
    assert d["ai_summary_written_at"] == (retra.now - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M")
    assert "'stats' covers the whole day" in d["ai_summary_note"]


def test_reflection_written_after_the_last_activity_has_no_note(retra):
    _save_reflection(retra.now)
    d = _day(retra)
    assert d["ai_summary_written_at"] and d["ai_summary_note"] is None


def test_old_database_gets_written_at_from_created_at(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE daily_summaries (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT UNIQUE NOT NULL,
            total_tracked_minutes INTEGER DEFAULT 0, focus_minutes INTEGER DEFAULT 0,
            communication_minutes INTEGER DEFAULT 0, browsing_minutes INTEGER DEFAULT 0,
            entertainment_minutes INTEGER DEFAULT 0, writing_minutes INTEGER DEFAULT 0,
            learning_minutes INTEGER DEFAULT 0, other_minutes INTEGER DEFAULT 0, idle_minutes INTEGER DEFAULT 0,
            app_switches INTEGER DEFAULT 0, longest_focus_streak_minutes INTEGER DEFAULT 0,
            focus_score INTEGER DEFAULT 0, ai_summary TEXT, created_at TEXT DEFAULT (datetime('now')));
        INSERT INTO daily_summaries (date, ai_summary, created_at) VALUES ('2026-11-05', 'Reflection', '2026-11-05 17:30:11');
        INSERT INTO daily_summaries (date, created_at) VALUES ('2026-11-04', '2026-11-04 17:30:10');
    """)
    conn.close()

    Database(path)
    rows = dict(sqlite3.connect(path).execute("SELECT date, ai_summary_at FROM daily_summaries"))
    assert rows["2026-11-04"] is None
    expected = sqlite3.connect(":memory:").execute("SELECT datetime('2026-11-05 17:30:11', 'localtime')").fetchone()[0]
    assert rows["2026-11-05"] == expected
