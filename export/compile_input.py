"""
The wiki compile's input: one day's activity read from the database, each
item tagged with a stable `retra://` reference the wiki can cite.

  retra://session/<session_id>                       agent_sessions
  retra://span/<date>T<HH:MM>-<HH:MM>?app=<app>      a run of window events (app percent-encoded)
  retra://url/<id>                                   url_events (the newest row of a page)
  retra://screen/<id>                                screenshots.ocr_text
  retra://day/<date>                                 the day's stats

agent_messages ids are never cited: re-ingesting a session deletes and
re-inserts its messages. Blocked apps, URL patterns and folders are dropped
here with the MCP server's own rules, so they never reach the prompt.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from mcp_server.store import (
    MAX_RESULTS, build_spans, compact_url, get_db, is_private, minutes, project_breakdown,
    redact_text, session_is_private, short, timeline,
)

MAX_SPANS = 40
MAX_URLS = 30
MAX_SCREENS = 20
SCREEN_CHARS = 400
SESSION_PROMPTS = 5


@dataclass
class Record:
    ref: str
    kind: str  # session, span, url, screen, day
    text: str
    # Project names the record is attributed to, preferred first (sessions:
    # the repo's code-host name, then the folder's project; spans: the
    # attributed project). Lowercased.
    projects: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"[{self.ref}] {self.text}"


def day_records(day: date) -> list[Record]:
    """Every record of `day`, in a fixed order so an unchanged day always
    renders the same input."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    db = get_db()
    spans = build_spans(db.get_events_between(start, end), end_cap=min(end, datetime.now()))
    return [
        _day_record(db, day, spans),
        *_session_records(db, start, end),
        *_span_records(day, spans),
        *_url_records(db, start, end),
        *_screen_records(db, start, end),
    ]


def _day_record(db, day: date, spans) -> Record:
    s = db.compute_daily_summary(day)
    projects = ", ".join(f"{p['project']} {p['minutes']:g}m" for p in project_breakdown(spans, 12))
    text = (f"Day stats for {day.isoformat()}: tracked {s.total_tracked_minutes}m, focus {s.focus_minutes}m "
            f"(score {s.focus_score}), longest focus streak {s.longest_focus_streak_minutes}m, "
            f"communication {s.communication_minutes}m, browsing {s.browsing_minutes}m, "
            f"writing {s.writing_minutes}m, learning {s.learning_minutes}m, "
            f"entertainment {s.entertainment_minutes}m, app switches {s.app_switches}. "
            f"Projects: {projects or 'none'}.")
    return Record(f"retra://day/{day.isoformat()}", "day", text)


def _hm(stamp: str | None) -> str:
    return (stamp or "")[11:16]


def _when(stamp: str | None) -> str:
    return (stamp or "?")[:16].replace("T", " ")


def _session_records(db, start: datetime, end: datetime) -> list[Record]:
    try:
        rows = db.get_agent_sessions(start, end, MAX_RESULTS)
    except sqlite3.OperationalError:
        return []  # the daemon hasn't created the table yet
    out = []
    for r in sorted((r for r in rows if not session_is_private(r)), key=lambda r: (r["started_at"] or "", r["session_id"])):
        names = list(dict.fromkeys(n.lower() for n in (r.get("repo"), r["project"]) if n))
        where = ", ".join(f"{k} {v}" for k, v in (("project", r["project"]), ("repo", r.get("repo")),
                                                   ("branch", r["branch"]), ("folder", r["cwd"])) if v)
        parts = [f'Claude Code session "{redact_text(r["title"]) or "(untitled)"}" ({where}), '
                 f'{_when(r["started_at"])} to {_when(r["ended_at"])}, {r["prompt_count"] or 0} prompts.']
        prompts = [redact_text(short(p["text"], 300)) for p in db.get_agent_prompts(r["session_id"], SESSION_PROMPTS)]
        if prompts := [p for p in prompts if p]:
            parts.append("Prompts: " + " | ".join(prompts))
        if status := redact_text(r["last_status"] or short(r["last_reply"], 600)):
            parts.append(f"Where it stopped: {status}")
        files = [_relative(f, r["cwd"]) for f in json.loads(r["files"] or "[]")[:10]]
        if files:
            parts.append("Files: " + ", ".join(files))
        if prs := json.loads(r["prs"] or "[]"):
            parts.append("PRs: " + ", ".join(map(str, prs)))
        out.append(Record(f"retra://session/{r['session_id']}", "session", " ".join(parts), names))
    return out


