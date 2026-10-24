"""
Retra MCP server (stdio, read-only).

Lets Claude Code, Claude desktop and other local agents ask what the user is
working on and pull context from captured activity. No network, no writes.
"""

from __future__ import annotations

import functools
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from config.settings import get_settings
from mcp_server.store import (
    active_hours, app_breakdown, category_breakdown, compact_url, filter_project,
    get_db, is_private, label, load_spans, minutes, parse_day, parse_when, project_breakdown, short,
    title_breakdown, url_breakdown,
)

MAX_NOTE_CHARS = 8000
MAX_WIKI_CHARS = 12000
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

mcp = MCPServer(
    name="retra",
    instructions=(
        "Retra is the user's local activity tracker (apps, window titles, URLs, "
        "projects). Use current_activity for 'what am I doing now', recent_work "
        "or list_projects for 'what have I been working on', day_summary for a "
        "specific day, search_activity to find when something was worked on, and "
        "wiki_page for the long-term notes compiled from daily activity. Times "
        "are local; durations are in minutes."
    ),
)


def tool(fn):
    """Register a read-only tool that returns compact JSON text.

    The SDK's default is indented JSON plus a duplicate structured copy,
    which roughly doubles tokens for the same data.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> str:
        try:
            result = fn(*args, **kwargs)
        except (ValueError, FileNotFoundError) as e:
            result = {"error": str(e)}
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
    return mcp.tool(annotations=READ_ONLY, structured_output=False)(wrapper)


def _start_of(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time())


@tool
def current_activity(minutes_back: int = 30) -> dict:
    """What the user is doing right now, plus the last `minutes_back` minutes
    (default 30, max 240) grouped by project and app."""
    minutes_back = max(5, min(minutes_back, 240))
    now = datetime.now()
    # Pull a little extra history so context inheritance has anchors to use.
    spans = load_spans(now - timedelta(minutes=minutes_back + 15), now + timedelta(seconds=1))
    window_start = now - timedelta(minutes=minutes_back)
    recent = [s for s in spans if s.ts >= window_start]

    latest_event = get_db().get_latest_event()

    current = None
    if recent:
        s = recent[-1]
        current = {
            "since_last_capture_sec": int((now - s.ts).total_seconds()),
            "app": s.app,
            "title": short(s.title),
            "url": compact_url(s.url),
            "category": s.category,
            "project": s.project,
        }

    return {
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "status": (
            "idle" if latest_event and latest_event.is_idle
            else "active" if current and current["since_last_capture_sec"] < 300
            else "no recent activity (away, idle, or capture not running)"
        ),
        "current": current,
        "window_minutes": minutes_back,
        "active_minutes": minutes(sum(s.seconds for s in recent)),
        "projects": project_breakdown(recent, 8),
        "apps": app_breakdown(recent, 8),
        "titles": title_breakdown(recent, 10),
    }


@tool
def recent_work(days: int = 1, project: Optional[str] = None) -> dict:
    """Time per project, top window titles and URLs, and active hours for the
    last `days` days (1 = today, max 30). Optionally filter to one project
    (case-insensitive substring of the project name)."""
    days = max(1, min(days, 30))
    start = _start_of(date.today() - timedelta(days=days - 1))
    spans = filter_project(load_spans(start, datetime.now() + timedelta(seconds=1)), project)

    return {
        "range": f"{start.date().isoformat()} to {date.today().isoformat()}",
        "project_filter": project,
        "active_minutes": minutes(sum(s.seconds for s in spans)),
        "projects": project_breakdown(spans, 15),
        "categories": category_breakdown(spans),
        "apps": app_breakdown(spans, 10),
        "titles": title_breakdown(spans, 15),
        "urls": url_breakdown(spans, 10),
        "active_hours": active_hours(spans),
    }


@tool
def day_summary(day: str = "today") -> dict:
    """Stats for one day (YYYY-MM-DD, 'today' or 'yesterday'): focus score,
    category minutes, projects, top titles, the saved AI reflection if one
    exists, and the Obsidian daily note if present."""
    d = parse_day(day)
    summary = get_db().compute_daily_summary(d)
    spans = load_spans(_start_of(d), _start_of(d + timedelta(days=1)))

    note_path = get_settings().obsidian.output_dir / f"{d.isoformat()}.md"
    note = None
    if note_path.exists():
        text = note_path.read_text(errors="replace")
        note = text[:MAX_NOTE_CHARS] + ("\n…[truncated]" if len(text) > MAX_NOTE_CHARS else "")

    return {
        "date": d.isoformat(),
        "stats": {
            "focus_score": summary.focus_score,
            "tracked_minutes": summary.total_tracked_minutes,
            "focus_minutes": summary.focus_minutes,
            "communication_minutes": summary.communication_minutes,
            "browsing_minutes": summary.browsing_minutes,
            "entertainment_minutes": summary.entertainment_minutes,
            "writing_minutes": summary.writing_minutes,
            "learning_minutes": summary.learning_minutes,
            "idle_minutes": summary.idle_minutes,
            "longest_focus_streak_minutes": summary.longest_focus_streak_minutes,
            "app_switches": summary.app_switches,
        },
        "active_hours": active_hours(spans),
        "projects": project_breakdown(spans, 12),
        "apps": app_breakdown(spans, 10),
        "titles": title_breakdown(spans, 12),
        "ai_summary": summary.ai_summary,
        "obsidian_note": note,
    }


@tool
def search_activity(query: str, since: Optional[str] = None, until: Optional[str] = None,
                    limit: int = 20) -> dict:
    """Find when something was worked on: case-insensitive match over window
    titles, URLs and page titles. `since`/`until` take YYYY-MM-DD, ISO
    datetimes, 'today', 'yesterday' or 'Nd' (N days ago); default is the
    last 30 days. Returns hits grouped by day and window, newest first."""
    q = query.strip()
    if not q:
        return {"error": "query is empty"}
    limit = max(1, min(limit, 50))
    end = parse_when(until, datetime.now() + timedelta(seconds=1))
    if until and len(until.strip()) == 10:
        end += timedelta(days=1)  # bare date: include that whole day
    start = parse_when(since, _start_of(date.today() - timedelta(days=29)))

    ql = q.lower()
    spans = [
        s for s in load_spans(start, end)
        if not s.private and (ql in s.title.lower() or ql in (s.url or "").lower())
    ]

    groups: dict[tuple, dict] = {}
    by_day: dict[str, int] = defaultdict(int)
    for s in spans:
        day = s.ts.date().isoformat()
        by_day[day] += s.seconds
        key = (day, s.app, label(s))
        g = groups.setdefault(key, {
            "date": day, "first": s.ts, "last": s.ts, "seconds": 0,
            "app": s.app, "title": key[2], "project": s.project,
        })
        g["last"] = s.ts
        g["seconds"] += s.seconds

    hits = sorted(groups.values(), key=lambda g: g["last"], reverse=True)[:limit]
    for g in hits:
        g["first"] = g["first"].strftime("%H:%M")
        g["last"] = g["last"].strftime("%H:%M")
        g["minutes"] = minutes(g.pop("seconds"))

    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pages = [
        {"domain": p["domain"], "title": short(p["page_title"]), "url": compact_url(p["url"]),
         "minutes": minutes(p["seconds"] or 0), "last_seen": p["last_seen"][:16].replace("T", " ")}
        for p in get_db().search_url_events(f"%{escaped}%", start, end, limit)
        if not is_private("", p["page_title"] or "", p["url"] or "")
    ][:10]

    return {
        "query": q,
        "range": f"{start.date().isoformat()} to {end.date().isoformat()}",
        "total_minutes": minutes(sum(by_day.values())),
        "days": [{"date": d, "minutes": minutes(s)} for d, s in sorted(by_day.items(), reverse=True)][:30],
        "hits": hits,
        "pages": pages,
    }


@tool
def list_projects(days: int = 14) -> dict:
    """Projects detected in the last `days` days (max 90), with minutes,
    number of active days and when each was last seen."""
    days = max(1, min(days, 90))
    start = _start_of(date.today() - timedelta(days=days - 1))
    spans = load_spans(start, datetime.now() + timedelta(seconds=1))

    projects: dict[str, dict] = {}
    unattributed = 0
    for s in spans:
        if not s.project:
            unattributed += s.seconds
            continue
        p = projects.setdefault(s.project, {"seconds": 0, "days": set(), "last": s.ts})
        p["seconds"] += s.seconds
        p["days"].add(s.ts.date())
        p["last"] = max(p["last"], s.ts)

    rows = sorted(projects.items(), key=lambda kv: kv[1]["seconds"], reverse=True)[:40]
    return {
        "range": f"{start.date().isoformat()} to {date.today().isoformat()}",
        "projects": [
            {"project": name, "minutes": minutes(p["seconds"]), "active_days": len(p["days"]),
             "last_seen": p["last"].strftime("%Y-%m-%d %H:%M")}
            for name, p in rows
        ],
        "unattributed_minutes": minutes(unattributed),
    }


def _wiki_dir() -> Path:
    return get_settings().obsidian.resolved_vault_path / "retra-wiki"


@tool
def wiki_page(name: Optional[str] = None) -> dict:
    """Read a page from the retra-wiki folder in the Obsidian vault (long-term
    notes on projects, patterns, people, rollups). With no name, returns the
    index plus the list of pages. `name` can be a page title ('retra') or a
    relative path ('projects/retra')."""
    wiki = _wiki_dir().resolve()
    if not wiki.is_dir():
        return {"error": f"wiki folder not found: {wiki}"}

    pages = sorted(
        p.relative_to(wiki).with_suffix("").as_posix()
        for p in wiki.rglob("*.md")
        if not any(part.startswith(("_", ".")) for part in p.relative_to(wiki).parts)
    )

    if not name:
        index = wiki / "index.md"
        text = index.read_text(errors="replace")[:MAX_WIKI_CHARS] if index.exists() else None
        # The index already links every page; only list files when it's missing.
        return {"index": text} if text else {"pages": pages[:300]}

    wanted = name.strip().removesuffix(".md").lower()
    match = next((p for p in pages if p.lower() == wanted), None)
    if match is None:
        match = next((p for p in pages if p.rsplit("/", 1)[-1].lower() == wanted.rsplit("/", 1)[-1]), None)
    if match is None:
        similar = [p for p in pages if wanted.rsplit("/", 1)[-1] in p.lower()][:20]
        return {"error": f"no wiki page named '{name}'", "similar": similar}

    path = (wiki / f"{match}.md").resolve()
    if not path.is_relative_to(wiki):
        return {"error": "invalid page name"}
    text = path.read_text(errors="replace")
    return {
        "page": match,
        "content": text[:MAX_WIKI_CHARS] + ("\n…[truncated]" if len(text) > MAX_WIKI_CHARS else ""),
    }


def run():
    mcp.run("stdio")
