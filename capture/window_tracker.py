"""
Retra Window Tracker
Uses macOS Accessibility APIs via pyobjc to capture the active window.
Requires Accessibility permission in System Preferences.
"""

import os
import subprocess
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ActiveWindow:
    app_name: str
    window_title: str
    bundle_id: Optional[str] = None
    url: Optional[str] = None
    # CGWindowID, used to screenshot just this window. None from the
    # AppleScript fallback, which can't see window ids.
    window_id: Optional[int] = None


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
            window_id = window.get("kCGWindowNumber")

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
                window_id=window_id,
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


# ── Project hint detection ────────────────────────────────────────
#
# Best-effort: attach a project slug ("retra", "owner/repo", etc.) to each
# event so the attribution pass has anchor points. Cheap and silent — if we
# can't tell, we return None and let the attribution layer infer from
# context. NEVER does subprocess work in the hot path beyond what
# window_tracker already does for browser URLs.

_TERMINAL_APPS = {
    "iTerm2", "iTerm", "Terminal", "Ghostty", "Warp", "WarpPreview",
    "Alacritty", "Hyper", "kitty", "Tabby",
}
_EDITOR_APPS = {
    "Code", "Cursor", "VS Code", "Xcode", "PyCharm", "IntelliJ IDEA",
    "WebStorm", "PhpStorm", "GoLand", "RustRover", "CLion", "DataGrip",
    "Vim", "MacVim", "Neovim", "Zed", "Sublime Text", "Nova",
}

# Direct-child-of-this-dir-is-a-project (lowercased path segment match)
_DEV_PARENT_DIRS = {
    "code", "projects", "project", "repos", "repo", "src", "work",
    "dev", "github", "src", "Documents/work",
}

# Cache cwd → project slug so we don't re-resolve identical paths.
# Bounded by the number of distinct cwds the user works in (small).
_PROJECT_CACHE: dict[str, Optional[str]] = {}

# Match a path-shaped substring inside a window title.
# Accepts ~/foo/bar or /Users/x/foo/bar or /home/x/foo.
_PATH_IN_TITLE_RE = re.compile(
    r'(~(?:/[\w.\-_]+)+|'                           # ~/foo/bar
    r'/(?:Users|home)/[\w.\-_]+(?:/[\w.\-_]+)+)'    # /Users/x/foo
)

# Editor title pattern "filename.ext — project-slug" / "filename.ext - project"
# Mirrors summarizer.detect_projects's editor pattern.
_EDITOR_PROJECT_RE = re.compile(
    r'\b[\w-]+\.[a-zA-Z0-9]{1,6}\s*[—\-]\s*'
    r'([a-z][a-z0-9]*(?:[-_.][a-z0-9]+)+|[a-z]{4,}-[a-z0-9-]+)',
    re.IGNORECASE,
)

# Apps whose name itself should never be treated as a project slug.
_NEVER_A_PROJECT = {
    "home", "desktop", "downloads", "documents", "library", "applications",
    "users", "tmp", "var", "etc", "opt",
}


def _cwd_to_project_slug(cwd: str) -> Optional[str]:
    """Resolve a cwd string to a project slug, with caching."""
    if cwd in _PROJECT_CACHE:
        return _PROJECT_CACHE[cwd]

    try:
        expanded = os.path.expanduser(cwd)
        p = Path(expanded)
    except Exception:
        _PROJECT_CACHE[cwd] = None
        return None

    slug: Optional[str] = None

    # 1. Walk up looking for a .git directory; its parent is the repo root.
    try:
        for parent in [p, *p.parents]:
            git_marker = parent / ".git"
            if git_marker.exists():
                slug = parent.name
                break
    except Exception:
        pass

    # 2. Fall back: any path segment that's a known dev parent dir → the
    #    NEXT segment is the project.
    if not slug:
        parts = list(p.parts)
        lowered = [s.lower() for s in parts]
        for i, seg in enumerate(lowered):
            if seg in _DEV_PARENT_DIRS and i + 1 < len(parts):
                slug = parts[i + 1]
                break

    # 3. ~/Desktop/<slug> only counts if it has a .git (covered by step 1)
    #    or a package.json / Cargo.toml / pyproject.toml etc.
    if not slug:
        try:
            if len(p.parts) >= 4 and p.parts[-2].lower() == "desktop":
                markers = ("package.json", "Cargo.toml", "pyproject.toml",
                           "go.mod", "Gemfile", "requirements.txt")
                if any((p / m).exists() for m in markers):
                    slug = p.name
        except Exception:
            pass

    if slug and slug.lower() in _NEVER_A_PROJECT:
        slug = None

    _PROJECT_CACHE[cwd] = slug
    return slug


def _extract_cwd_from_title(title: str) -> Optional[str]:
    if not title:
        return None
    m = _PATH_IN_TITLE_RE.search(title)
    return m.group(1) if m else None


