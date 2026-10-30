"""
Read-only data access for the MCP server.

The capture daemon writes to the DB continuously, so everything here opens
SQLite with mode=ro and never runs the schema/migration step. Durations come
from gaps between consecutive window_events rows, using the same rules as the
attribution pass, so numbers line up with the dashboard's project view.
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

from config.settings import DB_PATH, get_settings
from export.attribution import attribute_projects, event_seconds
from storage.database import Database
from storage.models import WindowEvent

PRIVATE = "[private]"


class ReadOnlyDatabase(Database):
    """Database that opens SQLite read-only and skips schema creation."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._persistent_conn = None
        self._has_project_hint: Optional[bool] = None
        if not Path(self.db_path).exists():
            raise FileNotFoundError(f"Retra database not found at {self.db_path}")
        # as_uri() percent-encodes '#' and '?', which would otherwise end the
        # filename early and drop mode=ro.
        self._uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _get_persistent_conn(self):
        raise PermissionError("Retra MCP server is read-only")

    def get_events_between(self, start: datetime, end: datetime) -> list[WindowEvent]:
        with self._conn() as conn:
            # Migrations never run here, so pre-upgrade DBs may lack this column.
            if self._has_project_hint is None:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(window_events)")}
                self._has_project_hint = "project_hint" in cols
            hint_col = "project_hint" if self._has_project_hint else "NULL AS project_hint"
            rows = conn.execute(f"""
                SELECT id, timestamp, app_name, window_title, category,
                       bundle_id, url, is_idle, {hint_col}
                FROM window_events
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
            """, (start.isoformat(), end.isoformat())).fetchall()
        return [
            WindowEvent(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                app_name=r["app_name"],
                window_title=r["window_title"] or "",
                category=r["category"],
                bundle_id=r["bundle_id"],
                url=r["url"],
                is_idle=bool(r["is_idle"]),
                project_hint=r["project_hint"],
            )
            for r in rows
        ]

    def get_latest_event(self) -> Optional[WindowEvent]:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT timestamp, app_name, is_idle FROM window_events "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        if r is None:
            return None
        return WindowEvent(
            timestamp=datetime.fromisoformat(r["timestamp"]),
            app_name=r["app_name"], window_title="", category="",
            is_idle=bool(r["is_idle"]),
        )

    def search_url_events(self, pattern: str, start: datetime, end: datetime,
                          limit: int) -> list[dict]:
        """Matching pages grouped by domain + title, with private rows dropped
        before they are merged (grouping also by app so blocked apps can be
        filtered per row)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT domain, page_title, app_name, MAX(url) AS url,
                       SUM(duration_seconds) AS seconds,
                       MAX(timestamp) AS last_seen
                FROM url_events
                WHERE timestamp >= ? AND timestamp < ?
                  AND (url LIKE ? ESCAPE '\\' OR page_title LIKE ? ESCAPE '\\')
                GROUP BY domain, page_title, app_name
                ORDER BY last_seen DESC
                LIMIT ?
            """, (start.isoformat(), end.isoformat(), pattern, pattern, limit * 5)).fetchall()

        merged: dict[tuple, dict] = {}
        for r in rows:
            if is_private(r["app_name"], r["page_title"] or "", r["url"] or ""):
                continue
            m = merged.setdefault((r["domain"], r["page_title"]), {
                "domain": r["domain"], "page_title": r["page_title"], "url": r["url"],
                "seconds": 0, "last_seen": r["last_seen"],
            })
            m["seconds"] += r["seconds"] or 0
            m["last_seen"] = max(m["last_seen"], r["last_seen"])
        return sorted(merged.values(), key=lambda m: m["last_seen"], reverse=True)[:limit]

    def search_screen_text(self, match: str, start: datetime, end: datetime,
                           limit: int) -> list[dict]:
        """Full-text search over the (already redacted) text read from
        screenshots, best matches first."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT s.timestamp, s.app_name, s.window_title,
                       snippet(screen_text_fts, 0, '[', ']', '…', 24) AS snippet
                FROM screen_text_fts
                JOIN screenshots s ON s.id = screen_text_fts.rowid
                WHERE screen_text_fts MATCH ? AND s.timestamp >= ? AND s.timestamp < ?
                ORDER BY bm25(screen_text_fts)
                LIMIT ?
            """, (match, start.isoformat(), end.isoformat(), limit * 3)).fetchall()
        return [dict(r) for r in rows if not is_private(r["app_name"], r["window_title"] or "")][:limit]


