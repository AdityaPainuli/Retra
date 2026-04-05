"""
Retra AI Summarizer
Generates natural-language reflections from daily activity data.
Supports Claude API and local Ollama.
"""

import json
import ssl
import urllib.request
from datetime import date
from typing import Optional

try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ssl_context = None

from storage.models import DailySummary, Session
from config.settings import get_settings, DB_PATH


REFLECTION_PROMPT = """You are a thoughtful productivity coach analyzing someone's full computer activity for the day.
Given the rich activity data below, write a concise, insightful daily reflection (200-350 words).

Guidelines:
- Lead with the most notable pattern or achievement
- Highlight the longest focus block and what was accomplished
- Comment on browsing patterns — were they productive (docs, StackOverflow) or distracting (social media, news)?
- If project work is detected, mention which projects got the most attention
- Note any recurring distractions or context-switching patterns (be kind but honest)
- Note the time distribution — were they most productive in the morning or afternoon?
- End with 1-2 actionable suggestions for tomorrow
- Use a warm, direct tone — like a thoughtful friend, not a robot
- Use **bold** for key highlights

Format the output as plain text with paragraphs. Use **bold** for emphasis on key points.
At the end, add:
**Highlight:** [single most important thing from the day]
**Tomorrow:** [one concrete suggestion]

Activity Data:
{activity_data}
"""


def _build_activity_summary(summary: DailySummary) -> str:
    """Convert a DailySummary + URL data into a rich text block for the AI prompt."""
    lines = [
        f"Date: {summary.date.strftime('%A, %B %d, %Y')}",
        f"Total tracked: {summary.total_tracked_display()}",
        f"Focus score: {summary.focus_score}/100",
        f"Focus percentage: {summary.focus_percentage}%",
        f"App switches: {summary.app_switches}",
        f"Longest focus streak: {summary.longest_focus_streak_minutes} minutes",
        "",
        "Category breakdown:",
    ]

    for cat in summary.category_breakdown():
        if cat["minutes"] > 0:
            h, m = divmod(cat["minutes"], 60)
            time_str = f"{h}h {m}m" if h > 0 else f"{m}m"
            lines.append(f"  - {cat['name']}: {time_str}")

    # Sessions with project detection
    lines.append("")
    lines.append("Sessions (chronological):")
    for session in summary.sessions:
        if session.category != "idle":
            start = session.start_time.strftime("%H:%M")
            end = session.end_time.strftime("%H:%M")
            titles = ", ".join(session.window_titles[:3])
            domains = ", ".join(session.domains[:3]) if session.domains else ""
            detail = titles
            if domains:
                detail += f" | domains: {domains}"
            lines.append(f"  - {start}-{end} | {session.app_name} | {session.category} | {detail}")

    # Add URL/browsing data
    try:
        from storage.database import Database
        db = Database(DB_PATH)
        domain_stats = db.get_domain_stats(summary.date)
        if domain_stats:
            lines.append("")
            lines.append("Top websites visited (by time spent):")
            for d in domain_stats[:10]:
                secs = d.get("total_seconds", 0)
                visits = d.get("visit_count", 0)
                mins = secs // 60
                if mins > 0:
                    lines.append(f"  - {d['domain']}: {mins}m ({visits} visits) [{d.get('category', 'browsing')}]")

        # Add URL events for context
        url_events = db.get_url_events_for_date(summary.date)
        if url_events:
            lines.append("")
            lines.append(f"Total page visits: {len(url_events)}")
            unique_domains = len(set(e.get("domain", "") for e in url_events))
            lines.append(f"Unique domains: {unique_domains}")
    except Exception:
        pass

    # Add detected projects
    projects = detect_projects(summary)
    if projects:
        lines.append("")
        lines.append("Detected projects/repos worked on:")
        for proj, mins in sorted(projects.items(), key=lambda x: -x[1]):
            lines.append(f"  - {proj}: ~{mins}m")

    return "\n".join(lines)


