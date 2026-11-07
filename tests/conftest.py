import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import ObsidianConfig, Settings  # noqa: E402
from storage.database import Database  # noqa: E402
from storage.models import WindowEvent  # noqa: E402


def write_heartbeat(path: Path, at: datetime, pid: int):
    path.write_text(json.dumps({"pid": pid, "timestamp": at.isoformat(), "uptime_seconds": 600}))


def write_pipeline(path: Path, at: datetime, ok: bool = True, error: str = ""):
    stamp = at.isoformat(timespec="seconds")
    entry = {"ok": ok, "last_run": stamp, "last_success": stamp}
    if not ok:
        entry["last_success"] = (at - timedelta(days=1)).isoformat(timespec="seconds")
        entry["error"] = error
    path.write_text(json.dumps({"journal": entry, "ai_summary": entry, "wiki": entry}))


@pytest.fixture
def retra(tmp_path, monkeypatch):
    """The real MCP server, pointed at a fixture database, vault, daemon
    heartbeat and pipeline status, with call logging captured instead of
    written to data/."""
    db_path = tmp_path / "retra.db"
    db = Database(db_path)
    now = datetime.now().replace(microsecond=0)
    for i in range(6):
        db.insert_event(WindowEvent(
            timestamp=now - timedelta(minutes=30 - i * 5), app_name="Code",
            window_title="server.py - retra", category="development", project_hint="retra",
        ))
    db.save_agent_session({
        "session_id": "s1", "source": "claude-code", "cwd": "/work/retra", "project": "retra",
        "branch": "main", "title": "Add contract test", "started_at": (now - timedelta(hours=1)).isoformat(),
        "ended_at": now.isoformat(), "prompt_count": 1, "files": "[]", "prs": "[]",
        "last_status": "Snapshot written", "last_reply": None, "log_path": "/x.jsonl",
        "log_size": 1, "log_mtime": 1.0,
    }, [((now - timedelta(minutes=50)).isoformat(), "prompt", "write the contract test")])

    # Default settings, not the local config: fixed privacy rules, and a vault in tmp_path so
    # wiki_page never reads the developer's real notes.
    vault = tmp_path / "vault"
    settings = Settings(obsidian=ObsidianConfig(vault_path=str(vault)))
    wiki = vault / "retra-wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "retra.md").write_text("# Retra\n")

    # A live daemon (this test process) that just wrote a heartbeat, and a
    # pipeline that compiled an hour ago.
    heartbeat, pid_file, pipeline = tmp_path / "daemon.heartbeat", tmp_path / "daemon.pid", tmp_path / "pipeline.json"
    write_heartbeat(heartbeat, now, os.getpid())
    pid_file.write_text(str(os.getpid()))
    write_pipeline(pipeline, now - timedelta(hours=1))

    from config import settings as settings_mod
    from export import pipeline_status
    from mcp_server import server as srv, status, store
    monkeypatch.setattr(settings_mod, "_settings", settings)
    monkeypatch.setenv("RETRA_DB_PATH", str(db_path))
    monkeypatch.setattr(store, "_db", None)
    monkeypatch.setattr(status, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(status, "PID_PATH", pid_file)
    monkeypatch.setattr(pipeline_status, "PIPELINE_STATUS_PATH", pipeline)
    calls = []
    monkeypatch.setattr(srv, "log_call", lambda tool, client, args, **kw: calls.append((tool, client, kw["ok"])))
    yield SimpleNamespace(mcp=srv.mcp, calls=calls, now=now, heartbeat=heartbeat,
                          pid_file=pid_file, pipeline=pipeline)
    monkeypatch.setattr(store, "_db", None)
