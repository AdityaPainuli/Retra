"""Every list tool pages: `offset` in, `has_more` / `next_offset` out, and
walking the pages returns each match exactly once."""

import asyncio
import json
import os
from datetime import datetime, timedelta

import pytest
from mcp import Client

from storage.database import Database
from storage.models import Screenshot, WindowEvent

N = 7  # matches per tool; limit 3 gives pages of 3, 3, 1


@pytest.fixture
def seeded(retra):
    # The MCP server opens the database read-only; the pipeline writes it.
    db = Database(os.environ["RETRA_DB_PATH"])
    base = retra.now - timedelta(hours=3)
    for i in range(N):
        at = base + timedelta(minutes=10 * i)
        db.insert_event(WindowEvent(timestamp=at, app_name="Notes", window_title=f"pagingword note {i}",
                                    category="writing"))
        db.insert_event(WindowEvent(timestamp=at + timedelta(minutes=5), app_name="Mail", window_title="Inbox",
                                    category="communication"))
        sid = db.insert_screenshot(Screenshot(timestamp=at, filepath="", app_name="Notes",
                                              window_title=f"note {i}"))
        db.set_screen_text(sid, f"pagingword screen {i}")
        db.save_agent_session({
            "session_id": f"p{i}", "source": "claude-code", "cwd": "/work/paging", "project": "paging",
            "branch": "main", "title": f"Paging session {i}", "started_at": at.isoformat(),
            "ended_at": (at + timedelta(minutes=5)).isoformat(), "prompt_count": 1, "files": "[]", "prs": "[]",
            "last_status": None, "last_reply": None, "log_path": f"/p{i}.jsonl", "log_size": 1, "log_mtime": 1.0,
        }, [(at.isoformat(), "prompt", f"pagingword prompt {i}")])
    return retra


def _call(retra, name, args):
    async def go():
        async with Client(retra.mcp) as client:
            return json.loads((await client.call_tool(name, args)).content[0].text)
    return asyncio.run(go())


def _walk(retra, name, args, key, ident):
    seen, pages, offset = [], [], 0
    while True:
        r = _call(retra, name, {**args, "limit": 3, "offset": offset})
        assert "error" not in r, r
        pages.append(len(r[key]))
        seen += [ident(h) for h in r[key]]
        if not r["has_more"]:
            assert r["next_offset"] is None
            return seen, pages, r
        assert r["next_offset"] == offset + 3
        offset = r["next_offset"]


CASES = {
    "search_activity": ({"query": "pagingword"}, "hits", lambda h: h["title"]),
    "search_screen_text": ({"query": "pagingword"}, "hits", lambda h: h["title"]),
    "search_coding_sessions": ({"query": "pagingword"}, "hits", lambda h: h["session"]),
    "coding_sessions": ({"project": "paging"}, "sessions", lambda h: h["title"]),
}


@pytest.mark.parametrize("name", CASES)
def test_pages_cover_every_match_once(seeded, name):
    args, key, ident = CASES[name]
    seen, pages, _ = _walk(seeded, name, args, key, ident)
    assert pages == [3, 3, 1]
    assert len(seen) == len(set(seen)) == N


def test_offset_past_the_end_is_empty(seeded):
    r = _call(seeded, "search_screen_text", {"query": "pagingword", "offset": 50})
    assert r["hits"] == [] and r["has_more"] is False and r["next_offset"] is None


def test_related_pages_come_with_the_first_page_only(seeded):
    assert _call(seeded, "search_activity", {"query": "pagingword", "limit": 3, "offset": 3})["pages"] == []
