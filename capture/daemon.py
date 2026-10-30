"""
Retra Capture Daemon
Main loop that captures window events, screenshots, and detects idle state.
Runs as a background service.
"""

import os
import time
import signal
import sys
import logging
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from capture.window_tracker import (
    get_active_window,
    is_screen_locked,
    is_incognito,
    is_secure_input_active,
    ActiveWindow,
    get_browser_url_applescript,
    extract_domain,
    get_project_hint,
)
from capture.screenshot import ScreenshotCapture, purge_old_images
from capture.screen_text import ocr_screenshot, process_backlog
from capture.idle_detector import is_user_idle
from storage.database import Database
from storage.models import WindowEvent, Screenshot
from config.settings import get_settings, PROJECT_ROOT, DB_PATH, SCREENSHOTS_DIR, HEARTBEAT_PATH, PID_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Retra] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("retra")


def _backup_db(src: Path, dest: Path):
    """Snapshot via SQLite's backup API so committed pages still sitting in
    the WAL are included (a plain file copy of retra.db can miss them)."""
    source = sqlite3.connect(src)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


class CaptureDaemon:
    def __init__(self):
        self.settings = get_settings()
        self.db = Database(DB_PATH)
        self.screenshotter = ScreenshotCapture(
            SCREENSHOTS_DIR, quality=self.settings.capture.screenshot_quality
        )
        self.running = False
        self.last_screenshot_time: float = 0
        self.last_window: Optional[ActiveWindow] = None
        self.last_event_time: Optional[datetime] = None
        self.last_url: Optional[str] = None
        self.last_url_time: Optional[datetime] = None

        # Screenshot dedup: skip if screen likely unchanged
        self._last_screenshot_app: Optional[str] = None
        self._last_screenshot_title: Optional[str] = None
        # Whether the current window may be screenshotted (set each cycle)
        self._window_blocked = False

        # OCR runs off the capture loop: one worker, so images are processed
        # in order and Vision never runs twice at once.
        self._ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="retra-ocr")
        self._backlog_lock = threading.Lock()

        self._events_since_start = 0
        self._screenshots_since_start = 0
        self._last_heartbeat_time: float = 0
        self._last_maintenance_date: Optional[date] = None

        # Catch-up for nightly runs missed while the Mac slept
        self._last_catchup_check: float = 0
        self._catchup_proc: Optional[subprocess.Popen] = None
        self._catchup_runs: dict[date, int] = {}

        # Graceful shutdown
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

    def _shutdown(self, signum, frame):
        log.info("Shutting down capture daemon...")
        self.running = False
        # Clean up PID and heartbeat files
        for p in (PID_PATH, HEARTBEAT_PATH):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def _write_heartbeat(self):
        """Write heartbeat file so other components can verify daemon is alive."""
        import json
        heartbeat = {
            "pid": os.getpid(),
            "timestamp": datetime.now().isoformat(),
            "events_captured": self._events_since_start,
            "screenshots_captured": self._screenshots_since_start,
            "uptime_seconds": int(time.time() - self._start_time),
        }
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(json.dumps(heartbeat))

    def _run_maintenance(self):
        """Daily backup + retention cleanup. Runs on startup and at each date change."""
        self._last_maintenance_date = date.today()

        # Rolling daily backup, keep last 7.
        try:
            backups_dir = DB_PATH.parent / "backups"
            backups_dir.mkdir(parents=True, exist_ok=True)
            today_backup = backups_dir / f"{DB_PATH.stem}.{datetime.now():%Y%m%d}.db"
            if not today_backup.exists():
                _backup_db(DB_PATH, today_backup)
                log.info(f"Database backed up to {today_backup.name}")
            keep = 7
            existing = sorted(backups_dir.glob(f"{DB_PATH.stem}.[0-9]*.db"))
            for old in existing[:-keep]:
                old.unlink(missing_ok=True)
                log.info(f"Pruned old backup {old.name}")
            # Also keep the legacy single-slot copy as a hot-spare for tools
            # that still look for it
            _backup_db(DB_PATH, DB_PATH.parent / f"{DB_PATH.stem}.backup.db")
        except Exception as e:
            log.warning(f"Backup failed (non-fatal): {e}")

        privacy = self.settings.privacy
        try:
            self.db.cleanup_old_data(event_retention_days=privacy.event_retention_days)
            log.info(
                "Old data cleanup complete (events: %s)",
                f"{privacy.event_retention_days}d" if privacy.event_retention_days else "kept forever",
            )
        except Exception as e:
            log.warning(f"Cleanup failed (non-fatal): {e}")

        # OCR anything left unprocessed (e.g. daemon stopped mid-queue), then
        # delete images past their retention.
        self._ocr_pool.submit(self._process_screen_text_backlog)

    def _process_screen_text_backlog(self):
        """Runs on the OCR worker."""
        if not self._backlog_lock.acquire(blocking=False):
            return
        try:
            done, failed = process_backlog(
                self.db, should_stop=lambda: not self.running,
                after=self.screenshotter.compress,
            )
            if done or failed:
                log.info(f"Extracted text from {done} queued screenshot(s), {failed} failed")
            purged, strays = purge_old_images(
                self.db, SCREENSHOTS_DIR, self.settings.privacy.retention_days
            )
            if purged or strays:
                log.info(f"Deleted {purged} screenshot image(s) past retention, {strays} stray")
        except Exception as e:
            log.warning(f"Screen text backlog failed (non-fatal): {e}")
        finally:
            self._backlog_lock.release()

    CATCHUP_CHECK_SEC = 1800
    # Each run can cost a wiki compile (~$0.30), so cap repeats for a day
    # whose failure isn't transient.
    MAX_CATCHUP_RUNS_PER_DAY = 4

    def _maybe_catchup(self):
        """While the user is active (so the Mac is truly awake), rerun any
        recent nightly stage that failed or never ran."""
        now = time.time()
        if now - self._last_catchup_check < self.CATCHUP_CHECK_SEC:
            return
        self._last_catchup_check = now
        if self._catchup_proc and self._catchup_proc.poll() is None:
            return
        today = date.today()
        if self._catchup_runs.get(today, 0) >= self.MAX_CATCHUP_RUNS_PER_DAY:
            return

        from export.catchup import pending_days
        pending = pending_days(today)
        if not pending:
            return
        self._catchup_runs[today] = self._catchup_runs.get(today, 0) + 1
        log.info("Catch-up needed: %s", {d.isoformat(): sorted(m) for d, m in pending.items()})
        log_path = Path.home() / "Library" / "Logs" / "retra-catchup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as out:
            self._catchup_proc = subprocess.Popen(
                [sys.executable, str(PROJECT_ROOT / "main.py"), "catchup"],
                cwd=PROJECT_ROOT, stdout=out, stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def _write_pid(self):
        """Write PID file for process management."""
        PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        PID_PATH.write_text(str(os.getpid()))

    def _is_blocked(self, window: ActiveWindow) -> bool:
        """Check if the app or URL should be blocked from tracking."""
        privacy = self.settings.privacy

        # Check blocked apps
        for blocked_app in privacy.blocked_apps:
            if blocked_app.lower() in window.app_name.lower():
                return True

        # Check blocked URL patterns in window title
        for pattern in privacy.blocked_url_patterns:
            if pattern.lower() in window.window_title.lower():
                return True

        return False

    def _should_capture_screenshot(self) -> bool:
        """Check if enough time has passed for next screenshot."""
        now = time.time()
        interval = self.settings.capture.screenshot_interval
        return (now - self.last_screenshot_time) >= interval

    _BROWSERS = {"Safari", "Chrome", "Google Chrome", "Firefox", "Arc", "Brave Browser", "Microsoft Edge"}

    def _enrich_browser_window(self, window: ActiveWindow) -> ActiveWindow:
        """For browsers, fetch the actual URL and enrich the window title with domain."""
        if window.app_name not in self._BROWSERS:
            return window

        url = get_browser_url_applescript(window.app_name)
        if not url:
            return window

        # Check privacy blocklist
        for pattern in self.settings.privacy.blocked_url_patterns:
            if pattern.lower() in url.lower():
                # Title may look harmless, but the page itself is private.
                self._window_blocked = True
                return window

        domain = extract_domain(url)

        # Build a richer title: "Page Title [domain.com]"
        title = window.window_title
        if domain and domain not in title.lower():
            title = f"{title} [{domain}]" if title else f"[{domain}]"

        return ActiveWindow(
            app_name=window.app_name,
            window_title=title,
            bundle_id=window.bundle_id,
            url=url,
            window_id=window.window_id,
        )

    def _capture_cycle(self) -> Optional[ActiveWindow]:
        """
        Run one full capture cycle. Returns the active window (or None).
        Single get_active_window() call per cycle.
        """
        # Check screen lock first (cheap check)
        if is_screen_locked():
            return None

        # Check idle
        is_idle = is_user_idle(self.settings.capture.idle_threshold)
        if is_idle:
            event = WindowEvent(
                timestamp=datetime.now(),
                app_name="Idle",
                window_title="",
                category="idle",
                is_idle=True,
            )
            self.db.insert_event(event)
            return None

        # Get active window — ONCE per cycle
        window = get_active_window()
        if not window:
            return None

        # Skip incognito/private browser windows entirely — no events, URLs, or screenshots
        if is_incognito(window):
            return None

        # Check privacy blocklist
        blocked = self._is_blocked(window)
        self._window_blocked = blocked
        if blocked:
            window = ActiveWindow(
                app_name=window.app_name,
                window_title="[blocked]",
                bundle_id=window.bundle_id,
                window_id=window.window_id,
            )
        else:
            # Enrich browser windows with URL/domain
            window = self._enrich_browser_window(window)

        # Categorize (now using enriched URL for better browser categorization)
        category = self.settings.categories.categorize(
            window.app_name, window.window_title, window.url or ""
        )

        now = datetime.now()
        time_since_last = (
            (now - self.last_event_time).total_seconds()
            if self.last_event_time else float("inf")
        )

        is_same_window = (
            self.last_window
            and window.app_name == self.last_window.app_name
            and window.window_title == self.last_window.window_title
        )

        # Record event if:
        # - Window changed (new app or new title)
        # - OR it's been >60s since last event (continuation heartbeat to keep sessions connected)
        should_record = not is_same_window or time_since_last >= 60

        if should_record:
            # Best-effort project signal. Title/URL only — no extra
            # AppleScript calls beyond what we already made for browser URL.
            # Never for blocked windows: the hint lookup can read a terminal's
            # cwd and would persist the private repo name.
            project_hint = None if blocked else get_project_hint(window)
            event = WindowEvent(
                timestamp=now,
                app_name=window.app_name,
                window_title=window.window_title[:self.settings.capture.max_title_length],
                category=category,
                bundle_id=window.bundle_id,
                url=window.url,
                is_idle=False,
                project_hint=project_hint,
            )
            self.db.insert_event(event)
            self._events_since_start += 1
            self.last_window = window
            self.last_event_time = now
            log.debug(
                f"{window.app_name} | {window.window_title[:60]} [{category}]"
                + (f" → {project_hint}" if project_hint else "")
            )

        return window

    def _capture_url(self, window: ActiveWindow):
        """Store URL event for browser tracking (domain stats, time-on-site)."""
        if window.app_name not in self._BROWSERS or not window.url:
            return

        url = window.url
        now = datetime.now()

        # Skip if same URL as last time (within 30s)
        if (
            self.last_url == url
            and self.last_url_time
            and (now - self.last_url_time).total_seconds() < 30
        ):
            return

        # Compute duration from last URL event
        duration = 0
        if self.last_url_time:
            duration = int((now - self.last_url_time).total_seconds())
            duration = min(duration, self.settings.capture.poll_interval * 10)

        domain = extract_domain(url)
        category = self.settings.categories.categorize(
            window.app_name, window.window_title, url
        )

        self.db.insert_url_event(
            timestamp=now,
            url=url,
            domain=domain,
            page_title=window.window_title,
            app_name=window.app_name,
            category=category,
            duration_seconds=duration,
        )

        self.last_url = url
        self.last_url_time = now

    def _screenshot_skip_reason(self, window: ActiveWindow) -> Optional[str]:
        """Why this window must not be screenshotted right now, if anything."""
        if self._window_blocked:
            return "blocked app/URL"
        app = window.app_name.lower()
        if any(a.lower() in app for a in self.settings.privacy.screenshot_skip_apps):
            return "private app"
        if is_secure_input_active():
            return "password field active"
        if not window.window_id:
            return "no window id"
        return None

    def _capture_screenshot(self, window: ActiveWindow):
        """Capture and store a screenshot of the active window. Skips if the
        window is private or the screen is likely unchanged."""
        app = window.app_name
        title = window.window_title

        skip = self._screenshot_skip_reason(window)
        if skip:
            log.debug(f"Screenshot skipped ({skip})")
            self.last_screenshot_time = time.time()
            # Forget the dedup key so returning to the last window captures again
            self._last_screenshot_app = self._last_screenshot_title = None
            return

        # Dedup: skip if same app + window title as last screenshot
        # (screen is very likely identical)
        if (
            self._last_screenshot_app == app
            and self._last_screenshot_title == title
            and app  # don't skip if we have no window info
        ):
            log.debug("Screenshot skipped (same app+title as last)")
            self.last_screenshot_time = time.time()
            return

        filepath = self.screenshotter.capture(window.window_id)
        if filepath:
            screenshot = Screenshot(
                timestamp=datetime.now(),
                filepath=filepath,
                app_name=app,
                window_title=title,
            )
            screenshot_id = self.db.insert_screenshot(screenshot)
            self._ocr_pool.submit(
                ocr_screenshot, self.db, screenshot_id, filepath, self.screenshotter.compress
            )
            self._screenshots_since_start += 1
            self._last_screenshot_app = app
            self._last_screenshot_title = title
            self.last_screenshot_time = time.time()
            log.debug(f"Screenshot saved: {filepath}")

    def run(self):
        """Main capture loop."""
        self.running = True
        self._start_time = time.time()
        self._write_pid()
        log.info("Retra capture daemon started (PID %d)", os.getpid())
        log.info(f"  Poll interval: {self.settings.capture.poll_interval}s")
        log.info(f"  Screenshot interval: {self.settings.capture.screenshot_interval}s")
        log.info(f"  Idle threshold: {self.settings.capture.idle_threshold}s")
        log.info(f"  Database: {DB_PATH}")

        self._run_maintenance()

        while self.running:
            try:
                # Single capture cycle: window event + returns the active window
                window = self._capture_cycle()

                # Capture browser URL (reuses window from above — no extra call)
                if window:
                    self._capture_url(window)

                # Capture screenshot if interval elapsed
                if self._should_capture_screenshot() and window:
                    self._capture_screenshot(window)

                # Write heartbeat every ~15s (not every 3s poll cycle)
                now = time.time()
                if now - self._last_heartbeat_time >= 15:
                    self._write_heartbeat()
                    self._last_heartbeat_time = now

                # Daemon can stay up for months, so backup/cleanup can't
                # only run on startup.
                if date.today() != self._last_maintenance_date:
                    self._run_maintenance()

                if window:
                    self._maybe_catchup()

            except Exception as e:
                log.error(f"Capture error: {e}")

            time.sleep(self.settings.capture.poll_interval)

        # Let an in-flight OCR finish so its row isn't left half-done.
        self._ocr_pool.shutdown(wait=True, cancel_futures=True)
        log.info("Capture daemon stopped")


def run_daemon():
    """Entry point for the capture daemon."""
    daemon = CaptureDaemon()
    daemon.run()