def _url_to_project(url: str) -> Optional[str]:
    """Pull a project slug from a code-hosting URL."""
    if not url:
        return None
    for host in ("github.com", "gitlab.com", "bitbucket.org"):
        m = re.search(rf'{re.escape(host)}/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)', url)
        if m:
            slug = m.group(1).rstrip("/")
            if slug.endswith(".git"):
                slug = slug[:-4]
            # Drop owner/ for a short display slug — matches existing
            # detect_projects convention for the dashboard.
            return slug.split("/")[-1]
    return None


# Lazy allow-list of single-word slugs that match actual repos on disk.
# Lets us accept "database.py — daylens" without false-positive risk on
# "Untitled — YouTube". Scanned once at first use.
_KNOWN_PROJECT_SLUGS: Optional[set[str]] = None


def _known_project_slugs() -> set[str]:
    global _KNOWN_PROJECT_SLUGS
    if _KNOWN_PROJECT_SLUGS is not None:
        return _KNOWN_PROJECT_SLUGS
    slugs: set[str] = set()
    home = Path.home()
    parents = [home / d for d in ("Desktop", "code", "projects", "work",
                                  "Documents/work", "dev", "src")]
    repo_markers = (".git", "package.json", "pyproject.toml", "Cargo.toml",
                    "go.mod", "Gemfile")
    for parent in parents:
        if not parent.is_dir():
            continue
        try:
            for child in parent.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if any((child / m).exists() for m in repo_markers):
                    slugs.add(child.name.lower())
        except (PermissionError, OSError):
            continue
    _KNOWN_PROJECT_SLUGS = slugs
    return slugs


# Looser editor pattern for single-word slugs: only accepted when the slug
# is in the known-projects allow-list.
_EDITOR_PROJECT_LOOSE_RE = re.compile(
    r'\b[\w-]+\.[a-zA-Z0-9]{1,6}\s*[—\-]\s*([a-z][a-z0-9]{2,})\b',
    re.IGNORECASE,
)


def _editor_title_to_project(title: str) -> Optional[str]:
    if not title:
        return None
    # Strict pattern: multi-segment slug — always trusted.
    m = _EDITOR_PROJECT_RE.search(title)
    if m:
        return m.group(1).strip()
    # Loose pattern: single-word slug — only if it's a real local repo.
    m2 = _EDITOR_PROJECT_LOOSE_RE.search(title)
    if m2:
        candidate = m2.group(1).strip().lower()
        if candidate in _known_project_slugs() and candidate not in _NEVER_A_PROJECT:
            return candidate
    return None


def _iterm2_cwd_applescript() -> Optional[str]:
    """Query iTerm2's current session for its working directory.

    Returns the cwd as an absolute path, or None if iTerm2 isn't running, no
    session is open, or shell integration isn't installed (in which case
    `session.path` is unset and the script errors).

    iTerm2 ships shell integration that maintains the variable cheaply via
    OSC sequences. This call typically completes in ~80–150ms.
    """
    script = '''
    tell application "iTerm"
        if (count of windows) > 0 then
            try
                tell current session of current window
                    return variable named "session.path"
                end tell
            on error
                return ""
            end try
        end if
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            cwd = result.stdout.strip()
            if cwd.startswith("/"):
                return cwd
    except (subprocess.TimeoutExpired, Exception):
        pass
    return None


def project_hint_from_title_url(
    app_name: str, window_title: str, url: Optional[str] = None
) -> Optional[str]:
    """
    Pure post-hoc project extraction. Same logic as get_project_hint
    minus any subprocess/AppleScript calls. Used by the attribution
    pass to label historical events that were captured before the
    project_hint column existed.
    """
    app = app_name or ""
    title = window_title or ""

    if app in _BROWSERS and url:
        proj = _url_to_project(url)
        if proj:
            return proj

    if app in _TERMINAL_APPS:
        cwd = _extract_cwd_from_title(title)
        if cwd:
            slug = _cwd_to_project_slug(cwd)
            if slug:
                return slug

    if app in _EDITOR_APPS:
        slug = _editor_title_to_project(title)
        if slug:
            return slug
        cwd = _extract_cwd_from_title(title)
        if cwd:
            slug = _cwd_to_project_slug(cwd)
            if slug:
                return slug

    return None


def get_project_hint(window: "ActiveWindow") -> Optional[str]:
    """
    Live project hint for an active window (capture-time use).

    Strategy: try the pure title/URL path first. For iTerm2 specifically,
    fall back to AppleScript when the title doesn't expose a path —
    common when Claude Code, fish, or anything else has hijacked the
    title.
    """
    if not window:
        return None

    proj = project_hint_from_title_url(
        window.app_name, window.window_title, window.url
    )
    if proj:
        return proj

    # AppleScript fallback for iTerm2. ~100ms IPC, only when we need it.
    if window.app_name in ("iTerm2", "iTerm"):
        cwd = _iterm2_cwd_applescript()
        if cwd:
            slug = _cwd_to_project_slug(cwd)
            if slug:
                return slug

    return None


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


_carbon = None


def is_secure_input_active() -> bool:
    """True while some app has Secure Event Input on, which macOS does while a
    password field has focus (and some apps, like Terminal's Secure Keyboard
    Entry, leave on). Treated as "a password may be on screen"."""
    global _carbon
    try:
        if _carbon is None:
            import ctypes
            _carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
            _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:
        return False
