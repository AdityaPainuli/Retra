"""
Retra Window Tracker
Uses macOS Accessibility APIs via pyobjc to capture the active window.
Requires Accessibility permission in System Preferences.
"""

import subprocess
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ActiveWindow:
    app_name: str
    window_title: str
    bundle_id: Optional[str] = None
    url: Optional[str] = None


def get_active_window_applescript() -> Optional[ActiveWindow]:
    """
    Fallback: Use AppleScript to get the frontmost app and window title.
    More compatible but slightly slower than direct API access.
    """
    script = '''
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set appName to name of frontApp
        set bundleID to bundle identifier of frontApp

        set winTitle to ""
        try
            tell frontApp
                set winTitle to name of front window
            end tell
        end try

        return appName & "|||" & bundleID & "|||" & winTitle
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and "|||" in result.stdout:
            parts = result.stdout.strip().split("|||")
            app_name = parts[0].strip()
            bundle_id = parts[1].strip() if len(parts) > 1 else None
            title = parts[2].strip() if len(parts) > 2 else ""

            # Try to extract URL from browser titles
            url = _extract_url_from_title(app_name, title)

            return ActiveWindow(
                app_name=app_name,
                window_title=title,
                bundle_id=bundle_id,
                url=url,
            )
    except (subprocess.TimeoutExpired, Exception):
        pass

    return None


def get_active_window_pyobjc() -> Optional[ActiveWindow]:
    """
    Primary: Use CGWindowListCopyWindowInfo to find the frontmost window.

    NOTE: We do NOT use NSWorkspace.frontmostApplication() because it requires
    an NSRunLoop to receive app-switch notifications. Background daemon processes
    don't have one, so it returns stale data (whatever app was active at launch).

    Instead, we query the window server directly via CGWindowListCopyWindowInfo.
    The first on-screen, normal-layer (layer 0) window in the list is the
    frontmost — the window server returns them in front-to-back z-order.
    """
    try:
        from AppKit import NSWorkspace, NSRunningApplication
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
        )

        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID)

        if not window_list:
            return None

        # Find the topmost normal window (layer 0 = normal app windows)
        for window in window_list:
            layer = window.get("kCGWindowLayer", -1)
            if layer != 0:
                continue

            owner_name = window.get("kCGWindowOwnerName", "")
            window_title = window.get("kCGWindowName", "")
            owner_pid = window.get("kCGWindowOwnerPID", 0)

            if not owner_name:
                continue

            # Look up bundle ID from the running app
            bundle_id = ""
            try:
                running_app = NSRunningApplication.runningApplicationWithProcessIdentifier_(owner_pid)
                if running_app:
                    bundle_id = running_app.bundleIdentifier() or ""
            except Exception:
                pass

            url = _extract_url_from_title(owner_name, window_title)

            return ActiveWindow(
                app_name=owner_name,
                window_title=window_title,
                bundle_id=bundle_id,
                url=url,
            )

        return None

    except ImportError:
        # pyobjc not available, fall back
        return get_active_window_applescript()
    except Exception:
        return None


def get_active_window() -> Optional[ActiveWindow]:
    """Get the currently active window. Tries pyobjc first, then AppleScript."""
    result = get_active_window_pyobjc()
    if result:
        return result
    return get_active_window_applescript()


_BROWSERS = {"Safari", "Chrome", "Google Chrome", "Firefox", "Arc", "Brave Browser", "Microsoft Edge"}

# Keywords browsers put in window titles for private/incognito mode
_INCOGNITO_TITLE_HINTS = [
    "(incognito)",      # Chrome
    "(private)",        # Firefox
    "private browsing", # Safari, Firefox
    "(inprivate)",      # Edge
]


def is_incognito(window: ActiveWindow) -> bool:
    """
    Detect if a browser window is in incognito / private browsing mode.

    Uses two strategies:
    1. Window title keywords (works for all browsers, no extra AppleScript call)
    2. AppleScript mode check for Chromium-based browsers (definitive answer)
    """
    if window.app_name not in _BROWSERS:
        return False

    # Strategy 1: check window title for incognito hints (fast, no subprocess)
    title_lower = window.window_title.lower()
    for hint in _INCOGNITO_TITLE_HINTS:
        if hint in title_lower:
            return True

    # Strategy 2: AppleScript check for Chromium browsers
    # Chrome/Brave/Arc/Edge expose "mode" on each window
    chrome_based = {"Google Chrome", "Chrome", "Brave Browser", "Arc", "Microsoft Edge", "Chromium"}
    if window.app_name in chrome_based:
        try:
            script = f'''
            tell application "{window.app_name}"
                if (count of windows) > 0 then
                    return mode of front window
                end if
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                mode = result.stdout.strip().lower()
                if "incognito" in mode:
                    return True
        except (subprocess.TimeoutExpired, Exception):
            pass

    # Strategy 2b: Safari private window check
    if window.app_name == "Safari":
        try:
            script = '''
            tell application "Safari"
                if (count of windows) > 0 then
                    return name of front document
                end if
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            # Safari returns an error or empty for private windows with no document
            if result.returncode != 0 or not result.stdout.strip():
                # Likely a private window — also check title
                if "private" in title_lower:
                    return True
        except (subprocess.TimeoutExpired, Exception):
            pass

    return False


def _extract_url_from_title(app_name: str, title: str) -> Optional[str]:
    """
    Extract a URL from browser window titles.
    Many browsers include the page title or URL in the window name.
    """
    browsers = {"Safari", "Chrome", "Google Chrome", "Firefox", "Arc", "Brave Browser", "Microsoft Edge"}
    if app_name not in browsers:
        return None

    # Some browser extensions show URLs in title
    url_pattern = r'https?://[^\s]+'
    match = re.search(url_pattern, title)
    if match:
        return match.group(0)

    return None


def get_browser_url_applescript(app_name: str) -> Optional[str]:
    """
    Use AppleScript to get the active URL from a browser.
    Works for Safari, Chrome, Arc, Brave, and Firefox.
    """
    chrome_based = {"Google Chrome", "Chrome", "Brave Browser", "Arc", "Microsoft Edge", "Chromium"}
    if app_name in chrome_based:
        script = f'''
        tell application "{app_name}"
            if (count of windows) > 0 then
                return URL of active tab of front window
            end if
        end tell
        '''
    elif app_name == "Safari":
        script = '''
        tell application "Safari"
            if (count of windows) > 0 then
                return URL of current tab of front window
            end if
        end tell
        '''
    elif app_name == "Firefox":
        # Firefox doesn't expose URL via AppleScript easily
        return None
    else:
        return None

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            if url.startswith("http"):
                return url
    except (subprocess.TimeoutExpired, Exception):
        pass

    return None


def extract_domain(url: str) -> str:
    """Extract the domain from a URL."""
    # Simple domain extraction without urllib to keep it fast
    try:
        # Remove protocol
        domain = url.split("://", 1)[1] if "://" in url else url
        # Remove path
        domain = domain.split("/")[0]
        # Remove port
        domain = domain.split(":")[0]
        # Remove www.
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except (IndexError, AttributeError):
        return url


def is_screen_locked() -> bool:
    """Check if the macOS screen is locked."""
    try:
        from Quartz import CGSessionCopyCurrentDictionary
        session = CGSessionCopyCurrentDictionary()
        if session:
            return session.get("CGSSessionScreenIsLocked", False)
    except ImportError:
        # Fallback: check via ioreg
        try:
            result = subprocess.run(
                ["ioreg", "-n", "Root", "-d1", "-a"],
                capture_output=True, text=True, timeout=3
            )
            return "CGSSessionScreenIsLocked" in result.stdout
        except Exception:
            pass
    return False
