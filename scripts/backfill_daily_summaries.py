"""
Recompute & re-save daily_summaries for every date with raw events.

Why:
  After fixing time-accounting in aggregate_sessions(), the daily_summaries
  table holds stale numbers. compute_daily_summary() pulls fresh stats from
  sessions but preserves the existing ai_summary text by reading it from
  the same cache row — so re-saving is non-destructive: stats refresh,
  AI text persists.

Run:
  ./venv/bin/python3 scripts/backfill_daily_summaries.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage.database import Database  # noqa: E402
from config.settings import DB_PATH  # noqa: E402


def main() -> None:
    db = Database(DB_PATH)
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date(timestamp) AS d FROM window_events ORDER BY d ASC"
        ).fetchall()
    dates = [date.fromisoformat(r["d"]) for r in rows]

    if not dates:
        print("No event dates found.")
        return

    print(f"Refreshing daily_summaries for {len(dates)} dates "
          f"({dates[0]} → {dates[-1]})…")
    print()

    width = max(len(d.isoformat()) for d in dates)
    for d in dates:
        summary = db.compute_daily_summary(d)
        db.save_daily_summary(summary)
        # Show a short status line per day
        h, m = divmod(summary.total_tracked_minutes, 60)
        ai_kept = "AI" if summary.ai_summary else "  "
        print(
            f"  {d.isoformat():<{width}}  "
            f"total={h}h{m:02d}m  "
            f"focus={summary.focus_minutes//60}h{summary.focus_minutes%60:02d}m  "
            f"score={summary.focus_score:>3}  "
            f"sessions={len(summary.sessions):>3}  "
            f"{ai_kept}"
        )

    print()
    print(f"Done. {len(dates)} day(s) refreshed.")


if __name__ == "__main__":
    main()