def db_path() -> Path:
    return Path(os.environ.get("RETRA_DB_PATH") or DB_PATH)


_db: Optional[ReadOnlyDatabase] = None


def get_db() -> ReadOnlyDatabase:
    global _db
    if _db is None:
        _db = ReadOnlyDatabase(db_path())
    return _db


# ── Privacy ──────────────────────────────────────────────────────

def is_private(app: str, title: str = "", url: str = "") -> bool:
    privacy = get_settings().privacy
    if title == "[blocked]":
        return True
    app_l = (app or "").lower()
    if any(b.lower() in app_l for b in privacy.blocked_apps):
        return True
    hay = f"{title or ''} {url or ''}".lower()
    return any(p.lower() in hay for p in privacy.blocked_url_patterns)


def redact_text(text: Optional[str]) -> Optional[str]:
    """Drop lines of saved prose (AI reflections, notes, wiki pages) that
    mention a blocked app or URL pattern. These were written before the
    current blocklist may have existed, so they can't be filtered per event."""
    if not text:
        return text
    privacy = get_settings().privacy
    terms = [t.lower() for t in privacy.blocked_apps + privacy.blocked_url_patterns if t]
    terms.append("[blocked]")
    kept = []
    removed = 0
    for line in text.splitlines():
        if any(t in line.lower() for t in terms):
            removed += 1
        else:
            kept.append(line)
    if removed:
        kept.append(f"[{removed} line(s) hidden by privacy settings]")
    return "\n".join(kept)


# ── Formatting helpers ───────────────────────────────────────────