def _relative(path: str, root: str | None) -> str:
    return path.removeprefix(root.rstrip("/") + "/") if root and path.startswith(root) else path


def _span_records(day: date, spans) -> list[Record]:
    blocks = timeline([s for s in spans if not s.private], limit=MAX_SPANS)
    out = []
    for b in blocks:
        ref = f"retra://span/{day.isoformat()}T{b['start']}-{b['end']}?app={quote(b['app'], safe='')}"
        project = f" (project {b['project']})" if b["project"] else ""
        out.append(Record(ref, "span", f'{b["app"]}: "{b["title"]}"{project}, {b["minutes"]:g} min',
                          [b["project"].lower()] if b["project"] else []))
    return out


def _url_records(db, start: datetime, end: datetime) -> list[Record]:
    with db._conn() as conn:
        rows = conn.execute("""
            SELECT id, url, domain, page_title, app_name, duration_seconds FROM url_events
            WHERE timestamp >= ? AND timestamp < ? ORDER BY id
        """, (start.isoformat(), end.isoformat())).fetchall()
    pages: dict[tuple, dict] = {}
    for r in rows:
        if is_private(r["app_name"], r["page_title"] or "", r["url"] or ""):
            continue
        p = pages.setdefault((r["domain"], r["page_title"] or ""), {"seconds": 0})
        p.update(id=r["id"], url=r["url"], app=r["app_name"], title=r["page_title"] or "")
        p["seconds"] += r["duration_seconds"] or 0
    top = sorted(pages.values(), key=lambda p: (-p["seconds"], p["id"]))[:MAX_URLS]
    return [Record(f"retra://url/{p['id']}", "url",
                   f'{compact_url(p["url"])} "{short(p["title"])}" ({p["app"]}), {minutes(p["seconds"]):g} min')
            for p in sorted(top, key=lambda p: p["id"])]


def _screen_records(db, start: datetime, end: datetime) -> list[Record]:
    if not db._has_column("screenshots", "ocr_text"):
        return []
    with db._conn() as conn:
        rows = conn.execute("""
            SELECT id, timestamp, app_name, window_title, ocr_text FROM screenshots
            WHERE timestamp >= ? AND timestamp < ? AND ocr_text IS NOT NULL AND ocr_text != ''
            ORDER BY id
        """, (start.isoformat(), end.isoformat())).fetchall()
    # The newest screenshot of each window; the most-seen windows first.
    latest: dict[tuple, sqlite3.Row] = {}
    seen: dict[tuple, int] = {}
    for r in rows:
        if is_private(r["app_name"], r["window_title"] or ""):
            continue
        key = (r["app_name"], r["window_title"] or "")
        latest[key] = r
        seen[key] = seen.get(key, 0) + 1
    keys = sorted(latest, key=lambda k: (-seen[k], latest[k]["id"]))[:MAX_SCREENS]
    out = []
    for r in sorted((latest[k] for k in keys), key=lambda r: r["id"]):
        text = re.sub(r"\s+", " ", redact_text(r["ocr_text"]) or "").strip()
        if not text:
            continue
        out.append(Record(f"retra://screen/{r['id']}", "screen",
                          f'Screen text, {r["app_name"]} "{short(r["window_title"])}" at {_hm(r["timestamp"])}: '
                          f'{short(text, SCREEN_CHARS)}'))
    return out
