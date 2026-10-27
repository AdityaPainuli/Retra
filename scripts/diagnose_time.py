"""
Step 0 — diagnose how time is accounted in Retra vs. reality.

For a given date, compute per-app totals three ways and compare:

  A) Production: what aggregate_sessions() + the dashboard show today.
  B) Lossless reconstruction: walk raw window_events in order, attribute
     (next.ts - this.ts), clipped at GAP_CAP, to this.app. This is the
     upper bound of what we can claim from our own data.
  C) macOS Screen Time: query knowledgeC.db (/app/inFocus stream).
     Requires Full Disk Access on the running terminal/Python; if denied,
     this column is left blank with a note.

Output:
  - Per-app table A vs B vs C with deltas
  - Total tracked time per source
  - Diagnostic counters: event count, idle events, sessions dropped for
    being <60s, etc.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Make the project importable when run from anywhere
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage.database import Database  # noqa: E402
from config.settings import DB_PATH  # noqa: E402


# Cap individual inter-event gaps at this many seconds. Anything larger we
# treat as "the user walked away" and don't attribute. 300s mirrors the
# session merge_threshold, so reconstruction is consistent with production.
GAP_CAP = 300


def fmt(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def lossless_reconstruction(events) -> tuple[dict[str, int], dict]:
    """Walk events, attribute (next - this) per app. Diagnostic counters."""
    per_app: dict[str, int] = defaultdict(int)
    counters = {
        "events": len(events),
        "idle_events": 0,
        "gaps_clipped": 0,
        "total_clipped_seconds": 0,
        "raw_attributed_seconds": 0,
    }

    for i, e in enumerate(events):
        if e.is_idle:
            counters["idle_events"] += 1
            # Don't attribute idle to any app
            continue

        if i + 1 < len(events):
            gap = int((events[i + 1].timestamp - e.timestamp).total_seconds())
        else:
            # Last event of the day. Attribute one heartbeat interval (60s) as
            # an upper bound — we don't actually know how long they stayed.
            gap = 60

        if gap < 0:
            gap = 0

        if gap > GAP_CAP:
            counters["gaps_clipped"] += 1
            counters["total_clipped_seconds"] += gap - GAP_CAP
            gap = GAP_CAP

        per_app[e.app_name] += gap
        counters["raw_attributed_seconds"] += gap

    return per_app, counters


def production_accounting(db: Database, day: date) -> tuple[dict[str, int], dict]:
    """Replicate what the dashboard shows: aggregate_sessions, sum per app."""
    # Pre-aggregate (without the <60s filter) so we can count what gets dropped.
    sessions_unfiltered = _sessions_unfiltered(db, day)
    dropped_under_60 = sum(1 for s in sessions_unfiltered if s.duration_seconds < 60)
    dropped_seconds = sum(s.duration_seconds for s in sessions_unfiltered if s.duration_seconds < 60)
    idle_sessions = sum(1 for s in sessions_unfiltered if s.category == "idle")

    # Production path — same call the dashboard uses
    sessions = db.aggregate_sessions(day)

    per_app: dict[str, int] = defaultdict(int)
    for s in sessions:
        per_app[s.app_name] += s.duration_seconds

    counters = {
        "sessions_kept": len(sessions),
        "sessions_dropped_under_60s": dropped_under_60,
        "dropped_seconds_total": dropped_seconds,
        "idle_sessions": idle_sessions,
    }
    return per_app, counters


def _sessions_unfiltered(db: Database, day: date):
    """Run the same session-building pipeline but without the <60s drop, so
    we can measure how much time the filter throws away."""
    # We can't easily call the internal pipeline without the filter. Cheapest
    # path: run aggregate_sessions and then re-run a parallel build that
    # keeps everything. To keep this honest, we mirror the same loops here.
    from datetime import datetime as _dt
    events = db.get_events_for_date(day)
    if not events:
        return []

    sessions = []
    current = None
    merge_threshold = 300

    for event in events:
        if event.is_idle:
            if current:
                current.end_time = event.timestamp
                current.duration_seconds = int(
                    (current.end_time - current.start_time).total_seconds()
                )
                sessions.append(current)
                current = None
            from storage.models import Session as _S
            sessions.append(_S(
                start_time=event.timestamp,
                end_time=event.timestamp,
                app_name="Idle",
                category="idle",
                duration_seconds=0,
            ))
            continue

        if current is None:
            from storage.models import Session as _S
            current = _S(
                start_time=event.timestamp,
                end_time=event.timestamp,
                app_name=event.app_name,
                category=event.category,
                window_titles=[event.window_title] if event.window_title else [],
            )
        elif (
            event.app_name == current.app_name
            and (event.timestamp - current.end_time).total_seconds() <= merge_threshold
        ):
            current.end_time = event.timestamp
        else:
            current.duration_seconds = int(
                (current.end_time - current.start_time).total_seconds()
            )
            sessions.append(current)
            from storage.models import Session as _S
            current = _S(
                start_time=event.timestamp,
                end_time=event.timestamp,
                app_name=event.app_name,
                category=event.category,
                window_titles=[event.window_title] if event.window_title else [],
            )

    if current:
        current.duration_seconds = int(
            (current.end_time - current.start_time).total_seconds()
        )
        sessions.append(current)
    return sessions


# ── macOS Screen Time / knowledgeC ────────────────────────────────────────────

KNOWLEDGE_DB = Path.home() / "Library/Application Support/Knowledge/knowledgeC.db"
MAC_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01 UTC


def screentime_accounting(day: date) -> tuple[dict[str, int] | None, str | None]:
    """Query knowledgeC.db /app/inFocus stream for the day. Returns (per_app, error)."""
    if not KNOWLEDGE_DB.exists():
        return None, f"knowledgeC.db not at expected path: {KNOWLEDGE_DB}"

    # Check we can read it (TCC will block without Full Disk Access)
    try:
        with open(KNOWLEDGE_DB, "rb") as f:
            f.read(16)
    except PermissionError:
        return None, (
            "Permission denied reading knowledgeC.db. macOS requires the "
            "running process (Terminal / iTerm / Claude Code) to have "
            "Full Disk Access. Grant it in System Settings → Privacy & "
            "Security → Full Disk Access, then re-run."
        )

    # Day bounds in Mac absolute time
    start_dt = datetime.combine(day, time.min)
    end_dt = datetime.combine(day, time.max)
    start_mac = start_dt.timestamp() - MAC_EPOCH_OFFSET
    end_mac = end_dt.timestamp() - MAC_EPOCH_OFFSET

    try:
        conn = sqlite3.connect(f"file:{KNOWLEDGE_DB}?mode=ro&immutable=1", uri=True)
        rows = conn.execute(
            """
            SELECT ZVALUESTRING AS bundle,
                   SUM(ZENDDATE - ZSTARTDATE) AS seconds
            FROM ZOBJECT
            WHERE ZSTREAMNAME = '/app/inFocus'
              AND ZSTARTDATE BETWEEN ? AND ?
            GROUP BY ZVALUESTRING
            ORDER BY seconds DESC
            """,
            (start_mac, end_mac),
        ).fetchall()
        conn.close()
    except sqlite3.DatabaseError as ex:
        return None, f"sqlite error reading knowledgeC.db: {ex}"

    per_app: dict[str, int] = {}
    for bundle, secs in rows:
        if bundle is None:
            continue
        per_app[bundle] = int(secs or 0)
    return per_app, None


# ── Bundle ID → display name (best effort) ─────────────────────────────────────

BUNDLE_TO_APP = {
    "com.apple.Safari": "Safari",
    "com.google.Chrome": "Google Chrome",
    "company.thebrowser.Browser": "Arc",
    "com.brave.Browser": "Brave Browser",
    "com.microsoft.VSCode": "Code",
    "com.todesktop.230313mzl4w4u92": "Cursor",
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm2",
    "com.mitchellh.ghostty": "Ghostty",
    "dev.warp.Warp-Stable": "Warp",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.hnc.Discord": "Discord",
    "linear.linear-app": "Linear",
    "notion.id": "Notion",
    "com.apple.finder": "Finder",
    "com.spotify.client": "Spotify",
    "com.anthropic.claudefordesktop": "Claude",
}


def normalize_app(name: str) -> str:
    return BUNDLE_TO_APP.get(name, name)


# ── Report ────────────────────────────────────────────────────────────────────

def run(day: date) -> None:
    db = Database(DB_PATH)

    events = db.get_events_for_date(day)
    if not events:
        print(f"No events for {day}")
        return

    prod_per_app, prod_counters = production_accounting(db, day)
    lossless_per_app, lossless_counters = lossless_reconstruction(events)
    st_per_app, st_err = screentime_accounting(day)
    st_per_app_normalized: dict[str, int] = {}
    if st_per_app:
        for bundle, secs in st_per_app.items():
            st_per_app_normalized[normalize_app(bundle)] = (
                st_per_app_normalized.get(normalize_app(bundle), 0) + secs
            )

    all_apps = set(prod_per_app) | set(lossless_per_app) | set(st_per_app_normalized or {})
    rows = []
    for app in all_apps:
        rows.append((
            app,
            prod_per_app.get(app, 0),
            lossless_per_app.get(app, 0),
            st_per_app_normalized.get(app, 0) if st_per_app_normalized else None,
        ))
    rows.sort(key=lambda r: -(r[2] or 0))

    # Totals
    total_prod = sum(prod_per_app.values())
    total_lossless = sum(lossless_per_app.values())
    total_st = sum(st_per_app_normalized.values()) if st_per_app_normalized else None

    # ── Header ──
    print(f"\n=== TIME ACCOUNTING DIAGNOSTIC — {day.isoformat()} ===")
    print(f"  Raw events:                  {lossless_counters['events']}")
    print(f"  Idle events:                 {lossless_counters['idle_events']}")
    print(f"  Idle sessions (production):  {prod_counters['idle_sessions']}")
    print(f"  Sessions kept (>=60s):       {prod_counters['sessions_kept']}")
    print(f"  Sessions dropped (<60s):     {prod_counters['sessions_dropped_under_60s']}")
    print(f"  Seconds dropped by <60s:     {prod_counters['dropped_seconds_total']}  ({fmt(prod_counters['dropped_seconds_total'])})")
    print(f"  Inter-event gaps clipped:    {lossless_counters['gaps_clipped']} (clipped at {GAP_CAP}s)")
    print(f"  Total seconds clipped:       {lossless_counters['total_clipped_seconds']}  ({fmt(lossless_counters['total_clipped_seconds'])})")
    print()

    print(f"  TOTAL tracked time:")
    print(f"    A) Production (dashboard):  {fmt(total_prod):>10}  ({total_prod}s)")
    print(f"    B) Lossless reconstruction: {fmt(total_lossless):>10}  ({total_lossless}s)")
    if total_st is not None:
        print(f"    C) macOS Screen Time:       {fmt(total_st):>10}  ({total_st}s)")
    else:
        print(f"    C) macOS Screen Time:       (unavailable)")
        if st_err:
            print(f"       reason: {st_err}")
    print()

    # ── Per-app table ──
    print(f"  {'APP':<28} {'A:Prod':>10} {'B:Lossless':>12} {'C:ScreenTime':>14} {'Δ(B-A)':>10} {'Δ(C-A)':>10}")
    print(f"  {'-'*28} {'-'*10} {'-'*12} {'-'*14} {'-'*10} {'-'*10}")
    for app, a, b, c in rows[:25]:
        delta_ba = b - a
        delta_ca = (c - a) if c is not None else None
        c_str = fmt(c) if c is not None else "—"
        delta_ca_str = (f"{'+' if delta_ca >= 0 else ''}{fmt(abs(delta_ca))}") if delta_ca is not None else "—"
        delta_ba_str = f"{'+' if delta_ba >= 0 else '-'}{fmt(abs(delta_ba))}"
        print(f"  {app[:28]:<28} {fmt(a):>10} {fmt(b):>12} {c_str:>14} {delta_ba_str:>10} {delta_ca_str:>10}")

    if len(rows) > 25:
        print(f"  ... ({len(rows) - 25} more apps not shown)")

    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="ISO date (default: yesterday)")
    args = p.parse_args()
    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today() - timedelta(days=1)
    run(target)
