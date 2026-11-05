"""get_status and the freshness stamp on every tool result: agents must be
able to tell when data is old and where it stops."""

import asyncio
import json
from datetime import timedelta

from mcp import Client

from tests.conftest import write_heartbeat, write_pipeline


def _call(retra, name, args=None):
    async def go():
        async with Client(retra.mcp) as client:
            res = await client.call_tool(name, args or {})
            return json.loads(res.content[0].text)
    return asyncio.run(go())


def test_status_when_all_fresh(retra):
    s = _call(retra, "get_status")
    assert s["capture"]["recording"] is True
    assert s["latest"]["activity"] == (retra.now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    assert s["latest"]["coding_sessions"] == retra.now.strftime("%Y-%m-%d %H:%M")
    assert s["latest"]["screen_text"] is None
    assert s["pipeline"]["wiki"]["ok"] is True
    assert s["warnings"] == []


def test_results_carry_freshness(retra):
    f = _call(retra, "recent_work")["freshness"]
    assert f == {"activity_until": (retra.now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")}

    f = _call(retra, "day_summary")["freshness"]
    assert set(f) == {"activity_until", "journal_compiled_at", "ai_summary_compiled_at"}

    f = _call(retra, "wiki_page", {"name": "retra"})["freshness"]
    assert f == {"wiki_compiled_at": (retra.now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")}

    assert "coding_sessions_until" in _call(retra, "coding_sessions")["freshness"]
    assert "freshness" not in _call(retra, "get_status")


def test_stale_heartbeat_warns(retra):
    write_heartbeat(retra.heartbeat, retra.now - timedelta(hours=3), pid=999_999)
    retra.pid_file.unlink()

    s = _call(retra, "get_status")
    assert s["capture"]["recording"] is False
    assert any("isn't recording" in w for w in s["warnings"])

    f = _call(retra, "search_activity", {"query": "server.py"})["freshness"]
    assert any("isn't recording" in w for w in f["warnings"])
    assert "warnings" not in _call(retra, "wiki_page", {"name": "retra"})["freshness"]


def test_failed_and_old_compiles_warn(retra):
    write_pipeline(retra.pipeline, retra.now, ok=False, error="API key sk-ant-abc123def456ghi789 rejected")
    s = _call(retra, "get_status")
    assert "sk-ant-abc123def456ghi789" not in json.dumps(s), "error text must be redacted"
    assert any("last wiki compile failed" in w for w in s["warnings"])
    assert any("failed" in w for w in _call(retra, "wiki_page", {"name": "retra"})["freshness"]["warnings"])

    write_pipeline(retra.pipeline, retra.now - timedelta(days=3))
    assert any("misses activity" in w for w in _call(retra, "get_status")["warnings"])
    assert "warnings" not in _call(retra, "recent_work")["freshness"]


def test_failed_ai_summary_warns_even_when_journal_and_wiki_succeed(retra):
    stamp = retra.now.isoformat(timespec="seconds")
    ok = {"ok": True, "last_run": stamp, "last_success": stamp}
    failed = {"ok": False, "last_run": stamp, "last_success": (retra.now - timedelta(days=1)).isoformat(timespec="seconds"),
              "error": "rate limited"}
    retra.pipeline.write_text(json.dumps({"journal": ok, "ai_summary": failed, "wiki": ok}))

    warnings = _call(retra, "get_status")["warnings"]
    assert len(warnings) == 1 and "AI reflection compile failed" in warnings[0]
    assert any("AI reflection" in w for w in _call(retra, "day_summary")["freshness"]["warnings"])
    assert "warnings" not in _call(retra, "wiki_page", {"name": "retra"})["freshness"]


def test_status_works_before_the_database_exists(retra, monkeypatch, tmp_path):
    from mcp_server import store
    monkeypatch.setenv("RETRA_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr(store, "_db", None)

    s = _call(retra, "get_status")
    assert "error" not in s
    assert s["latest"] == {"activity": None, "screen_text": None, "coding_sessions": None}
    assert s["capture"]["recording"] is True and s["pipeline"]["wiki"]["ok"] is True
    assert s["db_size_mb"] is None
