"""
Catch-up for nightly pipeline runs that failed or never happened.

The 23:00 job often runs while the Mac is asleep with the lid closed
(launchd fires it in brief dark-wake windows), so its network calls die.
The capture daemon calls `pending_days()` while the user is active, and if
anything is missing it runs `python main.py catchup`, which redoes only the
missing stages for the last few days, oldest first.
"""

import fcntl
import re
from contextlib import contextmanager
from datetime import date, timedelta

from config.settings import DATA_DIR, DB_PATH, get_settings

# Only recent days. Older gaps are a deliberate backfill decision (the wiki
# costs ~$0.30 per day), not something to do silently in the background.
LOOKBACK_DAYS = 3
# Matches export.obsidian.generate_daily_note: shorter days get no reflection.
MIN_REFLECTION_MINUTES = 30

JOURNAL_LOCK_PATH = DATA_DIR / "journal.lock"
_LOG_DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})(?: \d{2}:\d{2})?\] ingest")


@contextmanager
def journal_lock():
    """Serialize journal/wiki runs across processes (23:00 job vs catch-up),
    since wiki compiles must apply in date order to the same files."""
    JOURNAL_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _wiki_compiled_dates() -> set[str]:
    settings = get_settings()
    log_path = settings.obsidian.resolved_vault_path / "retra-wiki" / "log.md"
    try:
        return set(_LOG_DATE_RE.findall(log_path.read_text(encoding="utf-8")))
    except OSError:
        return set()


def missing_stages(day: date, compiled: set[str] = None) -> set[str]:
    """Which of journal / reflection / wiki still need to run for `day`."""
    from storage.database import Database

    settings = get_settings()
    missing = set()
    if not (settings.obsidian.output_dir / f"{day.isoformat()}.md").exists():
        missing.add("journal")

    summary = Database(DB_PATH).compute_daily_summary(day)
    if summary.total_tracked_minutes > MIN_REFLECTION_MINUTES and not summary.ai_summary:
        missing.add("reflection")

    wiki_ready = (settings.obsidian.resolved_vault_path / "retra-wiki" / "CLAUDE.md").exists()
    if compiled is None:
        compiled = _wiki_compiled_dates()
    if wiki_ready and summary.total_tracked_minutes > 0 and day.isoformat() not in compiled:
        missing.add("wiki")
    return missing


def pending_days(today: date = None) -> dict[date, set[str]]:
    """Past days in the lookback window with missing stages, oldest first."""
    today = today or date.today()
    compiled = _wiki_compiled_dates()
    result = {}
    for offset in range(LOOKBACK_DAYS, 0, -1):
        day = today - timedelta(days=offset)
        missing = missing_stages(day, compiled)
        if missing:
            result[day] = missing
    return result


def run_catchup(today: date = None) -> bool:
    """Redo missing stages for recent days. Returns True if nothing is left."""
    from export.obsidian import export_to_obsidian
    from export.wiki_compiler import compile_daily_note

    with journal_lock():
        # Re-check under the lock: the 23:00 job may have just finished.
        for day, missing in pending_days(today).items():
            print(f"[Retra] Catch-up {day}: {', '.join(sorted(missing))}")
            if missing & {"journal", "reflection"}:
                # A past day reuses a cached reflection and generates one only
                # if it's missing. The wiki runs separately below so a day
                # whose wiki already compiled isn't ingested twice.
                export_to_obsidian(day, compile_wiki=False, prefer_cached_ai=True)
            if "wiki" in missing_stages(day):
                compile_daily_note(day)
                if "wiki" in missing_stages(day):
                    # Later days build on this day's wiki state, so stop here
                    # and let the next catch-up retry in order.
                    print(f"[Retra] Catch-up stopped: wiki for {day} still failing")
                    return False
    return not pending_days(today)
