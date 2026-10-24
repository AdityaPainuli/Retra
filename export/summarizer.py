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
from export import pipeline_status


def _response_text(data: dict) -> Optional[str]:
    """Join the text blocks of a Messages API response.

    Current models think by default, so content[0] is usually a thinking
    block, not text. Returns None on a refusal or a reply cut off by
    max_tokens, so a truncated reflection is never saved as if complete.
    """
    stop = data.get("stop_reason")
    if stop in ("refusal", "max_tokens"):
        pipeline_status.note_error(f"Claude API: stop_reason={stop}")
        print(f"[Retra] Claude response unusable (stop_reason={stop})")
        return None
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    return text or None


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
    Returns {project_name: estimated_minutes}.

    Only emits things that *look like* repos/paths/branches — never bare
    English words from arbitrary title fragments.
    """
    import re
    # Accumulate in SECONDS — per-session // 60 loses fractional minutes
    # on every session, undercounting projects that appear in many short
    # sessions (the common context-switching case).
    project_seconds: dict[str, int] = {}

    # Pattern 1 (editor titles): require a file extension before the em-dash,
    #   AND a project token that looks like a slug (>=3 chars, includes a
    #   hyphen, dot, underscore, or digit — not a plain English word).
    #   Matches: "report2.md — presentation-generator", "main.py — chatty-cortex"
    #   Rejects: "Untitled — YouTube", "Free Time — Brave Browser"
    editor_re = re.compile(
        r'\b[\w-]+\.[a-zA-Z0-9]{1,6}\s*—\s*'                    # filename.ext —
        r'([a-z][a-z0-9]*(?:[-_.][a-z0-9]+)+|[a-z]{4,}-[a-z0-9-]+)',
        re.IGNORECASE,
    )

    # Pattern 4 (terminal cwd): only well-known dev parent dirs, child must be
    # a real slug (>=3 chars, contains hyphen/digit/dot OR is in a known repo
    # whitelist). Rejects "/Desktop/Free", "/Desktop/Watch", etc.
    cwd_re = re.compile(
        r'[~/](?:projects?|repos?|src|code|work|Documents/work)/([a-zA-Z0-9][\w.-]{2,})'
    )

    for session in summary.sessions:
        if session.category == "idle" or session.duration_seconds <= 0:
            continue

        session_secs = session.duration_seconds

        for title in session.window_titles:
            for m in editor_re.finditer(title):
                proj = m.group(1).strip()
                if _is_real_project_name(proj):
                    project_seconds[proj] = project_seconds.get(proj, 0) + session_secs

            # Pattern 2: GitHub/GitLab URLs "owner/repo"
            m = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', title)
            if m:
                proj = m.group(1).rstrip("/")
                # Strip ".git" suffix for cleaner names
                if proj.endswith(".git"):
                    proj = proj[:-4]
                project_seconds[proj] = project_seconds.get(proj, 0) + session_secs

            # Pattern 3: feature branches in terminal — only non-trivial ones
            m = re.search(r'git:\(([^)]+)\)', title)
            if m:
                branch = m.group(1)
                if branch not in ("main", "master", "develop", "dev", "trunk"):
                    key = f"branch:{branch}"
                    project_seconds[key] = project_seconds.get(key, 0) + session_secs

            for m in cwd_re.finditer(title):
                proj = m.group(1)
                if _is_real_project_name(proj):
                    project_seconds[proj] = project_seconds.get(proj, 0) + session_secs

    # Deduplicate: if "retra" and "Desktop/retra" both exist, merge
    merged_secs: dict[str, int] = {}
    for proj, secs in project_seconds.items():
        # Normalize: strip prefixes, lowercase for comparison
        base = proj.split("/")[-1].lower().strip(".")
        # Validate the original key: "acme/retra" is a strong repo signal even
        # though its bare base "retra" would fail the English-word check.
        if base in _SKIP_PROJECTS or not _is_real_project_name(proj):
            continue
        # Use the original casing of the longest key
        existing = None
        for k in merged_secs:
            if k.split("/")[-1].lower().strip(".") == base:
                existing = k
                break
        if existing:
            merged_secs[existing] += secs
        else:
            merged_secs[proj] = secs

    # Convert to minutes once at the end. Drop sub-minute projects to keep
    # output readable, matching the prior < 1 min skip behavior.
    return {p: s // 60 for p, s in merged_secs.items() if s >= 60}


# Words that frequently appear after em-dashes in YouTube/article/Linear titles
# but never indicate a project. Lowercase comparison.
_SKIP_PROJECTS = {
    # IDE chrome
    "settings", "extensions", "output", "terminal", "debug",
    "editor", "welcome", "untitled", "workspace", "window",
    "tab", "file", "folder", "code", "cursor", "vscode",
    "new", "open", "save", "close", "search", "replace",
    "working tree", "source control", "problems", "git",
    # Common YouTube / article / browser title noise
    "youtube", "youtu.be", "google search", "duckduckgo",
    "claude", "chatgpt", "gemini", "perplexity", "copilot",
    "loading", "home", "inbox", "calendar", "linear", "linkedin",
    "slack", "discord", "messages", "mail", "gmail",
    "netmirror", "netflix", "spotify", "twitch", "reddit",
    # Generic English nouns that show up as title fragments
    "free", "love", "your", "the", "watch", "travel", "consume",
    "mario", "play", "stop", "next", "previous", "page", "video",
    "audio", "image", "photo", "song", "track", "movie", "show",
    "deal", "plans", "team", "upload", "validate", "recall",
    "headless", "simple", "id", "professional", "onboarding",
    "dav", "tejas", "louis", "external", "externa", "consume",
    "ai", "ml", "qa", "ui", "ux", "api", "db", "os",
}


_ENGLISH_WORD_RE = __import__("re").compile(r"^[a-z]+$")


def _is_real_project_name(name: str) -> bool:
    """Heuristic: looks like a real project slug, not a bare English word."""
    if not name or len(name) < 3:
        return False
    n = name.lower().strip(".")
    if n in _SKIP_PROJECTS:
        return False
    # Branch:foo, owner/repo, kebab-case, snake_case, has digits or dots
    if "/" in name or "-" in name or "_" in name or "." in name:
        return True
    if any(c.isdigit() for c in name):
        return True
    # Bare English word with no separators → reject. This kills "Free",
    # "Watch", "Mario", "The", "Headless", "Slack", etc.
    if _ENGLISH_WORD_RE.match(n):
        return False
    return True


def summarize_with_claude(summary: DailySummary) -> Optional[str]:
    """Generate reflection using Claude API."""
    settings = get_settings()
    api_key = settings.ai.claude_api_key

    if not api_key:
        print("[Retra] Warning: ANTHROPIC_API_KEY not set. Skipping AI summary.")
        pipeline_status.note_error("ANTHROPIC_API_KEY not set")
        print("[Retra] Set it in .env file or config/settings.toml")
        return None

    activity_data = _build_activity_summary(summary)
    prompt = REFLECTION_PROMPT.format(activity_data=activity_data)

    payload = json.dumps({
        "model": settings.ai.claude_model,
        # Room for adaptive thinking plus the 200-350 word reflection.
        "max_tokens": 8000,
        "output_config": {"effort": "medium"},
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
        with urllib.request.urlopen(req, timeout=180, context=_ssl_context) as resp:
            data = json.loads(resp.read().decode())
            return _response_text(data)
    except Exception as e:
        print(f"[Retra] Claude API error: {e}")
        pipeline_status.note_error(f"Claude API: {e}")

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
        pipeline_status.note_error(f"Ollama: {e}")

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


# ── Replay-page narrative ────────────────────────────────────────────────────

NARRATIVE_PROMPT = """You are narrating someone's day, replay-style, from their captured
activity. Produce a *timeline of short beats* — each beat is one sentence
describing what happened in a stretch of the day, anchored to a wall-clock
time. The reader will scrub through screenshots; your beats will pop up
as captions when their playback time matches the beat time.

