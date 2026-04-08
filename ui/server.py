"""
Retra Dashboard API
FastAPI server that serves activity data to the React dashboard.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from storage.database import Database
from storage.models import DailySummary
from export.obsidian import export_to_obsidian
from export.summarizer import generate_summary
from config.settings import get_settings, DB_PATH, SCREENSHOTS_DIR

app = FastAPI(title="Retra", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = Database(DB_PATH)


# ── Day Overview ──────────────────────────────────────────────────

@app.get("/api/day/{date_str}")
async def get_day(date_str: str):
    """Get full day overview: summary + sessions + screenshots."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    summary = db.compute_daily_summary(target)
    sessions = db.aggregate_sessions(target)
    screenshots = db.get_screenshots_for_date(target)

    # Merge saved tags from sessions table into computed sessions
    saved = db.get_sessions_for_date(target)
    saved_tags = {
        (s.start_time.isoformat(), s.app_name): (s.is_productive, s.tag)
        for s in saved
    }
    for s in sessions:
        key = (s.start_time.isoformat(), s.app_name)
        if key in saved_tags:
            s.is_productive, s.tag = saved_tags[key]

    # Detect projects from window titles
    from export.summarizer import detect_projects
    projects = detect_projects(summary)

    # Group sessions by app — aggregate all Cursor sessions into one entry, etc.
    from collections import OrderedDict
    grouped: dict[str, dict] = OrderedDict()
    for s in sessions:
        key = s.app_name
        if key not in grouped:
            grouped[key] = {
                "app_name": s.app_name,
                "category": s.category,
                "duration_seconds": 0,
                "window_titles": [],
                "domains": [],
                "start_time": s.start_time,
                "end_time": s.end_time,
                "is_productive": s.is_productive,
                "tag": s.tag,
            }
        g = grouped[key]
        g["duration_seconds"] += s.duration_seconds
        g["start_time"] = min(g["start_time"], s.start_time)
        g["end_time"] = max(g["end_time"], s.end_time)
        for t in s.window_titles:
            if t and t not in g["window_titles"]:
                g["window_titles"].append(t)
        for d in s.domains:
            if d and d not in g["domains"]:
                g["domains"].append(d)
        # Keep productive/tag if any sub-session has it
        if s.is_productive is not None:
            g["is_productive"] = s.is_productive
        if s.tag:
            g["tag"] = s.tag

    # Sort by total duration descending
    grouped_list = sorted(grouped.values(), key=lambda g: -g["duration_seconds"])

    # Format duration display
    def _fmt_dur(secs):
        h, m = divmod(secs // 60, 60)
        return f"{h}h {m}m" if h > 0 and m > 0 else (f"{h}h" if h > 0 else f"{m}m")

    return {
        "date": date_str,
        "summary": {
            "total_tracked_minutes": summary.total_tracked_minutes,
            "focus_minutes": summary.focus_minutes,
            "focus_percentage": summary.focus_percentage,
            "focus_score": summary.focus_score,
            "app_switches": summary.app_switches,
            "longest_focus_streak_minutes": summary.longest_focus_streak_minutes,
            "categories": summary.category_breakdown(),
            "ai_summary": summary.ai_summary,
            "projects": [
                {"name": name, "minutes": mins}
                for name, mins in sorted(projects.items(), key=lambda x: -x[1])
            ],
        },
        "sessions": [
            {
                "id": i,
                "start_time": g["start_time"].isoformat(),
                "end_time": g["end_time"].isoformat(),
                "app_name": g["app_name"],
                "category": g["category"],
                "duration_seconds": g["duration_seconds"],
                "duration_display": _fmt_dur(g["duration_seconds"]),
                "window_titles": g["window_titles"][:10],
                "domains": g["domains"][:10],
                "is_productive": g["is_productive"],
                "tag": g["tag"],
            }
            for i, g in enumerate(grouped_list)
        ],
        "screenshots": [
            {
                "id": sc.id,
                "timestamp": sc.timestamp.isoformat(),
                "filepath": sc.filepath,
                "app_name": sc.app_name,
                "window_title": sc.window_title,
            }
            for sc in screenshots
        ],
    }


@app.get("/api/today")
async def get_today():
    """Shortcut for today's data."""
    return await get_day(date.today().isoformat())


# ── Timeline ─────────────────────────────────────────────────────

@app.get("/api/timeline/{date_str}")
async def get_timeline(date_str: str):
    """Get timeline blocks for the day (for the timeline visualization)."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    events = db.get_events_for_date(target)

    # Group events into blocks
    blocks = []
    current_block = None

    for event in events:
        if current_block is None or event.app_name != current_block["app_name"]:
            if current_block:
                current_block["end"] = event.timestamp.strftime("%H:%M")
                blocks.append(current_block)
            current_block = {
                "start": event.timestamp.strftime("%H:%M"),
                "end": event.timestamp.strftime("%H:%M"),
                "app_name": event.app_name,
                "category": event.category,
                "title": event.window_title,
            }
        else:
            current_block["end"] = event.timestamp.strftime("%H:%M")

    if current_block:
        blocks.append(current_block)

    return {"date": date_str, "blocks": blocks}


# ── Weekly ────────────────────────────────────────────────────────

@app.get("/api/week")
async def get_week(end_date: str = Query(default=None)):
    """Get weekly overview (last 7 days)."""
    target = date.fromisoformat(end_date) if end_date else date.today()
    start = target - timedelta(days=6)

    # Get saved summaries from the database
    saved = db.get_weekly_summaries(target, days=7)
    saved_by_date = {s.date: s for s in saved}

    # For each day in the range, use saved summary or compute from raw events
    summaries = []
    for i in range(7):
        d = start + timedelta(days=i)
        if d in saved_by_date:
            summaries.append(saved_by_date[d])
        else:
            computed = db.compute_daily_summary(d)
            if computed.total_tracked_minutes > 0:
                summaries.append(computed)

    return {
        "start_date": start.isoformat(),
        "end_date": target.isoformat(),
        "days": [
            {
                "date": s.date.isoformat(),
                "day_name": s.date.strftime("%a"),
                "focus_hours": round(s.focus_minutes / 60, 1),
                "total_hours": round(s.total_tracked_minutes / 60, 1),
                "focus_score": s.focus_score,
            }
            for s in summaries
        ],
        "avg_focus_score": (
            round(sum(s.focus_score for s in summaries) / len(summaries))
            if summaries else 0
        ),
        "total_focus_hours": round(sum(s.focus_minutes for s in summaries) / 60, 1),
    }


# ── AI Summary ────────────────────────────────────────────────────

@app.post("/api/summarize/{date_str}")
async def create_summary(date_str: str):
    """Generate AI summary for a given date."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    summary = db.compute_daily_summary(target)
    if summary.total_tracked_minutes < 30:
        raise HTTPException(400, "Not enough data to generate summary.")

    ai_text = generate_summary(summary)
    if ai_text:
        summary.ai_summary = ai_text
        db.save_daily_summary(summary)
        return {"summary": ai_text}
    else:
        raise HTTPException(500, "Failed to generate AI summary.")


# ── Obsidian Export ───────────────────────────────────────────────

@app.post("/api/export/{date_str}")
async def export_journal(date_str: str):
    """Export daily note to Obsidian vault."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    filepath = export_to_obsidian(target)
    return {"exported": True, "path": str(filepath)}


# ── Wiki Compilation ──────────────────────────────────────────────

@app.post("/api/compile/{date_str}")
async def compile_wiki(date_str: str):
    """Compile a daily note into the wiki."""
    from export.wiki_compiler import compile_daily_note
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    try:
        compile_daily_note(target)
        return {"compiled": True, "date": date_str}
    except Exception as e:
        raise HTTPException(500, f"Wiki compilation failed: {e}")


@app.post("/api/compile-week")
async def compile_week_endpoint():
    """Generate a weekly rollup."""
    from export.wiki_compiler import compile_week
    try:
        compile_week()
        return {"compiled": True}
    except Exception as e:
        raise HTTPException(500, f"Weekly rollup failed: {e}")


@app.post("/api/ask")
async def ask_wiki(body: dict):
    """Ask a question against the wiki."""
    from export.wiki_compiler import query_wiki
    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(400, "No question provided.")

    # Capture the printed output
    import io
    import contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        query_wiki(question)
    answer = f.getvalue().strip()

    return {"answer": answer}


@app.get("/api/wiki/compile-status")
async def wiki_compile_status():
    """Check wiki compilation status — which dates have been compiled."""
    import re
    settings = get_settings()
    wiki_dir = settings.obsidian.resolved_vault_path / "retra-wiki"
    log_path = wiki_dir / "log.md"

    if not log_path.exists():
        return {"initialized": False, "compiled_dates": [], "last_compiled": None}

    log_content = log_path.read_text(encoding="utf-8")

    # Extract dates from log entries like "## [2026-04-06] ingest | Daily note for 2026-04-06"
    compiled_dates = re.findall(r'\[(\d{4}-\d{2}-\d{2})\] ingest', log_content)
    today = date.today().isoformat()

    return {
        "initialized": True,
        "compiled_dates": compiled_dates,
        "last_compiled": compiled_dates[-1] if compiled_dates else None,
        "today_compiled": today in compiled_dates,
    }


@app.get("/api/wiki/focus-trends")
async def wiki_focus_trends():
    """Parse focus-trends.md and return structured data for charting."""
    import re
    settings = get_settings()
    wiki_dir = settings.obsidian.resolved_vault_path / "retra-wiki"
    trends_path = wiki_dir / "patterns" / "focus-trends.md"

    if not trends_path.exists():
        return {"trends": []}

    content = trends_path.read_text(encoding="utf-8")

    # Parse markdown table rows: | 2026-04-06 | 14/100 | 1h 28m | 5h 26m | 18m |
    trends = []
    for match in re.finditer(
        r'\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d+)/100\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|',
        content,
    ):
        date_str, score, deep_work, total, streak = match.groups()
        trends.append({
            "date": date_str,
            "score": int(score),
            "deep_work": deep_work.strip(),
            "total_tracked": total.strip(),
            "longest_streak": streak.strip(),
        })

    return {"trends": trends}


@app.get("/api/wiki/search")
async def wiki_search(q: str = Query(..., min_length=1)):
    """Full-text search across all wiki pages."""
    settings = get_settings()
    wiki_dir = settings.obsidian.resolved_vault_path / "retra-wiki"

    if not wiki_dir.exists():
        return {"results": []}

    query_lower = q.lower()
    results = []

    for subdir in ["projects", "patterns", "learning", "people", "rollups", "insights"]:
        dir_path = wiki_dir / subdir
        if not dir_path.exists():
            continue
        for f in dir_path.glob("*.md"):
            content = f.read_text(encoding="utf-8")
            if query_lower in content.lower():
                # Extract matching lines for context
                matches = []
                for i, line in enumerate(content.split("\n")):
                    if query_lower in line.lower():
                        matches.append(line.strip())
                        if len(matches) >= 3:
                            break
                results.append({
                    "path": f"{subdir}/{f.name}",
                    "title": f.stem.replace("-", " ").title(),
                    "type": subdir.rstrip("s"),
                    "matches": matches,
                })

    # Also search index and log
    for fname in ["index.md", "log.md"]:
        fp = wiki_dir / fname
        if fp.exists() and query_lower in fp.read_text(encoding="utf-8").lower():
            results.append({
                "path": fname,
                "title": fname.replace(".md", "").title(),
                "type": "meta",
                "matches": [],
            })

    return {"results": results, "query": q}


@app.get("/api/wiki/page/{path:path}")
async def wiki_get_page(path: str):
    """Read a single wiki page by relative path."""
    settings = get_settings()
    wiki_dir = settings.obsidian.resolved_vault_path / "retra-wiki"
    file_path = wiki_dir / path

    # Prevent path traversal
    try:
        file_path.resolve().relative_to(wiki_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied.")

    if not file_path.exists() or not file_path.suffix == ".md":
        raise HTTPException(404, "Page not found.")

    content = file_path.read_text(encoding="utf-8")
    return {"path": path, "content": content}


@app.get("/api/wiki/index")
async def wiki_index():
    """Return the wiki index with all pages and their metadata."""
    settings = get_settings()
    wiki_dir = settings.obsidian.resolved_vault_path / "retra-wiki"

    if not wiki_dir.exists():
        return {"initialized": False, "pages": []}

    pages = []
    for subdir in ["projects", "patterns", "learning", "people", "rollups", "insights"]:
        dir_path = wiki_dir / subdir
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.glob("*.md")):
            pages.append({
                "path": f"{subdir}/{f.name}",
                "title": f.stem.replace("-", " ").title(),
                "type": subdir.rstrip("s"),
            })

    return {"initialized": True, "pages": pages}


# ── Screenshots ───────────────────────────────────────────────────

@app.get("/api/screenshot/{screenshot_id}")
async def get_screenshot(screenshot_id: int):
    """Serve a screenshot image."""
    with db._conn() as conn:
        row = conn.execute(
            "SELECT filepath FROM screenshots WHERE id = ?", (screenshot_id,)
        ).fetchone()

    if not row:
        raise HTTPException(404, "Screenshot not found.")

    filepath = Path(row["filepath"])
    if not filepath.exists():
        raise HTTPException(404, "Screenshot file missing.")

    return FileResponse(filepath, media_type="image/jpeg")


# ── Tagging ───────────────────────────────────────────────────────

@app.post("/api/sessions/tag")
async def tag_session(
    start_time: str = Query(...),
    app_name: str = Query(...),
    productive: bool = None,
    tag: str = None,
):
    """Tag a session as productive/distraction or add a custom tag.
    Identifies sessions by start_time + app_name (unique key for computed sessions)."""
    with db._conn() as conn:
        # Check if this session is already saved
        row = conn.execute(
            "SELECT id FROM sessions WHERE start_time = ? AND app_name = ?",
            (start_time, app_name),
        ).fetchone()

        if row:
            # Update existing
            if productive is not None:
                conn.execute(
                    "UPDATE sessions SET is_productive = ? WHERE id = ?",
                    (int(productive), row["id"]),
                )
            if tag is not None:
                conn.execute(
                    "UPDATE sessions SET tag = ? WHERE id = ?",
                    (tag, row["id"]),
                )
        else:
            # Insert a new row to persist the tag
            conn.execute("""
                INSERT INTO sessions (start_time, end_time, app_name, category,
                                      window_titles, duration_seconds, is_productive, tag)
                VALUES (?, ?, ?, '', '[]', 0, ?, ?)
            """, (
                start_time, start_time, app_name,
                int(productive) if productive is not None else None,
                tag,
            ))

    return {"updated": True}


# Keep old endpoint for backwards compatibility
@app.post("/api/sessions/{session_id}/tag")
async def tag_session_by_id(
    session_id: int,
    start_time: str = Query(...),
    app_name: str = Query(...),
    productive: bool = None,
    tag: str = None,
):
    """Backwards-compatible tag endpoint — redirects to start_time based tagging."""
    return await tag_session(
        start_time=start_time, app_name=app_name,
        productive=productive, tag=tag,
    )


# ── Daily Goals ──────────────────────────────────────────────────

@app.get("/api/goals/{date_str}")
async def get_goals(date_str: str):
    """Get daily goals and current progress for a date."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    goals = db.get_goals(target)
    summary = db.compute_daily_summary(target)

    # Map goal category names to actual minutes from the summary
    actual_map = {
        "deep_work": summary.focus_minutes,
        "communication": summary.communication_minutes,
        "browsing": summary.browsing_minutes,
        "entertainment": summary.entertainment_minutes,
        "writing": summary.writing_minutes,
        "learning": summary.learning_minutes,
    }

    return {
        "date": date_str,
        "goals": [
            {
                "category": g["category"],
                "target_minutes": g["target_minutes"],
                "actual_minutes": actual_map.get(g["category"], 0),
                "progress": min(100, round(
                    (actual_map.get(g["category"], 0) / g["target_minutes"]) * 100
                )) if g["target_minutes"] > 0 else 0,
            }
            for g in goals
        ],
    }


@app.post("/api/goals/{date_str}")
async def set_goal(date_str: str, category: str = Query(...), target_minutes: int = Query(...)):
    """Set a daily goal for a category."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    valid_categories = ["deep_work", "communication", "browsing", "entertainment", "writing", "learning"]
    if category not in valid_categories:
        raise HTTPException(400, f"Invalid category. Must be one of: {valid_categories}")
    if target_minutes < 0:
        raise HTTPException(400, "target_minutes must be >= 0")

    db.set_goal(target, category, target_minutes)
    return {"saved": True}


@app.delete("/api/goals/{date_str}")
async def delete_goal(date_str: str, category: str = Query(...)):
    """Delete a daily goal."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")
    db.delete_goal(target, category)
    return {"deleted": True}


