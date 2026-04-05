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
from datetime import datetime
from pathlib import Path
from typing import Optional

from capture.window_tracker import get_active_window, is_screen_locked, ActiveWindow, get_browser_url_applescript, extract_domain
from capture.screenshot import ScreenshotCapture
from capture.idle_detector import is_user_idle
from storage.database import Database
from storage.models import WindowEvent, Screenshot
from config.settings import get_settings, DB_PATH, SCREENSHOTS_DIR, HEARTBEAT_PATH, PID_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Retra] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("retra")


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

        self._events_since_start = 0
        self._screenshots_since_start = 0
        self._last_heartbeat_time: float = 0

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

        # Check privacy blocklist
        if self._is_blocked(window):
            window = ActiveWindow(
                app_name=window.app_name,
                window_title="[blocked]",
                bundle_id=window.bundle_id,
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
            event = WindowEvent(
                timestamp=now,
                app_name=window.app_name,
                window_title=window.window_title[:self.settings.capture.max_title_length],
                category=category,
                bundle_id=window.bundle_id,
                url=window.url,
                is_idle=False,
            )
            self.db.insert_event(event)
            self._events_since_start += 1
            self.last_window = window
            self.last_event_time = now
            log.debug(f"{window.app_name} | {window.window_title[:60]} [{category}]")

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

    def _capture_screenshot(self, window: Optional[ActiveWindow] = None):
        """Capture and store a screenshot. Skips if screen likely unchanged."""
        app = window.app_name if window else ""
        title = window.window_title if window else ""

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

        filepath = self.screenshotter.capture()
        if filepath:
            screenshot = Screenshot(
                timestamp=datetime.now(),
                filepath=filepath,
                app_name=app,
                window_title=title,
            )
            self.db.insert_screenshot(screenshot)
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

        # Auto-backup database on startup
        try:
            import shutil
            backup_path = DB_PATH.parent / f"{DB_PATH.stem}.backup.db"
            shutil.copy2(DB_PATH, backup_path)
            log.info(f"Database backed up to {backup_path}")
        except Exception as e:
            log.warning(f"Backup failed (non-fatal): {e}")

        # Auto-cleanup old data on startup
        try:
            self.db.cleanup_old_data(self.settings.privacy.retention_days)
            self.screenshotter.cleanup_old(self.settings.privacy.retention_days)
            log.info("Old data cleanup complete (retention: %dd)", self.settings.privacy.retention_days)
        except Exception as e:
            log.warning(f"Cleanup failed (non-fatal): {e}")

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

            except Exception as e:
                log.error(f"Capture error: {e}")

            time.sleep(self.settings.capture.poll_interval)

        log.info("Capture daemon stopped")


def run_daemon():
    """Entry point for the capture daemon."""
    daemon = CaptureDaemon()
    daemon.run()