Rules:
- 8–18 beats total. Cover the whole day's active periods proportionally.
- One sentence per beat. Concrete, specific, second-person ("you").
- Reference real apps, websites, repos, or projects from the data — never
  invent details that aren't there.
- Avoid platitudes ("you were productive"). Say *what* happened.
- Don't moralize. No "good job" or "consider taking breaks."
- Cluster brief idle/interrupt periods into surrounding beats; don't make
  a beat for every micro-switch.

Output ONLY a JSON array, no prose. Each element:
  {{"time": "HH:MM", "text": "..." }}

Activity data:
{activity_data}
"""


def _generate_narrative_with_claude(summary: DailySummary) -> Optional[list[dict]]:
    """Call Claude to produce timestamped narrative beats. Returns list or None."""
    settings = get_settings()
    api_key = settings.ai.claude_api_key
    if not api_key:
        return None

    activity_data = _build_activity_summary(summary)
    prompt = NARRATIVE_PROMPT.format(activity_data=activity_data)

    payload = json.dumps({
        "model": settings.ai.claude_model,
        "max_tokens": 8000,
        "output_config": {"effort": "medium"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload, headers=headers, method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180, context=_ssl_context) as resp:
            data = json.loads(resp.read().decode())
            raw = _response_text(data)
            if not raw:
                return None
    except Exception as e:
        print(f"[Retra] Narrative Claude error: {e}")
        return None

    # Strip possible markdown fences
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip("` \n")

    try:
        beats = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[Retra] Narrative parse failed; raw={raw[:200]}")
        return None
    # Sometimes the model wraps the array in an object
    if isinstance(beats, dict):
        beats = beats.get("beats") or beats.get("timeline") or []
    if not isinstance(beats, list):
        print(f"[Retra] Narrative has unexpected shape; raw={raw[:200]}")
        return None

    cleaned: list[dict] = []
    for b in beats:
        if not isinstance(b, dict):
            continue
        t = str(b.get("time", "")).strip()
        text = str(b.get("text", "")).strip()
        if not t or not text:
            continue
        # Lightly validate HH:MM
        parts = t.split(":")
        if len(parts) != 2:
            continue
        try:
            hh = int(parts[0])
            mm = int(parts[1])
            if not (0 <= hh < 24 and 0 <= mm < 60):
                continue
        except ValueError:
            continue
        cleaned.append({"time": f"{hh:02d}:{mm:02d}", "text": text})

    cleaned.sort(key=lambda b: b["time"])
    return cleaned or None


def generate_narrative(summary: DailySummary) -> Optional[list[dict]]:
    """Generate replay-style narrative beats for a day.

    Returns a list of {"time": "HH:MM", "text": "..."} beats, sorted by
    time, or None if generation failed. Currently Claude-only (Ollama
    JSON output reliability isn't worth the failure modes here yet).
    """
    settings = get_settings()
    if settings.ai.provider != "claude":
        print(f"[Retra] Narrative not yet supported for provider={settings.ai.provider}")
        return None
    return _generate_narrative_with_claude(summary)