# ── Comparison ───────────────────────────────────────────────────

@app.get("/api/compare")
async def compare_days(date_a: str = Query(...), date_b: str = Query(...)):
    """Compare two days side by side."""
    try:
        da = date.fromisoformat(date_a)
        db_ = date.fromisoformat(date_b)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    return db.get_comparison(da, db_)


# ── Heatmap ──────────────────────────────────────────────────────

@app.get("/api/heatmap")
async def get_heatmap(days: int = Query(default=180)):
    """Get focus score per day for heatmap visualization."""
    today = date.today()
    start = today - timedelta(days=days - 1)

    saved = db.get_heatmap_data(days)
    saved_dates = {e["date"] for e in saved}

    # Find days with raw events but no saved summary
    event_dates = db.get_dates_with_events(start, today)
    missing_dates = [d for d in event_dates if d not in saved_dates]

    # Compute summaries only for missing days that have events
    computed = []
    for d_str in missing_dates:
        d = date.fromisoformat(d_str)
        summary = db.compute_daily_summary(d)
        if summary.total_tracked_minutes > 0:
            computed.append({
                "date": d_str,
                "focus_score": summary.focus_score,
                "total_tracked_minutes": summary.total_tracked_minutes,
                "focus_minutes": summary.focus_minutes,
            })

    all_days = saved + computed
    all_days.sort(key=lambda e: e["date"])
    return {"days": all_days}


