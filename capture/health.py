"""
Retra Health Check
Verifies that the capture daemon is running and recording properly.
"""

import json
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config.settings import DB_PATH, HEARTBEAT_PATH, PID_PATH


@dataclass
class HealthStatus:
    daemon_running: bool
    daemon_pid: Optional[int]
    uptime_seconds: Optional[int]
    last_heartbeat: Optional[datetime]
    heartbeat_age_seconds: Optional[float]
    events_captured: Optional[int]
    screenshots_captured: Optional[int]
    last_event_time: Optional[datetime]
    last_screenshot_time: Optional[datetime]
    db_exists: bool
    db_size_mb: float

    @property
    def recording_ok(self) -> bool:
        """True if daemon is alive and heartbeat is fresh (< 45s old).
        Heartbeat writes every ~15s, so 45s allows for 2 missed beats."""
        return self.daemon_running and (
            self.heartbeat_age_seconds is not None
            and self.heartbeat_age_seconds < 45
        )

    @property
    def events_flowing(self) -> bool:
        """True if events have been recorded in the last 2 minutes."""
        if not self.last_event_time:
            return False
        age = (datetime.now() - self.last_event_time).total_seconds()
        return age < 120

    @property
    def screenshots_flowing(self) -> bool:
        """True if a screenshot was taken in the last 5 minutes."""
        if not self.last_screenshot_time:
            return False
        age = (datetime.now() - self.last_screenshot_time).total_seconds()
        return age < 300

    def summary_lines(self) -> list[str]:
        """Return human-readable status lines."""
        lines = []

        # Daemon status
        if self.daemon_running:
            lines.append(f"  Daemon:       RUNNING (PID {self.daemon_pid})")
            if self.uptime_seconds is not None:
                h, rem = divmod(self.uptime_seconds, 3600)
                m, s = divmod(rem, 60)
                lines.append(f"  Uptime:       {h}h {m}m {s}s")
        else:
            lines.append("  Daemon:       NOT RUNNING")

        # Heartbeat
        if self.heartbeat_age_seconds is not None:
            age = int(self.heartbeat_age_seconds)
            if age < 10:
                lines.append(f"  Heartbeat:    OK ({age}s ago)")
            else:
                lines.append(f"  Heartbeat:    STALE ({age}s ago)")
        else:
            lines.append("  Heartbeat:    NO DATA")

        # Events
        if self.last_event_time:
            age = int((datetime.now() - self.last_event_time).total_seconds())
            if age < 60:
                lines.append(f"  Last Event:   {age}s ago")
            else:
                lines.append(f"  Last Event:   {age // 60}m ago")
        else:
            lines.append("  Last Event:   NONE")

        # Screenshots
        if self.last_screenshot_time:
            age = int((datetime.now() - self.last_screenshot_time).total_seconds())
            if age < 60:
                lines.append(f"  Last Screenshot: {age}s ago")
            else:
                lines.append(f"  Last Screenshot: {age // 60}m ago")
        else:
            lines.append("  Last Screenshot: NONE")

        # Totals from heartbeat
        if self.events_captured is not None:
            lines.append(f"  Events (session): {self.events_captured}")
        if self.screenshots_captured is not None:
            lines.append(f"  Screenshots (session): {self.screenshots_captured}")

        # DB
        lines.append(f"  Database:     {'OK' if self.db_exists else 'MISSING'} ({self.db_size_mb:.1f} MB)")

        return lines


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)  # Signal 0 = existence check, no actual signal sent
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but we can't signal it


def get_health() -> HealthStatus:
    """Run a full health check on the Retra capture system."""
    daemon_running = False
    daemon_pid = None
    uptime_seconds = None
    last_heartbeat = None
    heartbeat_age = None
    events_captured = None
    screenshots_captured = None

    # Check PID file
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text().strip())
            if _is_pid_alive(pid):
                daemon_running = True
                daemon_pid = pid
        except (ValueError, OSError):
            pass

    # Check heartbeat file
    if HEARTBEAT_PATH.exists():
        try:
            data = json.loads(HEARTBEAT_PATH.read_text())
            last_heartbeat = datetime.fromisoformat(data["timestamp"])
            heartbeat_age = (datetime.now() - last_heartbeat).total_seconds()
            events_captured = data.get("events_captured")
            screenshots_captured = data.get("screenshots_captured")
            uptime_seconds = data.get("uptime_seconds")

            # Cross-check PID from heartbeat
            hb_pid = data.get("pid")
            if hb_pid and _is_pid_alive(hb_pid):
                daemon_running = True
                daemon_pid = hb_pid
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    # Check database
    db_exists = Path(DB_PATH).exists()
    db_size_mb = Path(DB_PATH).stat().st_size / (1024 * 1024) if db_exists else 0.0

    # Query last event and screenshot times from DB
    last_event_time = None
    last_screenshot_time = None
    if db_exists:
        import sqlite3
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute(
                "SELECT MAX(timestamp) FROM window_events"
            ).fetchone()
            if row and row[0]:
                last_event_time = datetime.fromisoformat(row[0])

            row = conn.execute(
                "SELECT MAX(timestamp) FROM screenshots"
            ).fetchone()
            if row and row[0]:
                last_screenshot_time = datetime.fromisoformat(row[0])
            conn.close()
        except Exception:
            pass

    return HealthStatus(
        daemon_running=daemon_running,
        daemon_pid=daemon_pid,
        uptime_seconds=uptime_seconds,
        last_heartbeat=last_heartbeat,
        heartbeat_age_seconds=heartbeat_age,
        events_captured=events_captured,
        screenshots_captured=screenshots_captured,
        last_event_time=last_event_time,
        last_screenshot_time=last_screenshot_time,
        db_exists=db_exists,
        db_size_mb=db_size_mb,
    )
