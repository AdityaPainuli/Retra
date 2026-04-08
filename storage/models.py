"""
Retra Data Models
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class WindowEvent:
    """A single window focus event captured by the daemon."""
    timestamp: datetime
    app_name: str
    window_title: str
    category: str
    bundle_id: Optional[str] = None
    url: Optional[str] = None
    is_idle: bool = False
    id: Optional[int] = None


@dataclass
class Session:
    """An aggregated block of continuous activity in one app/category."""
    start_time: datetime
    end_time: datetime
    app_name: str
    category: str
    window_titles: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)  # domains visited (browsers)
    duration_seconds: int = 0
    is_productive: Optional[bool] = None  # user-tagged
    tag: Optional[str] = None
    id: Optional[int] = None

    @property
    def duration_minutes(self) -> int:
        return self.duration_seconds // 60

    @property
    def duration_display(self) -> str:
        h, m = divmod(self.duration_minutes, 60)
        if h > 0:
            return f"{h}h {m}m" if m > 0 else f"{h}h"
        return f"{m}m"


@dataclass
class Screenshot:
    """A captured screenshot."""
    timestamp: datetime
    filepath: str
    app_name: str
    window_title: str
    thumbnail_path: Optional[str] = None
    id: Optional[int] = None


@dataclass
class DailySummary:
    """Aggregated stats for a single day."""
    date: date
    total_tracked_minutes: int = 0
    focus_minutes: int = 0
    communication_minutes: int = 0
    browsing_minutes: int = 0
    entertainment_minutes: int = 0
    writing_minutes: int = 0
    learning_minutes: int = 0
    other_minutes: int = 0
    idle_minutes: int = 0
    app_switches: int = 0
    longest_focus_streak_minutes: int = 0
    focus_score: int = 0
    sessions: list[Session] = field(default_factory=list)
    ai_summary: Optional[str] = None

    @property
    def active_minutes(self) -> int:
        return self.total_tracked_minutes - self.idle_minutes

    @property
    def focus_percentage(self) -> int:
        if self.active_minutes == 0:
            return 0
        return round((self.focus_minutes / self.active_minutes) * 100)

    def total_tracked_display(self) -> str:
        h, m = divmod(self.total_tracked_minutes, 60)
        return f"{h}h {m}m"

    @property
    def coding_minutes(self) -> int:
        """Coding-specific minutes (focus minus writing and learning)."""
        return max(0, self.focus_minutes - self.writing_minutes - self.learning_minutes)

    def category_breakdown(self) -> list[dict]:
        return [
            {"name": "Deep Work", "minutes": self.coding_minutes, "color": "#f59e0b"},
            {"name": "Writing", "minutes": self.writing_minutes, "color": "#10b981"},
            {"name": "Learning", "minutes": self.learning_minutes, "color": "#06b6d4"},
            {"name": "Communication", "minutes": self.communication_minutes, "color": "#3b82f6"},
            {"name": "Browsing", "minutes": self.browsing_minutes, "color": "#8b5cf6"},
            {"name": "Entertainment", "minutes": self.entertainment_minutes, "color": "#ef4444"},
            {"name": "Other", "minutes": self.other_minutes, "color": "#6b7280"},
        ]


@dataclass
class WeeklySummary:
    """Aggregated stats for a week."""
    start_date: date
    end_date: date
    days: list[DailySummary] = field(default_factory=list)
    avg_focus_minutes: float = 0
    avg_focus_score: float = 0
    total_focus_minutes: int = 0
    most_used_app: str = ""
    trend_vs_prev_week: Optional[float] = None  # percentage change in focus