# ── URL Tracking ─────────────────────────────────────────────────

@app.get("/api/urls/{date_str}")
async def get_url_stats(date_str: str):
    """Get rich URL analytics for a date."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")

    settings = get_settings()
    domains = db.get_domain_stats(target)
    events = db.get_url_events_for_date(target)

    # Re-categorize domains using settings (the daemon may have stored generic "browsing")
    for d in domains:
        better_cat = settings.categories.categorize_domain(d["domain"])
        if better_cat:
            d["category"] = better_cat

    for e in events:
        better_cat = settings.categories.categorize_domain(e.get("domain", ""))
        if better_cat:
            e["category"] = better_cat

    # -- Compute deeper analytics --

    # 1. Total browser time
    total_seconds = sum(d.get("total_seconds", 0) for d in domains)
    total_visits = sum(d.get("visit_count", 0) for d in domains)

    # 2. Category breakdown (with proper categorization)
    cat_time: dict[str, int] = {}
    cat_domains: dict[str, list[str]] = {}
    for d in domains:
        cat = d.get("category", "browsing")
        cat_time[cat] = cat_time.get(cat, 0) + (d.get("total_seconds", 0))
        cat_domains.setdefault(cat, []).append(d["domain"])

    category_breakdown = sorted(
        [{"category": c, "seconds": s, "domains": cat_domains.get(c, [])} for c, s in cat_time.items()],
        key=lambda x: -x["seconds"],
    )

    # 3. Top pages (group by URL, show page titles and total time)
    page_map: dict[str, dict] = {}
    for e in events:
        url = e.get("url", "")
        if url not in page_map:
            page_map[url] = {
                "url": url,
                "domain": e.get("domain", ""),
                "title": e.get("page_title", ""),
                "category": e.get("category", "browsing"),
                "total_seconds": 0,
                "visits": 0,
            }
        page_map[url]["total_seconds"] += e.get("duration_seconds", 0)
        page_map[url]["visits"] += 1
        # Use the longest title (usually the most descriptive)
        if len(e.get("page_title", "")) > len(page_map[url]["title"]):
            page_map[url]["title"] = e["page_title"]

    top_pages = sorted(page_map.values(), key=lambda p: -p["total_seconds"])[:20]

    # 4. Hourly browsing heatmap
    hourly: dict[int, int] = {h: 0 for h in range(24)}
    for e in events:
        try:
            hour = int(e["timestamp"].split("T")[1].split(":")[0])
            hourly[hour] += e.get("duration_seconds", 0)
        except (IndexError, ValueError):
            pass

    hourly_data = [
        {"hour": f"{h}:00", "seconds": s}
        for h, s in sorted(hourly.items())
        if s > 0 or 6 <= h <= 23
    ]

    # 5. Browsing sequences (domain switches — context switching)
    sequences = []
    prev_domain = None
    for e in events:
        domain = e.get("domain", "")
        if domain and domain != prev_domain:
            sequences.append({
                "time": e.get("timestamp", ""),
                "domain": domain,
                "title": e.get("page_title", ""),
                "category": e.get("category", "browsing"),
            })
            prev_domain = domain

    # 6. Productivity split
    from config.settings import get_settings
    productive_cats = get_settings().categories.PRODUCTIVE_CATEGORIES
    distraction_cats = {"entertainment"}
    neutral_cats = {"browsing", "communication", "other"}
    productive_secs = sum(s for c, s in cat_time.items() if c in productive_cats)
    distraction_secs = sum(s for c, s in cat_time.items() if c in distraction_cats)
    neutral_secs = sum(s for c, s in cat_time.items() if c in neutral_cats)

    return {
        "date": date_str,
        "summary": {
            "total_seconds": total_seconds,
            "total_visits": total_visits,
            "unique_domains": len(domains),
            "unique_pages": len(page_map),
            "productive_seconds": productive_secs,
            "distraction_seconds": distraction_secs,
            "neutral_seconds": neutral_secs,
        },
        "domains": domains,
        "top_pages": top_pages,
        "category_breakdown": category_breakdown,
        "hourly": hourly_data,
        "sequences": sequences[:100],
        "url_events": events[:200],
    }


@app.get("/api/urls/range/stats")
async def get_url_range_stats(start: str = Query(...), end: str = Query(...)):
    """Get aggregated URL stats for a date range."""
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Invalid date format.")
    return {"domains": db.get_domain_stats_range(start_date, end_date)}


# ── Insights ─────────────────────────────────────────────────────

@app.get("/api/insights/trends")
async def get_insights(days: int = Query(default=30)):
    """Get aggregated insights and trends for the past N days."""
    today = date.today()
    start = today - timedelta(days=days - 1)

    # Collect daily summaries for the full range
    summaries = db.get_weekly_summaries(today, days=days)

    # Focus scores over time
    focus_scores = [
        {"date": s.date.isoformat(), "score": s.focus_score}
        for s in summaries
    ]

    # Daily tracked hours
    daily_hours = [
        {"date": s.date.isoformat(), "hours": round(s.total_tracked_minutes / 60, 1)}
        for s in summaries
    ]

    # Category totals across the range
    cat_totals = {
        "Deep Work": 0, "Communication": 0, "Browsing": 0,
        "Entertainment": 0, "Writing": 0, "Learning": 0, "Other": 0,
    }
    cat_field_map = {
        "Deep Work": "focus_minutes", "Communication": "communication_minutes",
        "Browsing": "browsing_minutes", "Entertainment": "entertainment_minutes",
        "Writing": "writing_minutes", "Learning": "learning_minutes",
        "Other": "other_minutes",
    }
    for s in summaries:
        for name, field in cat_field_map.items():
            cat_totals[name] += getattr(s, field, 0)

    category_totals = [
        {"name": name, "minutes": mins}
        for name, mins in sorted(cat_totals.items(), key=lambda x: -x[1])
        if mins > 0
    ]

    # Top apps from sessions (aggregate across all days in range)
    app_minutes: dict[str, int] = {}
    for day_offset in range(days):
        d = start + timedelta(days=day_offset)
        sessions = db.aggregate_sessions(d)
        for sess in sessions:
            if sess.category != "idle":
                app_minutes[sess.app_name] = app_minutes.get(sess.app_name, 0) + (sess.duration_seconds // 60)

    app_usage = [
        {"name": name, "minutes": mins}
        for name, mins in sorted(app_minutes.items(), key=lambda x: -x[1])
    ]

    # Comparison: current half vs previous half
    mid = days // 2
    current_half = [s for s in summaries if s.date > today - timedelta(days=mid)]
    previous_half = [s for s in summaries if s.date <= today - timedelta(days=mid)]

    def avg(lst, attr):
        if not lst:
            return 0
        return sum(getattr(s, attr, 0) for s in lst) / len(lst)

    comparison = {
        "current_avg_score": round(avg(current_half, "focus_score"), 1),
        "previous_avg_score": round(avg(previous_half, "focus_score"), 1),
        "current_avg_focus_hours": round(avg(current_half, "focus_minutes") / 60, 1),
        "previous_avg_focus_hours": round(avg(previous_half, "focus_minutes") / 60, 1),
        "current_avg_switches": round(avg(current_half, "app_switches"), 1),
        "previous_avg_switches": round(avg(previous_half, "app_switches"), 1),
    }

    return {
        "focus_scores": focus_scores,
        "daily_hours": daily_hours,
        "category_totals": category_totals,
        "app_usage": app_usage[:20],
        "comparison": comparison,
    }


# ── Settings ─────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings_api():
    """Return current settings (masks API keys)."""
    s = get_settings()
    return {
        "capture": {
            "poll_interval": s.capture.poll_interval,
            "screenshot_interval": s.capture.screenshot_interval,
            "idle_threshold": s.capture.idle_threshold,
            "screenshot_quality": s.capture.screenshot_quality,
        },
        "privacy": {
            "blocked_apps": s.privacy.blocked_apps,
            "blocked_url_patterns": s.privacy.blocked_url_patterns,
            "blur_screenshots": s.privacy.blur_screenshots,
            "retention_days": s.privacy.retention_days,
        },
        "ai": {
            "provider": s.ai.provider,
            "claude_model": s.ai.claude_model,
            "has_api_key": bool(s.ai.claude_api_key),
            "ollama_model": s.ai.ollama_model,
            "ollama_url": s.ai.ollama_url,
        },
        "obsidian": {
            "vault_path": s.obsidian.vault_path,
            "daily_notes_folder": s.obsidian.daily_notes_folder,
        },
        "dashboard": {
            "port": s.dashboard.port,
        },
        "storage": {
            "db_path": str(DB_PATH),
            "screenshots_dir": str(SCREENSHOTS_DIR),
        },
    }


@app.put("/api/settings")
async def update_settings(body: dict):
    """Update settings and persist to settings.toml."""
    import tomllib

    config_path = Path(__file__).resolve().parent.parent / "config" / "settings.toml"

    # Load existing TOML as raw dict
    raw = {}
    if config_path.exists():
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    # Merge updates
    for section, values in body.items():
        if section == "storage":
            continue  # read-only
        if not isinstance(values, dict):
            continue
        if section not in raw:
            raw[section] = {}
        for k, v in values.items():
            if k == "has_api_key":
                continue  # don't write masked fields back
            raw[section][k] = v

    # Write back as TOML
    lines = ["# Retra Configuration\n"]
    for section, values in raw.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in values.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            elif isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, list):
                items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
                lines.append(f"{k} = [{items}]")
            else:
                lines.append(f"{k} = {v}")
        lines.append("")

    config_path.write_text("\n".join(lines), encoding="utf-8")

    # Reload singleton
    from config.settings import _settings
    import config.settings as cs
    cs._settings = None

    return {"saved": True}


# ── Health ────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ── Serve React Dashboard ────────────────────────────────────────

DASHBOARD_DIR = Path(__file__).parent / "dashboard" / "dist"


@app.on_event("startup")
async def mount_dashboard():
    """Mount the built React dashboard if it exists."""
    if DASHBOARD_DIR.exists():
        # Serve static assets (JS, CSS, images) from the dist directory
        app.mount("/assets", StaticFiles(directory=str(DASHBOARD_DIR / "assets")), name="static-assets")

        # SPA catch-all: serve index.html for any non-API route so React Router works
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            # Serve actual static files (favicon, icons, etc.) if they exist
            static_file = DASHBOARD_DIR / full_path
            if full_path and static_file.is_file():
                return FileResponse(str(static_file))
            return FileResponse(str(DASHBOARD_DIR / "index.html"))


def run_server():
    """Start the dashboard server."""
    import uvicorn
    settings = get_settings()
    port = settings.dashboard.port
    # Use 5174 for API when running in dev mode (Vite proxies /api to this)
    if DASHBOARD_DIR.exists():
        print(f"[Retra] Dashboard running at http://localhost:{port}")
    else:
        port = 5174
        print(f"[Retra] API server running at http://localhost:{port}")
        print(f"[Retra] Run 'cd ui/dashboard && npm run dev' for the dashboard")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
