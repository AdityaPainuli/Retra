"""
Retra Menubar App
A lightweight macOS menubar icon for quick access to Retra features.
Uses the `rumps` library. Shows live recording health status.
"""

import webbrowser
from datetime import date, datetime

try:
    import rumps
except ImportError:
    print("[Retra] rumps not installed. Run: pip install rumps")
    raise


from storage.database import Database
from export.obsidian import export_to_obsidian
from capture.health import get_health
from config.settings import get_settings, DB_PATH


class RetraMenubar(rumps.App):
    def __init__(self):
        super().__init__(
            "Retra",
            icon=None,
            title="🔍 ...",
            quit_button=None,
        )
        self.db = Database(DB_PATH)
        self.settings = get_settings()
        self._last_health_ok = True
        self._notified_down = False

        self._recording_status = rumps.MenuItem("Recording: checking...")
        self._recording_status.set_callback(None)

        # Build menu
        self.menu = [
            self._recording_status,
            None,
            rumps.MenuItem("Open Dashboard", callback=self.open_dashboard),
            None,
            rumps.MenuItem("Today's Stats", callback=self.show_stats),
            rumps.MenuItem("Recording Health", callback=self.show_health),
            rumps.MenuItem("Generate Journal", callback=self.generate_journal),
            None,
            rumps.MenuItem("Quit Retra", callback=self.quit_app),
        ]

        # Timer: update title + health every 10 seconds
        self.timer = rumps.Timer(self.update_title, 10)
        self.timer.start()

    def update_title(self, _):
        """Update menubar title with focus time and recording indicator."""
        health = get_health()

        # Update recording status in menu
        if health.recording_ok:
            self._recording_status.title = "Recording: Active"
            indicator = "●"  # solid dot = recording
            if not self._last_health_ok and self._notified_down:
                rumps.notification("Retra", "Recording resumed", "Capture daemon is active again.")
                self._notified_down = False
            self._last_health_ok = True
        else:
            self._recording_status.title = "Recording: STOPPED"
            indicator = "○"  # hollow dot = not recording
            if self._last_health_ok:
                rumps.notification(
                    "Retra",
                    "Recording stopped!",
                    "Capture daemon is not running. Use 'python main.py start' to restart.",
                )
                self._notified_down = True
            self._last_health_ok = False

        # Show focus time
        try:
            summary = self.db.compute_daily_summary(date.today())
            h, m = divmod(summary.focus_minutes, 60)
            self.title = f"{indicator} {h}h{m}m"
        except Exception:
            self.title = f"{indicator} --"

    @rumps.clicked("Open Dashboard")
    def open_dashboard(self, _):
        port = self.settings.dashboard.port
        webbrowser.open(f"http://localhost:{port}")

    @rumps.clicked("Today's Stats")
    def show_stats(self, _):
        summary = self.db.compute_daily_summary(date.today())
        stats = (
            f"Focus Score: {summary.focus_score}/100\n"
            f"Deep Work: {summary.focus_minutes // 60}h {summary.focus_minutes % 60}m\n"
            f"Total Tracked: {summary.total_tracked_display()}\n"
            f"Longest Streak: {summary.longest_focus_streak_minutes}m\n"
            f"App Switches: {summary.app_switches}"
        )
        rumps.alert(title="Retra — Today", message=stats, ok="Close")

    @rumps.clicked("Recording Health")
    def show_health(self, _):
        health = get_health()
        lines = health.summary_lines()
        overall = "ALL OK" if health.recording_ok else "NOT OK"
        msg = f"Overall: {overall}\n\n" + "\n".join(lines)
        rumps.alert(title="Retra — Recording Health", message=msg, ok="Close")

    @rumps.clicked("Generate Journal")
    def generate_journal(self, _):
        try:
            filepath = export_to_obsidian()
            rumps.notification(
                title="Retra",
                subtitle="Journal exported!",
                message=f"Saved to: {filepath.name}",
            )
        except Exception as e:
            rumps.alert(title="Export Error", message=str(e))

    @rumps.clicked("Quit Retra")
    def quit_app(self, _):
        rumps.quit_application()


def run_menubar():
    """Start the menubar app."""
    RetraMenubar().run()
