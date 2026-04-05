"""
Retra Idle Detector
Detects user inactivity via macOS HID idle time.
"""

import subprocess
from typing import Optional


def get_idle_seconds() -> Optional[float]:
    """
    Get the number of seconds since last user input (keyboard/mouse).
    Uses macOS ioreg to read HIDIdleTime from IOHIDSystem.
    """
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "HIDIdleTime" in line:
                    # Value is in nanoseconds
                    parts = line.split("=")
                    if len(parts) >= 2:
                        ns = int(parts[-1].strip())
                        return ns / 1_000_000_000  # convert to seconds
    except (subprocess.TimeoutExpired, ValueError, Exception):
        pass

    return None


def is_user_idle(threshold_seconds: int = 300) -> bool:
    """Check if the user has been idle longer than the threshold."""
    idle = get_idle_seconds()
    if idle is None:
        return False  # assume active if we can't determine
    return idle >= threshold_seconds
