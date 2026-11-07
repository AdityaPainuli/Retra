"""day_summary: live stats and timeline, and a reflection that carries the
stats it was written from, since one written before the day ended quotes
other numbers than the live stats."""

import asyncio
import json
import os
import sqlite3
from datetime import date, datetime, timedelta

from mcp import Client

from storage.database import Database
from storage.models import DailySummary, WindowEvent

# A fixed past day, so nothing depends on the real clock (the conftest
# fixture's own events sit around "now" and can straddle midnight).
DAY = date(2026, 11, 5)
T0 = datetime(2026, 11, 5, 10, 0)


def _db():
    # The MCP server opens the database read-only; the pipeline writes it.
    return Database(os.environ["RETRA_DB_PATH"])


def _events(*rows):
    """rows: (minute offset from T0, title, url)."""
    db = _db()
    for offset, title, url in rows:
        db.insert_event(WindowEvent(timestamp=T0 + timedelta(minutes=offset), app_name="Brave Browser",
                                    window_title=title, url=url, category="development"))


def _day(retra):
    async def go():
        async with Client(retra.mcp) as client:
            return json.loads((await client.call_tool("day_summary", {"day": DAY.isoformat()})).content[0].text)
    return asyncio.run(go())


def _reflect():
    """Write a reflection from the stats of the events present now."""
    db = _db()
    summary = db.compute_daily_summary(DAY)
    summary.set_reflection("Focus was great.")
    db.save_daily_summary(summary)
    return summary.headline_stats()


def test_timeline_merges_one_window_into_a_block(retra):
    _events(*[(m, "server.py - retra", None) for m in range(0, 30, 5)], (30, "Other", None))
    t = _day(retra)["timeline"]
    assert [(b["start"], b["title"]) for b in t] == [("10:00", "server.py - retra")]
    assert t[0]["minutes"] >= 25


def test_timeline_does_not_merge_windows_that_share_a_shortened_title(retra):
    long = "A" * 200
    _events((0, long + " one", None), (3, long + " two", None), (6, "Other", None))
    assert _day(retra)["timeline"] == [], "two 3-minute windows must not become one 6-minute block"


def test_no_daily_note_in_the_result(retra):
    assert "obsidian_note" not in _day(retra)


def test_reflection_with_stale_numbers_says_so(retra):
    _events(*[(m, "server.py - retra", None) for m in range(0, 30, 5)])
    then = _reflect()
    _events(*[(m, "later.py - retra", None) for m in range(60, 120, 5)], (120, "Other", None))

    d = _day(retra)
    assert d["ai_summary"] == "Focus was great."
    assert d["ai_summary_stats"] == then != d["stats"]
    assert "Quote 'stats'" in d["ai_summary_note"]


def test_reflection_with_current_numbers_has_no_note(retra):
    _events(*[(m, "server.py - retra", None) for m in range(0, 30, 5)], (30, "Other", None))
    _reflect()
    d = _day(retra)
    assert d["ai_summary_written_at"] and d["ai_summary_stats"] and d["ai_summary_note"] is None


def test_old_database_gets_reflection_time_and_stats(tmp_path):
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
        INSERT INTO daily_summaries (date, focus_score, app_switches, total_tracked_minutes, ai_summary, created_at)
            VALUES ('2026-11-05', 45, 88, 334, 'Reflection', '2026-11-05 17:30:11');
        INSERT INTO daily_summaries (date, created_at) VALUES ('2026-11-04', '2026-11-04 17:30:10');
    """)
    conn.close()

    Database(path)
    rows = {d: (at, stats) for d, at, stats in
            sqlite3.connect(path).execute("SELECT date, ai_summary_at, ai_summary_stats FROM daily_summaries")}
    assert rows["2026-11-04"] == (None, None)
    expected = sqlite3.connect(":memory:").execute("SELECT datetime('2026-11-05 17:30:11', 'localtime')").fetchone()[0]
    assert rows["2026-11-05"][0] == expected
    stats = json.loads(rows["2026-11-05"][1])
    assert (stats["focus_score"], stats["app_switches"], stats["tracked_minutes"]) == (45, 88, 334)