def detect_projects(summary: DailySummary) -> dict[str, int]:
    """Extract project names from window titles across all sessions.
    Returns {project_name: estimated_minutes}."""
    import re
    projects: dict[str, int] = {}

    for session in summary.sessions:
        if session.category == "idle":
            continue

        session_mins = session.duration_seconds // 60
        if session_mins < 1:
            continue

        for title in session.window_titles:
            # Pattern 1: "filename.ext — project-name" (VS Code, Cursor style)
            # Also handles "filename.ext — project-name — Editor"
            m = re.search(r'—\s*([a-zA-Z0-9][\w.-]*(?:[\w.-]+))', title)
            if m:
                proj = m.group(1).strip()
                # Skip generic names
                if proj.lower() not in _SKIP_PROJECTS and len(proj) > 1:
                    projects[proj] = projects.get(proj, 0) + session_mins

            # Pattern 2: GitHub/GitLab URLs "owner/repo"
            m = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', title)
            if m:
                projects[m.group(1)] = projects.get(m.group(1), 0) + session_mins

            # Pattern 3: "branch-name" in terminal titles like "git:(feature/xyz)"
            m = re.search(r'git:\(([^)]+)\)', title)
            if m:
                branch = m.group(1)
                if branch not in ("main", "master", "develop", "dev"):
                    projects[f"branch:{branch}"] = projects.get(f"branch:{branch}", 0) + session_mins

            # Pattern 4: Terminal with path "~/projects/myapp" or "user@host:~/myapp"
            m = re.search(r'[~/](?:projects?|repos?|src|code|Desktop)/([a-zA-Z0-9_.-]+)', title)
            if m:
                proj = m.group(1)
                if proj.lower() not in _SKIP_PROJECTS and len(proj) > 1:
                    projects[proj] = projects.get(proj, 0) + session_mins

    # Deduplicate: if "retra" and "Desktop/retra" both exist, merge
    merged: dict[str, int] = {}
    for proj, mins in projects.items():
        # Normalize: strip prefixes, lowercase for comparison
        base = proj.split("/")[-1].lower().strip(".")
        if base in _SKIP_PROJECTS or len(base) <= 1:
            continue
        # Use the original casing of the longest key
        existing = None
        for k in merged:
            if k.split("/")[-1].lower().strip(".") == base:
                existing = k
                break
        if existing:
            merged[existing] += mins
        else:
            merged[proj] = mins

    return merged


_SKIP_PROJECTS = {
    "settings", "extensions", "output", "terminal", "debug",
    "editor", "welcome", "untitled", "workspace", "window",
    "tab", "file", "folder", "code", "cursor", "vscode",
    "new", "open", "save", "close", "search", "replace",
    "working tree", "source control", "problems", "git",
}


def summarize_with_claude(summary: DailySummary) -> Optional[str]:
    """Generate reflection using Claude API."""
    settings = get_settings()
    api_key = settings.ai.claude_api_key

    if not api_key:
        print("[Retra] Warning: ANTHROPIC_API_KEY not set. Skipping AI summary.")
        print("[Retra] Set it in .env file or config/settings.toml")
        return None

    activity_data = _build_activity_summary(summary)
    prompt = REFLECTION_PROMPT.format(activity_data=activity_data)

    payload = json.dumps({
        "model": settings.ai.claude_model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context) as resp:
            data = json.loads(resp.read().decode())
            if data.get("content"):
                return data["content"][0]["text"]
    except Exception as e:
        print(f"[Retra] Claude API error: {e}")

    return None


def summarize_with_ollama(summary: DailySummary) -> Optional[str]:
    """Generate reflection using local Ollama instance."""
    settings = get_settings()
    activity_data = _build_activity_summary(summary)
    prompt = REFLECTION_PROMPT.format(activity_data=activity_data)

    payload = json.dumps({
        "model": settings.ai.ollama_model,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{settings.ai.ollama_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response")
    except Exception as e:
        print(f"[Retra] Ollama error: {e}")

    return None


def generate_summary(summary: DailySummary) -> Optional[str]:
    """Generate an AI reflection using the configured provider."""
    settings = get_settings()

    if settings.ai.provider == "claude":
        return summarize_with_claude(summary)
    elif settings.ai.provider == "ollama":
        return summarize_with_ollama(summary)
    else:
        print(f"[Retra] Unknown AI provider: {settings.ai.provider}")
        return None
