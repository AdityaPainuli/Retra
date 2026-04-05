#!/usr/bin/env python3
"""
Retra — Your Day, In Focus
Main CLI entry point.

Usage:
    python main.py start       Start everything (capture + dashboard + menubar)
    python main.py stop        Stop all Retra processes
    python main.py status      Show recording health & today's stats
    python main.py capture     Start the capture daemon (foreground)
    python main.py dashboard   Start the web dashboard (foreground)
    python main.py menubar     Start the menubar app
    python main.py journal     Generate today's Obsidian journal
    python main.py journal YYYY-MM-DD  Generate journal for a specific date
    python main.py install     Install as macOS Launch Agent (auto-start on login)
    python main.py uninstall   Remove Launch Agent
"""

import sys
import os
import subprocess
import signal
import time
from datetime import date
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def cmd_start():
    """Launch capture daemon + dashboard + menubar in one command."""
    from capture.health import get_health
    from config.settings import PID_PATH

    health = get_health()
    script = str(Path(__file__).resolve())
    python = sys.executable
    log_dir = Path.home() / "Library" / "Logs"
    log_dir.mkdir(exist_ok=True)

    # 1. Start capture daemon (background)
    if health.daemon_running:
        print(f"  Capture daemon already running (PID {health.daemon_pid})")
    else:
        capture_log = log_dir / "retra.log"
        capture_err = log_dir / "retra.error.log"
        with open(capture_log, "a") as out, open(capture_err, "a") as err:
            proc = subprocess.Popen(
                [python, script, "capture"],
                stdout=out, stderr=err,
                start_new_session=True,
            )
        # Wait briefly and verify it started
        time.sleep(1)
        health = get_health()
        if health.daemon_running:
            print(f"  Capture daemon started (PID {health.daemon_pid})")
        else:
            print(f"  WARNING: Capture daemon may not have started (PID {proc.pid})")
            print(f"  Check logs: {capture_err}")

    # 2. Start dashboard (background)
    dash_pid_path = Path(__file__).resolve().parent / "data" / "dashboard.pid"
    dash_running = False
    if dash_pid_path.exists():
        try:
            pid = int(dash_pid_path.read_text().strip())
            os.kill(pid, 0)
            dash_running = True
            print(f"  Dashboard already running (PID {pid})")
        except (ProcessLookupError, ValueError, OSError):
            pass

    if not dash_running:
        dash_log = log_dir / "retra-dashboard.log"
        with open(dash_log, "a") as out:
            proc = subprocess.Popen(
                [python, script, "dashboard"],
                stdout=out, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        dash_pid_path.parent.mkdir(parents=True, exist_ok=True)
        dash_pid_path.write_text(str(proc.pid))
        print(f"  Dashboard started (PID {proc.pid}) — http://localhost:5173")

    # 3. Start menubar (background)
    menu_pid_path = Path(__file__).resolve().parent / "data" / "menubar.pid"
    menu_running = False
    if menu_pid_path.exists():
        try:
            pid = int(menu_pid_path.read_text().strip())
            os.kill(pid, 0)
            menu_running = True
            print(f"  Menubar already running (PID {pid})")
        except (ProcessLookupError, ValueError, OSError):
            pass

    if not menu_running:
        proc = subprocess.Popen(
            [python, script, "menubar"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        menu_pid_path.parent.mkdir(parents=True, exist_ok=True)
        menu_pid_path.write_text(str(proc.pid))
        print(f"  Menubar started (PID {proc.pid})")

    print()
    print("Retra is running. Use 'python main.py status' to verify recording.")
    print("Use 'python main.py stop' to stop everything.")


def cmd_stop():
    """Stop all Retra processes."""
    from config.settings import PID_PATH

    data_dir = Path(__file__).resolve().parent / "data"
    pid_files = {
        "Capture daemon": PID_PATH,
        "Dashboard": data_dir / "dashboard.pid",
        "Menubar": data_dir / "menubar.pid",
    }

    any_stopped = False
    for name, pid_path in pid_files.items():
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"  Stopped {name} (PID {pid})")
                any_stopped = True
            except ProcessLookupError:
                print(f"  {name} was not running (stale PID file)")
            except (ValueError, OSError) as e:
                print(f"  Could not stop {name}: {e}")
            finally:
                try:
                    pid_path.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            print(f"  {name}: not running")

    if any_stopped:
        print("\nRetra stopped.")
    else:
        print("\nNothing was running.")


def cmd_capture():
    from capture.daemon import run_daemon
    run_daemon()


def cmd_dashboard():
    from ui.server import run_server
    run_server()


def cmd_menubar():
    from ui.menubar import run_menubar
    run_menubar()


def cmd_journal(target_date: str = None):
    from export.obsidian import export_to_obsidian

    if target_date:
        d = date.fromisoformat(target_date)
    else:
        d = date.today()

    filepath = export_to_obsidian(d)
    print(f"Journal exported: {filepath}")


def cmd_status():
    from capture.health import get_health
    from storage.database import Database
    from config.settings import DB_PATH

    print(f"\n  Retra — {date.today().strftime('%A, %B %d')}")
    print(f"{'─' * 44}")

    # Recording health
    health = get_health()
    print("\n  RECORDING HEALTH")
    if health.recording_ok:
        print("  Status:       ALL OK")
    elif health.daemon_running:
        print("  Status:       DEGRADED (heartbeat stale)")
    else:
        print("  Status:       NOT RECORDING")

    for line in health.summary_lines():
        print(line)

    # Today's stats
    if health.db_exists:
        db = Database(DB_PATH)
        summary = db.compute_daily_summary(date.today())
        print(f"\n  TODAY'S ACTIVITY")
        print(f"  Focus Score:    {summary.focus_score}/100")
        print(f"  Deep Work:      {summary.focus_minutes // 60}h {summary.focus_minutes % 60}m")
        print(f"  Total Tracked:  {summary.total_tracked_display()}")
        print(f"  Longest Streak: {summary.longest_focus_streak_minutes}m")
        print(f"  App Switches:   {summary.app_switches}")
        print(f"  Communication:  {summary.communication_minutes}m")
        print(f"  Browsing:       {summary.browsing_minutes}m")
        print(f"  Entertainment:  {summary.entertainment_minutes}m")
    else:
        print("\n  No database yet — start recording first.")

    print()


def _make_plist(label: str, command: str, log_name: str, keep_alive: bool = True) -> str:
    """Generate a launchd plist XML for a Retra service."""
    python_path = sys.executable
    script_path = Path(__file__).resolve()
    home = Path.home()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
        <string>{command}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <{'true' if keep_alive else 'false'}/>
    <key>StandardOutPath</key>
    <string>{home}/Library/Logs/{log_name}.log</string>
    <key>StandardErrorPath</key>
    <string>{home}/Library/Logs/{log_name}.error.log</string>
</dict>
</plist>"""


SERVICES = [
    ("com.retra.capture",   "capture",   "retra",           True),
    ("com.retra.dashboard", "dashboard", "retra-dashboard", True),
    ("com.retra.menubar",   "menubar",   "retra-menubar",   False),
]


def cmd_install():
    """Install all Retra services as macOS Launch Agents.
    They auto-start on login and survive sleep/wake cycles."""
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(exist_ok=True)

    # Stop existing processes first so launchd takes over cleanly
    cmd_stop()
    print()

    for label, command, log_name, keep_alive in SERVICES:
        plist_path = plist_dir / f"{label}.plist"

        # Unload if already loaded
        os.system(f"launchctl unload {plist_path} 2>/dev/null")

        plist_path.write_text(_make_plist(label, command, log_name, keep_alive))
        os.system(f"launchctl load {plist_path}")
        print(f"  Installed {label}")

    print()
    print("Retra installed. All services will:")
    print("  - Start automatically on login")
    print("  - Resume after sleep/wake")
    print("  - Restart if they crash (capture + dashboard)")
    print()
    print(f"Logs: ~/Library/Logs/retra*.log")
    print("To remove: python main.py uninstall")


def cmd_uninstall():
    """Remove all Retra Launch Agents."""
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    any_found = False

    for label, _, _, _ in SERVICES:
        plist_path = plist_dir / f"{label}.plist"
        if plist_path.exists():
            os.system(f"launchctl unload {plist_path}")
            plist_path.unlink()
            print(f"  Removed {label}")
            any_found = True

    if any_found:
        print("\nRetra Launch Agents removed.")
    else:
        print("No Launch Agents found.")


COMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "status": cmd_status,
    "capture": cmd_capture,
    "dashboard": cmd_dashboard,
    "menubar": cmd_menubar,
    "journal": cmd_journal,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command not in COMMANDS:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    # Pass extra args to the command
    if command == "journal" and len(sys.argv) > 2:
        cmd_journal(sys.argv[2])
    else:
        COMMANDS[command]()


if __name__ == "__main__":
    main()