def compact_url(url: Optional[str], max_len: int = 100) -> Optional[str]:
    """Drop scheme, query and fragment (often carry tokens), truncate."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        # Never fall back to the raw string: query/fragment may hold tokens.
        stripped = url.split("?", 1)[0].split("#", 1)[0].split("://", 1)[-1]
        return stripped[:max_len] or None
    host = parts.netloc.removeprefix("www.")
    out = f"{host}{parts.path}".rstrip("/")
    return out[:max_len] if out else None


def short(text: Optional[str], max_len: int = 120) -> str:
    text = (text or "").strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def minutes(seconds: float) -> float:
    return round(seconds / 60, 1)


def parse_day(value: Optional[str]) -> date:
    if not value or value == "today":
        return date.today()
    if value == "yesterday":
        return date.today() - timedelta(days=1)
    return date.fromisoformat(value)


def parse_when(value: Optional[str], default: datetime) -> datetime:
    """Accept YYYY-MM-DD, full ISO datetimes, 'today', 'yesterday', or 'Nd'."""
    if not value:
        return default
    v = value.strip().lower()
    if v.endswith("d") and v[:-1].isdigit():
        return datetime.combine(date.today() - timedelta(days=int(v[:-1])), datetime.min.time())
    if v in ("today", "yesterday"):
        return datetime.combine(parse_day(v), datetime.min.time())
    if len(v) == 10:
        return datetime.combine(date.fromisoformat(v), datetime.min.time())
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is not None:
        # Captured timestamps are naive local time.
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


# ── Event spans ──────────────────────────────────────────────────

@dataclass
class Span:
    """One window_events row with the time it represents and its project."""
    ts: datetime
    seconds: int
    app: str
    title: str
    url: Optional[str]
    category: str
    project: Optional[str]
    private: bool


def build_spans(events: list[WindowEvent], end_cap: Optional[datetime] = None) -> list[Span]:
    """Turn raw events into duration-weighted spans (idle rows dropped).

    Each row's duration follows attribution.event_seconds. The final row's
    tail is clipped to `end_cap` (e.g. now).
    """
    private_ids = {e.id for e in events if is_private(e.app_name, e.window_title, e.url or "")}
    # Private events must not anchor attribution, or their project leaks onto
    # neighbouring public events. They still count for gap/duration accounting.
    attribution = attribute_projects(e for e in events if e.id not in private_ids)
    spans: list[Span] = []
    for i, e in enumerate(events):
        if e.is_idle:
            continue
        nxt = events[i + 1] if i + 1 < len(events) else None
        secs = event_seconds(e, nxt)
        if nxt is None and end_cap is not None:
            secs = int(max(0, min(secs, (end_cap - e.timestamp).total_seconds())))
        private = e.id in private_ids
        spans.append(Span(
            ts=e.timestamp,
            seconds=secs,
            app=PRIVATE if private else e.app_name,
            title="" if private else e.window_title,
            url=None if private else e.url,
            category=e.category,
            project=None if private else attribution.get(e.id),
            private=private,
        ))
    return spans


def load_spans(start: datetime, end: datetime) -> list[Span]:
    now = datetime.now()
    return build_spans(get_db().get_events_between(start, end), end_cap=min(end, now))


def filter_project(spans: Iterable[Span], project: Optional[str]) -> list[Span]:
    if not project:
        return list(spans)
    p = project.lower()
    return [s for s in spans if s.project and p in s.project.lower()]


# ── Aggregations ─────────────────────────────────────────────────

def top_by(spans: Iterable[Span], key, limit: int) -> list[tuple]:
    totals: dict = defaultdict(int)
    for s in spans:
        k = key(s)
        if k:
            totals[k] += s.seconds
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]


def project_breakdown(spans: list[Span], limit: int = 15) -> list[dict]:
    rows = top_by(spans, lambda s: s.project or "(unattributed)", limit)
    return [{"project": p, "minutes": minutes(secs)} for p, secs in rows]


def app_breakdown(spans: list[Span], limit: int = 10) -> list[dict]:
    return [{"app": a, "minutes": minutes(secs)} for a, secs in top_by(spans, lambda s: s.app, limit)]


def category_breakdown(spans: list[Span]) -> dict[str, float]:
    return {c: minutes(secs) for c, secs in top_by(spans, lambda s: s.category, 20)}


def label(s: Span) -> str:
    """Window title, or the page URL when the browser only gave '[domain]'."""
    title = s.title.strip()
    if s.url and (not title or (title.startswith("[") and title.endswith("]"))):
        return compact_url(s.url) or title
    return short(title)


def title_breakdown(spans: list[Span], limit: int = 15) -> list[dict]:
    rows = top_by(
        (s for s in spans if not s.private and (s.title or s.url)),
        lambda s: (s.app, label(s)),
        limit,
    )
    return [{"app": app, "title": title, "minutes": minutes(secs)} for (app, title), secs in rows]


def url_breakdown(spans: list[Span], limit: int = 10) -> list[dict]:
    rows = top_by((s for s in spans if s.url), lambda s: compact_url(s.url), limit)
    return [{"url": u, "minutes": minutes(secs)} for u, secs in rows]


def active_hours(spans: list[Span]) -> list[dict]:
    by_day: dict[date, list[Span]] = defaultdict(list)
    for s in spans:
        by_day[s.ts.date()].append(s)
    out = []
    for d in sorted(by_day, reverse=True):
        day = by_day[d]
        out.append({
            "date": d.isoformat(),
            "first": day[0].ts.strftime("%H:%M"),
            "last": day[-1].ts.strftime("%H:%M"),
            "active_minutes": minutes(sum(s.seconds for s in day)),
        })
    return out
