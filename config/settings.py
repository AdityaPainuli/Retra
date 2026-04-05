"""
Retra Configuration
Loads settings from TOML config file with sensible defaults.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Optional

CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent

# Load .env file if present (for background processes that don't inherit shell env)
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and val and key not in os.environ:
                os.environ[key] = val
DATA_DIR = PROJECT_ROOT / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
DB_PATH = DATA_DIR / "retra.db"
HEARTBEAT_PATH = DATA_DIR / "daemon.heartbeat"
PID_PATH = DATA_DIR / "daemon.pid"


@dataclass
class CaptureConfig:
    poll_interval: int = 3
    screenshot_interval: int = 180
    idle_threshold: int = 300
    screenshot_quality: int = 40
    max_title_length: int = 500


@dataclass
class PrivacyConfig:
    blocked_apps: list[str] = field(default_factory=lambda: ["1Password", "Keychain Access"])
    blocked_url_patterns: list[str] = field(default_factory=lambda: ["bank", "chase.com"])
    blur_screenshots: bool = False
    retention_days: int = 90


@dataclass
class CategoryConfig:
    coding: list[str] = field(default_factory=lambda: [
        "VS Code", "Code", "Xcode", "Terminal", "iTerm", "PyCharm",
        "IntelliJ", "WebStorm", "Cursor", "Warp", "Alacritty", "Vim", "Neovim"
    ])
    communication: list[str] = field(default_factory=lambda: [
        "Slack", "Discord", "Messages", "Zoom", "Teams", "Telegram", "WhatsApp"
    ])
    browsing: list[str] = field(default_factory=lambda: [
        "Safari", "Chrome", "Firefox", "Arc", "Brave", "Edge"
    ])
    entertainment: list[str] = field(default_factory=lambda: [
        "Spotify", "Music", "VLC", "IINA", "Podcasts"
    ])
    writing: list[str] = field(default_factory=lambda: [
        "Obsidian", "Notion", "Bear", "Notes", "Craft", "Pages", "Word"
    ])
    learning: list[str] = field(default_factory=lambda: [
        "Anki", "Kindle", "Books"
    ])

    # Domain-to-category mapping for URL-based categorization
    DOMAIN_CATEGORIES: ClassVar[dict[str, str]] = {
        # Entertainment
        "youtube.com": "entertainment", "netflix.com": "entertainment",
        "twitch.tv": "entertainment", "reddit.com": "entertainment",
        "twitter.com": "entertainment", "x.com": "entertainment",
        "instagram.com": "entertainment", "tiktok.com": "entertainment",
        "facebook.com": "entertainment", "9gag.com": "entertainment",
        "news.ycombinator.com": "entertainment", "imgur.com": "entertainment",
        "spotify.com": "entertainment", "soundcloud.com": "entertainment",
        # Coding
        "github.com": "coding", "gitlab.com": "coding",
        "stackoverflow.com": "coding", "stackexchange.com": "coding",
        "npmjs.com": "coding", "pypi.org": "coding",
        "crates.io": "coding", "docs.python.org": "coding",
        "developer.mozilla.org": "coding", "devdocs.io": "coding",
        "pkg.go.dev": "coding", "docs.rs": "coding",
        "vercel.com": "coding", "netlify.com": "coding",
        "hub.docker.com": "coding", "bitbucket.org": "coding",
        # Learning
        "coursera.org": "learning", "udemy.com": "learning",
        "khanacademy.org": "learning", "edx.org": "learning",
        "arxiv.org": "learning", "scholar.google.com": "learning",
        "wikipedia.org": "learning", "en.wikipedia.org": "learning",
        "medium.com": "learning", "substack.com": "learning",
        # Communication
        "mail.google.com": "communication", "outlook.live.com": "communication",
        "slack.com": "communication", "discord.com": "communication",
        "teams.microsoft.com": "communication", "zoom.us": "communication",
        "calendar.google.com": "communication",
        # Writing
        "notion.so": "writing", "docs.google.com": "writing",
        "obsidian.md": "writing",
    }

    def categorize_domain(self, domain: str) -> Optional[str]:
        """Categorize by domain. Returns None if unknown."""
        domain = domain.lower()
        if domain in self.DOMAIN_CATEGORIES:
            return self.DOMAIN_CATEGORIES[domain]
        # Check parent domain (e.g. en.wikipedia.org -> wikipedia.org)
        parts = domain.split(".")
        if len(parts) > 2:
            parent = ".".join(parts[-2:])
            if parent in self.DOMAIN_CATEGORIES:
                return self.DOMAIN_CATEGORIES[parent]
        return None

    def categorize(self, app_name: str, window_title: str = "", url: str = "") -> str:
        """Determine category for an app + window title + optional URL combo."""
        app_lower = app_name.lower()
        title_lower = window_title.lower()

        # Browser sub-categorization — prefer URL over window title
        browser_apps = [b.lower() for b in self.browsing]
        if app_lower in browser_apps or any(b in app_lower for b in browser_apps):
            # If we have a URL, use domain-based categorization first
            if url:
                try:
                    domain = url.split("://", 1)[1].split("/")[0].split(":")[0]
                    if domain.startswith("www."):
                        domain = domain[4:]
                    cat = self.categorize_domain(domain)
                    if cat:
                        return cat
                except (IndexError, AttributeError):
                    pass

            # Fall back to title-based heuristics
            entertainment_signals = [
                "youtube", "netflix", "twitch", "reddit", "twitter",
                "instagram", "tiktok", "hacker news", "9gag"
            ]
            learning_signals = [
                "coursera", "udemy", "khan academy", "documentation",
                "tutorial", "docs.", "arxiv", "paper"
            ]
            coding_signals = [
                "github", "gitlab", "stackoverflow", "stack overflow",
                "npm", "pypi", "crates.io", "docs.python"
            ]
            if any(s in title_lower for s in entertainment_signals):
                return "entertainment"
            if any(s in title_lower for s in learning_signals):
                return "learning"
            if any(s in title_lower for s in coding_signals):
                return "coding"
            return "browsing"

        # Direct app matching
        for category, apps in [
            ("coding", self.coding),
            ("communication", self.communication),
            ("entertainment", self.entertainment),
            ("writing", self.writing),
            ("learning", self.learning),
        ]:
            if any(a.lower() in app_lower or app_lower in a.lower() for a in apps):
                return category

        return "other"


@dataclass
class AIConfig:
    provider: str = "claude"  # "claude" or "ollama"
    claude_model: str = "claude-sonnet-4-20250514"
    claude_api_key: Optional[str] = None
    ollama_model: str = "llama3.1:8b"
    ollama_url: str = "http://localhost:11434"


@dataclass
class ObsidianConfig:
    vault_path: str = "~/Documents/Obsidian/MyVault"
    daily_notes_folder: str = "Retra"
    template: str = "default"

    @property
    def resolved_vault_path(self) -> Path:
        return Path(self.vault_path).expanduser()

    @property
    def output_dir(self) -> Path:
        return self.resolved_vault_path / self.daily_notes_folder


@dataclass
class DashboardConfig:
    port: int = 5173
    auto_open: bool = True


@dataclass
class Settings:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    categories: CategoryConfig = field(default_factory=CategoryConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)


def load_settings(config_path: Optional[str] = None) -> Settings:
    """Load settings from TOML file, falling back to defaults."""
    path = Path(config_path) if config_path else CONFIG_DIR / "settings.toml"

    if not path.exists():
        return Settings()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    settings = Settings()

    if "capture" in raw:
        for k, v in raw["capture"].items():
            if hasattr(settings.capture, k):
                setattr(settings.capture, k, v)

    if "privacy" in raw:
        for k, v in raw["privacy"].items():
            if hasattr(settings.privacy, k):
                setattr(settings.privacy, k, v)

    if "categories" in raw:
        for k, v in raw["categories"].items():
            if hasattr(settings.categories, k):
                setattr(settings.categories, k, v)

    if "ai" in raw:
        for k, v in raw["ai"].items():
            if hasattr(settings.ai, k):
                setattr(settings.ai, k, v)
        # Also check env var for API key
        if not settings.ai.claude_api_key:
            settings.ai.claude_api_key = os.environ.get("ANTHROPIC_API_KEY")

    if "obsidian" in raw:
        for k, v in raw["obsidian"].items():
            if hasattr(settings.obsidian, k):
                setattr(settings.obsidian, k, v)

    if "dashboard" in raw:
        for k, v in raw["dashboard"].items():
            if hasattr(settings.dashboard, k):
                setattr(settings.dashboard, k, v)

    return settings


# Singleton
_settings: Optional[Settings] = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
