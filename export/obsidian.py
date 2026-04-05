"""
Retra Obsidian Export
Generates richly interlinked daily notes for Obsidian.

Uses [[wikilinks]] throughout so that Obsidian's graph view
shows relationships between days, apps, projects, categories, and domains.

Vault structure created:
  Retra/
    2026-04-02.md          ← daily notes (interlinked to prev/next day)
    Apps/Cursor.md          ← one note per app
    Projects/retra.md     ← one note per detected project
    Categories/Deep Work.md ← one note per category
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from storage.models import DailySummary, Session
from storage.database import Database
from export.summarizer import generate_summary, detect_projects
from config.settings import get_settings, DB_PATH


# Category emoji map
CATEGORY_EMOJI = {
    "coding": "🟡",
    "communication": "🔵",
    "browsing": "🟣",
    "entertainment": "🔴",
    "writing": "🟢",
    "learning": "🩵",
    "idle": "⚫",
    "other": "⚪",
}

CATEGORY_LABELS = {
    "coding": "Deep Work",
    "communication": "Communication",
    "browsing": "Browsing",
    "entertainment": "Entertainment",
    "writing": "Writing",
    "learning": "Learning",
    "idle": "Idle / Away",
    "other": "Other",
}


def generate_daily_note(
    target_date: Optional[date] = None,
    include_ai_summary: bool = True,
) -> str:
    """
    Generate a full Obsidian daily note for the given date.
    Returns the markdown content as a string.
    """
    if target_date is None:
        target_date = date.today()

    db = Database(DB_PATH)
    summary = db.compute_daily_summary(target_date)

    # Generate AI reflection if requested
    ai_text = None
    if include_ai_summary and summary.total_tracked_minutes > 30:
        ai_text = generate_summary(summary)
        if ai_text:
            summary.ai_summary = ai_text
            db.save_daily_summary(summary)

    projects = detect_projects(summary)
    domains = _extract_domains(summary.sessions)

    return _render_markdown(summary, ai_text, projects, domains)


def _render_markdown(
    summary: DailySummary,
    ai_summary: Optional[str] = None,
    projects: Optional[dict[str, int]] = None,
    domains: Optional[dict[str, int]] = None,
) -> str:
    """Render the daily note as markdown with [[wikilinks]] for graph connectivity."""
    d = summary.date
    day_name = d.strftime("%A")
    date_display = d.strftime("%B %d, %Y")
    short_date = d.strftime("%Y-%m-%d")

    # Prev / next day links
    prev_date = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")

    # Collect all unique apps used
    apps_used = []
    seen_apps = set()
    for s in summary.sessions:
        if s.app_name not in seen_apps and s.category != "idle":
            seen_apps.add(s.app_name)
            apps_used.append(s.app_name)

    # Build frontmatter tags from categories + projects
    fm_tags = ["retra", "daily-review"]
    active_categories = [cat["name"].lower().replace(" ", "-") for cat in summary.category_breakdown() if cat["minutes"] > 0]
    fm_tags.extend(active_categories)
    if projects:
        fm_tags.extend(p.lower().replace("/", "-") for p in projects)

    lines = []

    # ── Frontmatter ──
    lines.append("---")
    lines.append(f"date: {short_date}")
    lines.append(f"type: daily-review")
    lines.append(f"focus_score: {summary.focus_score}")
    lines.append(f"total_tracked: {summary.total_tracked_display()}")
    lines.append(f"deep_work: {_fmt_min(summary.focus_minutes)}")
    lines.append(f"apps: [{', '.join(apps_used)}]")
    if projects:
        lines.append(f"projects: [{', '.join(projects.keys())}]")
    lines.append(f"tags: [{', '.join(fm_tags)}]")
    lines.append("---")
    lines.append("")

    # ── Nav links ──
    lines.append(f"⬅️ [[{prev_date}|Previous Day]] · ➡️ [[{next_date}|Next Day]]")
    lines.append("")

    # ── Header ──
    lines.append(f"# 📊 Retra — {day_name[:3]}, {date_display}")
    lines.append("")

    # ── Quick Stats ──
    lines.append("> [!info] Quick Stats")
    lines.append(f"> 🎯 **Focus Score:** {summary.focus_score}/100 · "
                 f"⏱️ **Tracked:** {summary.total_tracked_display()} · "
                 f"🔥 **Deep Work:** {_fmt_min(summary.focus_minutes)} · "
                 f"⚡ **Longest Streak:** {summary.longest_focus_streak_minutes}m · "
                 f"🔄 **Switches:** {summary.app_switches}")
    lines.append("")

    # ── Timeline (with [[App]] links) ──
    lines.append("## 🕐 Timeline")
    lines.append("")

    for session in summary.sessions:
        if session.duration_seconds < 30:
            continue
        emoji = CATEGORY_EMOJI.get(session.category, "⚪")
        start = session.start_time.strftime("%H:%M")
        end = session.end_time.strftime("%H:%M")
        app_link = f"[[Apps/{session.app_name}|{session.app_name}]]" if session.category != "idle" else session.app_name
        title = session.window_titles[0] if session.window_titles else ""
        title_display = f" — {title}" if title else ""
        lines.append(f"{emoji} `{start}–{end}` **{app_link}**{title_display}")

    lines.append("")

    # ── Category Breakdown (with [[Category]] links) ──
    lines.append("## 📊 Focus Summary")
    lines.append("")
    lines.append("| Category | Time | % of Day |")
    lines.append("|----------|------|----------|")

    active = summary.active_minutes or 1
    for cat in summary.category_breakdown():
        if cat["minutes"] > 0:
            pct = round((cat["minutes"] / active) * 100)
            cat_link = f"[[Categories/{cat['name']}\\|{cat['name']}]]"
            lines.append(f"| {cat_link} | {_fmt_min(cat['minutes'])} | {pct}% |")

    lines.append("")

    # ── Projects (with [[Project]] links) ──
    if projects:
        lines.append("## 🗂️ Projects")
        lines.append("")
        for proj, mins in sorted(projects.items(), key=lambda x: -x[1]):
            lines.append(f"- [[Projects/{proj}|{proj}]] — {_fmt_min(mins)}")
        lines.append("")

    # ── Top Domains (with [[Domain]] links) ──
    if domains:
        lines.append("## 🌐 Top Domains")
        lines.append("")
        for domain, mins in sorted(domains.items(), key=lambda x: -x[1])[:10]:
            if mins >= 1:
                lines.append(f"- [[Domains/{domain}|{domain}]] — {_fmt_min(mins)}")
        lines.append("")

    # ── AI Reflection ──
    if ai_summary:
        lines.append("## 🤖 AI Reflection")
        lines.append("")
        lines.append(ai_summary)
        lines.append("")

    # ── Top Sessions (with [[App]] links) ──
    lines.append("## 📋 Top Sessions")
    lines.append("")

    top_sessions = sorted(
        [s for s in summary.sessions if s.category != "idle"],
        key=lambda s: s.duration_seconds,
        reverse=True,
    )[:5]

    for s in top_sessions:
        emoji = CATEGORY_EMOJI.get(s.category, "⚪")
        app_link = f"[[Apps/{s.app_name}|{s.app_name}]]"
        cat_link = f"[[Categories/{CATEGORY_LABELS.get(s.category, 'Other')}|{CATEGORY_LABELS.get(s.category, 'Other')}]]"
        titles = " → ".join(s.window_titles[:3]) if s.window_titles else ""
        lines.append(f"- {emoji} **{app_link}** ({s.duration_display}) · {cat_link} — {titles}")

    lines.append("")

    # ── Apps Used ──
    lines.append("## 🖥️ Apps Used")
    lines.append("")
    lines.append(" · ".join(f"[[Apps/{app}|{app}]]" for app in apps_used))
    lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append(f"*Generated by Retra at {datetime.now().strftime('%H:%M')}*")

    return "\n".join(lines)


def _fmt_min(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h > 0:
        return f"{h}h {m}m" if m > 0 else f"{h}h"
    return f"{m}m"


def _extract_domains(sessions: list[Session]) -> dict[str, int]:
    """Extract domains from sessions and estimate minutes per domain."""
    domain_mins: dict[str, int] = {}
    for s in sessions:
        if not s.domains:
            continue
        per_domain = max(s.duration_seconds // 60, 1) // len(s.domains)
        for d in s.domains:
            domain_mins[d] = domain_mins.get(d, 0) + per_domain
    return domain_mins


# ── Linked note generators ──

def _generate_app_note(app_name: str, output_dir: Path, daily_date: str):
    """Create or update an App note that backlinks to daily notes."""
    app_dir = output_dir / "Apps"
    app_dir.mkdir(parents=True, exist_ok=True)
    filepath = app_dir / f"{_safe_filename(app_name)}.md"

    # If note exists, append the daily link if not already there
    daily_link = f"[[{daily_date}]]"
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
        if daily_link not in content:
            content = content.rstrip() + f"\n- {daily_link}\n"
            filepath.write_text(content, encoding="utf-8")
        return

    lines = [
        "---",
        f"type: app",
        f"name: {app_name}",
        f"tags: [retra, app]",
        "---",
        "",
        f"# 🖥️ {app_name}",
        "",
        "App tracked by Retra.",
        "",
        "## Daily Usage",
        "",
        f"- {daily_link}",
        "",
    ]
    filepath.write_text("\n".join(lines), encoding="utf-8")


def _generate_project_note(project_name: str, output_dir: Path, daily_date: str):
    """Create or update a Project note that backlinks to daily notes."""
    proj_dir = output_dir / "Projects"
    proj_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(project_name)
    filepath = proj_dir / f"{safe_name}.md"

    daily_link = f"[[{daily_date}]]"
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
        if daily_link not in content:
            content = content.rstrip() + f"\n- {daily_link}\n"
            filepath.write_text(content, encoding="utf-8")
        return

    lines = [
        "---",
        f"type: project",
        f"name: {project_name}",
        f"tags: [retra, project]",
        "---",
        "",
        f"# 🗂️ {project_name}",
        "",
        "Project detected by Retra from window titles.",
        "",
        "## Work Sessions",
        "",
        f"- {daily_link}",
        "",
    ]
    filepath.write_text("\n".join(lines), encoding="utf-8")


def _generate_category_note(category_name: str, output_dir: Path, daily_date: str):
    """Create or update a Category note that backlinks to daily notes."""
    cat_dir = output_dir / "Categories"
    cat_dir.mkdir(parents=True, exist_ok=True)
    filepath = cat_dir / f"{_safe_filename(category_name)}.md"

    daily_link = f"[[{daily_date}]]"
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
        if daily_link not in content:
            content = content.rstrip() + f"\n- {daily_link}\n"
            filepath.write_text(content, encoding="utf-8")
        return

    emoji = {"Deep Work": "🟡", "Communication": "🔵", "Browsing": "🟣",
             "Entertainment": "🔴", "Writing": "🟢", "Learning": "🩵", "Other": "⚪"}.get(category_name, "⚪")

    lines = [
        "---",
        f"type: category",
        f"name: {category_name}",
        f"tags: [retra, category]",
        "---",
        "",
        f"# {emoji} {category_name}",
        "",
        "Activity category tracked by Retra.",
        "",
        "## Days",
        "",
        f"- {daily_link}",
        "",
    ]
    filepath.write_text("\n".join(lines), encoding="utf-8")


def _generate_domain_note(domain: str, output_dir: Path, daily_date: str):
    """Create or update a Domain note that backlinks to daily notes."""
    dom_dir = output_dir / "Domains"
    dom_dir.mkdir(parents=True, exist_ok=True)
    filepath = dom_dir / f"{_safe_filename(domain)}.md"

    daily_link = f"[[{daily_date}]]"
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
        if daily_link not in content:
            content = content.rstrip() + f"\n- {daily_link}\n"
            filepath.write_text(content, encoding="utf-8")
        return

    lines = [
        "---",
        f"type: domain",
        f"name: {domain}",
        f"tags: [retra, domain]",
        "---",
        "",
        f"# 🌐 {domain}",
        "",
        "Website tracked by Retra.",
        "",
        "## Visits",
        "",
        f"- {daily_link}",
        "",
    ]
    filepath.write_text("\n".join(lines), encoding="utf-8")


def _safe_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    return name.replace("/", "-").replace("\\", "-").replace(":", "-").replace(".", "_")


def export_to_obsidian(target_date: Optional[date] = None):
    """Generate and write the daily note + linked notes to the Obsidian vault."""
    settings = get_settings()
    if target_date is None:
        target_date = date.today()

    db = Database(DB_PATH)
    summary = db.compute_daily_summary(target_date)
    projects = detect_projects(summary)
    domains = _extract_domains(summary.sessions)

    # Generate the markdown
    content = generate_daily_note(target_date)

    # Write to vault
    output_dir = settings.obsidian.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    short_date = target_date.strftime("%Y-%m-%d")
    filename = f"{short_date}.md"
    filepath = output_dir / filename
    filepath.write_text(content, encoding="utf-8")

    # Generate linked notes for graph connectivity
    seen_apps = set()
    for s in summary.sessions:
        if s.app_name not in seen_apps and s.category != "idle":
            seen_apps.add(s.app_name)
            _generate_app_note(s.app_name, output_dir, short_date)

    for proj in (projects or {}):
        _generate_project_note(proj, output_dir, short_date)

    for cat in summary.category_breakdown():
        if cat["minutes"] > 0:
            _generate_category_note(cat["name"], output_dir, short_date)

    for domain, mins in (domains or {}).items():
        if mins >= 1:
            _generate_domain_note(domain, output_dir, short_date)

    print(f"[Retra] Journal exported to: {filepath}")
    return filepath
