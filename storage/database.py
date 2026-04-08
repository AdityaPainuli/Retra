"""
Retra SQLite Database
Schema, migrations, and query layer.
"""

import sqlite3
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from storage.models import WindowEvent, Session, Screenshot, DailySummary


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._persistent_conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_persistent_conn(self) -> sqlite3.Connection:
        """Return a long-lived connection for the daemon's hot path (inserts)."""
        if self._persistent_conn is None:
            self._persistent_conn = sqlite3.connect(self.db_path)
            self._persistent_conn.row_factory = sqlite3.Row
            self._persistent_conn.execute("PRAGMA journal_mode=WAL")
            self._persistent_conn.execute("PRAGMA foreign_keys=ON")
            self._persistent_conn.execute("PRAGMA synchronous=NORMAL")
        return self._persistent_conn

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Create tables if they don't exist."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS window_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    window_title TEXT DEFAULT '',
                    category TEXT NOT NULL,
                    bundle_id TEXT,
                    url TEXT,
                    is_idle INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    window_titles TEXT DEFAULT '[]',
                    duration_seconds INTEGER DEFAULT 0,
                    is_productive INTEGER,
                    tag TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS screenshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    app_name TEXT DEFAULT '',
                    window_title TEXT DEFAULT '',
                    thumbnail_path TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS daily_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE NOT NULL,
                    total_tracked_minutes INTEGER DEFAULT 0,
                    focus_minutes INTEGER DEFAULT 0,
                    communication_minutes INTEGER DEFAULT 0,
                    browsing_minutes INTEGER DEFAULT 0,
                    entertainment_minutes INTEGER DEFAULT 0,
                    writing_minutes INTEGER DEFAULT 0,
                    learning_minutes INTEGER DEFAULT 0,
                    other_minutes INTEGER DEFAULT 0,
                    idle_minutes INTEGER DEFAULT 0,
                    app_switches INTEGER DEFAULT 0,
                    longest_focus_streak_minutes INTEGER DEFAULT 0,
                    focus_score INTEGER DEFAULT 0,
                    ai_summary TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS daily_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    category TEXT NOT NULL,
                    target_minutes INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(date, category)
                );

                CREATE TABLE IF NOT EXISTS url_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    page_title TEXT DEFAULT '',
                    app_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    duration_seconds INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                -- Indexes for fast queries
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                    ON window_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_date
                    ON window_events(date(timestamp));
                CREATE INDEX IF NOT EXISTS idx_sessions_start
                    ON sessions(start_time);
                CREATE INDEX IF NOT EXISTS idx_screenshots_timestamp
                    ON screenshots(timestamp);
                CREATE INDEX IF NOT EXISTS idx_url_events_timestamp
                    ON url_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_url_events_domain
                    ON url_events(domain);
            """)

    # ── Window Events ─────────────────────────────────────────────

    def insert_event(self, event: WindowEvent) -> int:
        conn = self._get_persistent_conn()
        cursor = conn.execute("""
            INSERT INTO window_events (timestamp, app_name, window_title, category, bundle_id, url, is_idle)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            event.timestamp.isoformat(),
            event.app_name,
            event.window_title,
            event.category,
            event.bundle_id,
            event.url,
            int(event.is_idle),
        ))
        conn.commit()
        return cursor.lastrowid

    def get_events_for_date(self, target_date: date) -> list[WindowEvent]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM window_events
                WHERE date(timestamp) = ?
                ORDER BY timestamp ASC
            """, (target_date.isoformat(),)).fetchall()

        return [
            WindowEvent(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                app_name=r["app_name"],
                window_title=r["window_title"],
                category=r["category"],
                bundle_id=r["bundle_id"],
                url=r["url"],
                is_idle=bool(r["is_idle"]),
            )
            for r in rows
        ]

    def get_dates_with_events(self, start_date: date, end_date: date) -> list[str]:
        """Return list of date strings that have window events in the range."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT date(timestamp) as d FROM window_events
                WHERE date(timestamp) BETWEEN ? AND ?
                ORDER BY d ASC
            """, (start_date.isoformat(), end_date.isoformat())).fetchall()
        return [r["d"] for r in rows]

    # ── Sessions ──────────────────────────────────────────────────

    def aggregate_sessions(self, target_date: date, merge_threshold: int = 300) -> list[Session]:
        """
        Build sessions from raw window events.
        Events in the same app within `merge_threshold` seconds (default 5 min) get merged.
        For browsers, tracks unique domains visited within the session.
        """
        events = self.get_events_for_date(target_date)
        if not events:
            return []

        import re

        def _extract_domain(title: str, url: str = "") -> Optional[str]:
            """Extract domain from URL field or [domain] tag in title."""
            if url:
                try:
                    d = url.split("://", 1)[1].split("/")[0].split(":")[0]
                    return d[4:] if d.startswith("www.") else d
                except (IndexError, AttributeError):
                    pass
            # Check for [domain.com] in enriched titles
            m = re.search(r'\[([a-zA-Z0-9][-a-zA-Z0-9.]+\.[a-zA-Z]{2,})\]', title)
            if m:
                return m.group(1)
            return None

        def _clean_title(title: str) -> str:
            """Strip the [domain] suffix we added for storage."""
            return re.sub(r'\s*\[[a-zA-Z0-9][-a-zA-Z0-9.]+\.[a-zA-Z]{2,}\]\s*$', '', title).strip()

        sessions: list[Session] = []
        current: Optional[Session] = None

        for event in events:
            if event.is_idle:
                if current:
                    current.end_time = event.timestamp
                    current.duration_seconds = int(
                        (current.end_time - current.start_time).total_seconds()
                    )
                    sessions.append(current)
                    current = None
                sessions.append(Session(
                    start_time=event.timestamp,
                    end_time=event.timestamp,
                    app_name="Idle",
                    category="idle",
                    duration_seconds=0,
                ))
                continue

            # Extract domain for browser events
            domain = _extract_domain(event.window_title, event.url or "")
            clean_title = _clean_title(event.window_title) if event.window_title else ""

            if current is None:
                current = Session(
                    start_time=event.timestamp,
                    end_time=event.timestamp,
                    app_name=event.app_name,
                    category=event.category,
                    window_titles=[clean_title] if clean_title else [],
                    domains=[domain] if domain else [],
                )
            elif (
                event.app_name == current.app_name
                and (event.timestamp - current.end_time).total_seconds() <= merge_threshold
            ):
                # Merge into current session
                current.end_time = event.timestamp
                if clean_title and clean_title not in current.window_titles:
                    current.window_titles.append(clean_title)
                if domain and domain not in current.domains:
                    current.domains.append(domain)
            else:
                # Finish current session, start new one
                current.duration_seconds = int(
                    (current.end_time - current.start_time).total_seconds()
                )
                sessions.append(current)
                current = Session(
                    start_time=event.timestamp,
                    end_time=event.timestamp,
                    app_name=event.app_name,
                    category=event.category,
                    window_titles=[clean_title] if clean_title else [],
                    domains=[domain] if domain else [],
                )

        if current:
            # If viewing today and the last event is recent (< 5 min ago),
            # extend the session to "now" so the active app's time is accurate
            if target_date == date.today() and current.category != "idle":
                age = (datetime.now() - current.end_time).total_seconds()
                if age < 300:
                    current.end_time = datetime.now()
            current.duration_seconds = int(
                (current.end_time - current.start_time).total_seconds()
            )
            sessions.append(current)

        # Merge idle sessions — but cap at idle_threshold to avoid counting sleep
        idle_cap = 600  # 10 minutes max for idle sessions
        for i, s in enumerate(sessions):
            if s.category == "idle" and i + 1 < len(sessions):
                gap = int((sessions[i + 1].start_time - s.start_time).total_seconds())
                s.end_time = sessions[i + 1].start_time
                s.duration_seconds = min(gap, idle_cap)

        # Second pass: merge consecutive sessions of the same app,
        # but ONLY if the gap between them is small (< 10 min).
        # Large gaps (sleep, away) should NOT be merged.
        max_merge_gap = 600  # 10 minutes
        merged: list[Session] = []
        for s in sessions:
            if (
                merged
                and s.app_name == merged[-1].app_name
                and s.category != "idle"
                and merged[-1].category != "idle"
                and (s.start_time - merged[-1].end_time).total_seconds() <= max_merge_gap
            ):
                prev = merged[-1]
                prev.end_time = s.end_time
                prev.duration_seconds = int(
                    (prev.end_time - prev.start_time).total_seconds()
                )
                for t in s.window_titles:
                    if t and t not in prev.window_titles:
                        prev.window_titles.append(t)
                for d in s.domains:
                    if d and d not in prev.domains:
                        prev.domains.append(d)
            else:
                merged.append(s)

        # Drop idle sessions and tiny sessions from the result
        return [s for s in merged if s.duration_seconds >= 60 and s.category != "idle"]

    def save_sessions(self, sessions: list[Session]):
        with self._conn() as conn:
            for s in sessions:
                conn.execute("""
                    INSERT INTO sessions (start_time, end_time, app_name, category,
                                          window_titles, duration_seconds, is_productive, tag)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    s.start_time.isoformat(),
                    s.end_time.isoformat(),
                    s.app_name,
                    s.category,
                    json.dumps(s.window_titles),
                    s.duration_seconds,
                    None if s.is_productive is None else int(s.is_productive),
                    s.tag,
                ))

    def get_sessions_for_date(self, target_date: date) -> list[Session]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM sessions
                WHERE date(start_time) = ?
                ORDER BY start_time ASC
            """, (target_date.isoformat(),)).fetchall()

        return [
            Session(
                id=r["id"],
                start_time=datetime.fromisoformat(r["start_time"]),
                end_time=datetime.fromisoformat(r["end_time"]),
                app_name=r["app_name"],
                category=r["category"],
                window_titles=json.loads(r["window_titles"]),
                duration_seconds=r["duration_seconds"],
                is_productive=None if r["is_productive"] is None else bool(r["is_productive"]),
                tag=r["tag"],
            )
            for r in rows
        ]

    # ── Screenshots ───────────────────────────────────────────────

    def insert_screenshot(self, screenshot: Screenshot) -> int:
        conn = self._get_persistent_conn()
        cursor = conn.execute("""
            INSERT INTO screenshots (timestamp, filepath, app_name, window_title, thumbnail_path)
            VALUES (?, ?, ?, ?, ?)
        """, (
            screenshot.timestamp.isoformat(),
            screenshot.filepath,
            screenshot.app_name,
            screenshot.window_title,
            screenshot.thumbnail_path,
        ))
        conn.commit()
        return cursor.lastrowid

    def get_screenshots_for_date(self, target_date: date) -> list[Screenshot]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM screenshots
                WHERE date(timestamp) = ?
                ORDER BY timestamp ASC
            """, (target_date.isoformat(),)).fetchall()

        return [
            Screenshot(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                filepath=r["filepath"],
                app_name=r["app_name"],
                window_title=r["window_title"],
                thumbnail_path=r["thumbnail_path"],
            )
            for r in rows
        ]

    # ── Daily Summaries ───────────────────────────────────────────

    def compute_daily_summary(self, target_date: date) -> DailySummary:
        """Compute a full daily summary from raw events."""
        from config.settings import get_settings
        productive_cats = get_settings().categories.PRODUCTIVE_CATEGORIES

        events = self.get_events_for_date(target_date)
        sessions = self.aggregate_sessions(target_date)

        summary = DailySummary(date=target_date)

        # Count category minutes from sessions
        # focus_minutes = sum of all productive categories (coding + writing + learning)
        category_attr_map = {
            "communication": "communication_minutes",
            "browsing": "browsing_minutes",
            "entertainment": "entertainment_minutes",
            "idle": "idle_minutes",
            "other": "other_minutes",
        }

        for session in sessions:
            mins = session.duration_seconds // 60
            if session.category in productive_cats:
                # All productive categories contribute to focus_minutes
                summary.focus_minutes += mins
                # Also track the individual category for breakdown
                if session.category == "writing":
                    summary.writing_minutes += mins
                elif session.category == "learning":
                    summary.learning_minutes += mins
                # coding gets tracked only in focus_minutes (no separate attr needed)
            else:
                attr = category_attr_map.get(session.category, "other_minutes")
                setattr(summary, attr, getattr(summary, attr) + mins)
            summary.total_tracked_minutes += mins

        # Count context switches (only penalize switches between different categories,
        # not between apps doing the same type of work)
        if events:
            prev_cat = events[0].category
            for e in events[1:]:
                if e.category != prev_cat and not e.is_idle:
                    # Only count as a disruptive switch if moving between
                    # productive and non-productive work (or entertainment)
                    prev_productive = prev_cat in productive_cats
                    curr_productive = e.category in productive_cats
                    if prev_productive != curr_productive:
                        summary.app_switches += 1
                    prev_cat = e.category

        # Longest focus streak — consecutive productive sessions count together
        max_streak = 0
        current_streak = 0
        for session in sessions:
            if session.category in productive_cats or session.category == "communication":
                # Communication between productive sessions doesn't break the streak
                # (e.g. quick Slack reply while coding)
                current_streak += session.duration_seconds
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        summary.longest_focus_streak_minutes = max_streak // 60

        # Focus score (weighted formula)
        if summary.active_minutes > 0:
            focus_ratio = summary.focus_minutes / summary.active_minutes
            # Communication is "supportive" work — counts partially
            comm_ratio = summary.communication_minutes / summary.active_minutes
            productive_ratio = focus_ratio + (comm_ratio * 0.5)

            streak_bonus = min(summary.longest_focus_streak_minutes / 60, 1.0) * 15
            switch_penalty = min(summary.app_switches / 50, 1.0) * 10
            entertainment_penalty = min(summary.entertainment_minutes / summary.active_minutes, 0.3) * 15
            summary.focus_score = max(0, min(100, int(
                productive_ratio * 75 + streak_bonus - switch_penalty - entertainment_penalty
            )))

        summary.sessions = sessions

        # Load saved AI summary if one exists
        with self._conn() as conn:
            row = conn.execute(
                "SELECT ai_summary FROM daily_summaries WHERE date = ?",
                (target_date.isoformat(),)
            ).fetchone()
            if row and row["ai_summary"]:
                summary.ai_summary = row["ai_summary"]

        return summary

    def save_daily_summary(self, summary: DailySummary):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_summaries
                (date, total_tracked_minutes, focus_minutes, communication_minutes,
                 browsing_minutes, entertainment_minutes, writing_minutes, learning_minutes,
                 other_minutes, idle_minutes, app_switches, longest_focus_streak_minutes,
                 focus_score, ai_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                summary.date.isoformat(),
                summary.total_tracked_minutes,
                summary.focus_minutes,
                summary.communication_minutes,
                summary.browsing_minutes,
                summary.entertainment_minutes,
                summary.writing_minutes,
                summary.learning_minutes,
                summary.other_minutes,
                summary.idle_minutes,
                summary.app_switches,
                summary.longest_focus_streak_minutes,
                summary.focus_score,
                summary.ai_summary,
            ))

    def get_weekly_summaries(self, end_date: date, days: int = 7) -> list[DailySummary]:
        """Get daily summaries for the past N days."""
        start = end_date - timedelta(days=days - 1)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM daily_summaries
                WHERE date BETWEEN ? AND ?
                ORDER BY date ASC
            """, (start.isoformat(), end_date.isoformat())).fetchall()

        return [
            DailySummary(
                date=date.fromisoformat(r["date"]),
                total_tracked_minutes=r["total_tracked_minutes"],
                focus_minutes=r["focus_minutes"],
                communication_minutes=r["communication_minutes"],
                browsing_minutes=r["browsing_minutes"],
                entertainment_minutes=r["entertainment_minutes"],
                writing_minutes=r["writing_minutes"],
                learning_minutes=r["learning_minutes"],
                other_minutes=r["other_minutes"],
                idle_minutes=r["idle_minutes"],
                app_switches=r["app_switches"],
                longest_focus_streak_minutes=r["longest_focus_streak_minutes"],
                focus_score=r["focus_score"],
                ai_summary=r["ai_summary"],
            )
            for r in rows
        ]

    # ── Daily Goals ───────────────────────────────────────────────

    def set_goal(self, target_date: date, category: str, target_minutes: int):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO daily_goals (date, category, target_minutes)
                VALUES (?, ?, ?)
                ON CONFLICT(date, category) DO UPDATE SET target_minutes = excluded.target_minutes
            """, (target_date.isoformat(), category, target_minutes))

    def get_goals(self, target_date: date) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category, target_minutes FROM daily_goals WHERE date = ?",
                (target_date.isoformat(),)
            ).fetchall()
        return [{"category": r["category"], "target_minutes": r["target_minutes"]} for r in rows]

    def delete_goal(self, target_date: date, category: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM daily_goals WHERE date = ? AND category = ?",
                (target_date.isoformat(), category),
            )

    # ── URL Events ───────────────────────────────────────────────

    def insert_url_event(self, timestamp: datetime, url: str, domain: str,
                         page_title: str, app_name: str, category: str,
                         duration_seconds: int = 0):
        conn = self._get_persistent_conn()
        conn.execute("""
            INSERT INTO url_events (timestamp, url, domain, page_title, app_name, category, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (timestamp.isoformat(), url, domain, page_title, app_name, category, duration_seconds))
        conn.commit()

    def get_url_events_for_date(self, target_date: date) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM url_events
                WHERE date(timestamp) = ?
                ORDER BY timestamp ASC
            """, (target_date.isoformat(),)).fetchall()
        return [dict(r) for r in rows]

    def get_domain_stats(self, target_date: date) -> list[dict]:
        """Get aggregated time per domain for a date."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT domain, category,
                       COUNT(*) as visit_count,
                       SUM(duration_seconds) as total_seconds
                FROM url_events
                WHERE date(timestamp) = ?
                GROUP BY domain
                ORDER BY total_seconds DESC
            """, (target_date.isoformat(),)).fetchall()
        return [dict(r) for r in rows]

    def get_domain_stats_range(self, start_date: date, end_date: date) -> list[dict]:
        """Get aggregated domain stats for a date range."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT domain, category,
                       COUNT(*) as visit_count,
                       SUM(duration_seconds) as total_seconds
                FROM url_events
                WHERE date(timestamp) BETWEEN ? AND ?
                GROUP BY domain
                ORDER BY total_seconds DESC
            """, (start_date.isoformat(), end_date.isoformat())).fetchall()
        return [dict(r) for r in rows]

    # ── Heatmap ──────────────────────────────────────────────────

    def get_heatmap_data(self, days: int = 180) -> list[dict]:
        """Get focus_score per day for the heatmap."""
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT date, focus_score, total_tracked_minutes, focus_minutes
                FROM daily_summaries
                WHERE date >= ?
                ORDER BY date ASC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    # ── Comparison ───────────────────────────────────────────────

    def get_comparison(self, date_a: date, date_b: date) -> dict:
        """Return full summaries for two days for side-by-side comparison."""
        sum_a = self.compute_daily_summary(date_a)
        sum_b = self.compute_daily_summary(date_b)

        def summary_to_dict(s: DailySummary) -> dict:
            return {
                "date": s.date.isoformat(),
                "total_tracked_minutes": s.total_tracked_minutes,
                "focus_minutes": s.focus_minutes,
                "focus_percentage": s.focus_percentage,
                "focus_score": s.focus_score,
                "app_switches": s.app_switches,
                "longest_focus_streak_minutes": s.longest_focus_streak_minutes,
                "communication_minutes": s.communication_minutes,
                "browsing_minutes": s.browsing_minutes,
                "entertainment_minutes": s.entertainment_minutes,
                "writing_minutes": s.writing_minutes,
                "learning_minutes": s.learning_minutes,
                "categories": s.category_breakdown(),
            }

        return {
            "day_a": summary_to_dict(sum_a),
            "day_b": summary_to_dict(sum_b),
        }

    # ── Cleanup ───────────────────────────────────────────────────

    def cleanup_old_data(self, retention_days: int = 90):
        """Delete data older than retention period."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        with self._conn() as conn:
            conn.execute("DELETE FROM window_events WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM sessions WHERE start_time < ?", (cutoff,))
            conn.execute("DELETE FROM screenshots WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM url_events WHERE timestamp < ?", (cutoff,))
