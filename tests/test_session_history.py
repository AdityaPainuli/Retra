"""A Claude Code session can run for weeks, so its latest recap says nothing
about where it stood on a past day. coding_sessions(as_of=...) and
day_summary's coding_sessions answer from the turns up to that moment."""

import asyncio
import json
import os
from datetime import date, datetime, timedelta

import pytest
from mcp import Client

from storage.database import Database

D = date.today() - timedelta(days=3)


def at(day: date, hm: str) -> str:
    return f"{day.isoformat()}T{hm}:00"


@pytest.fixture
def history(retra):
    db = Database(os.environ["RETRA_DB_PATH"])
    later = D + timedelta(days=2)

    def session(sid, title, project, turns, cwd=None):
        db.save_agent_session({
            "session_id": sid, "source": "claude-code", "cwd": cwd or f"/work/{project}", "project": project,
            "repo": None, "branch": "main", "title": title, "started_at": turns[0][0], "ended_at": turns[-1][0],
            "prompt_count": sum(k == "prompt" for _, k, _ in turns), "files": '["/work/x/a.py"]',
            "prs": '["https://github.com/o/r/pull/18"]', "last_status": [t for _, k, t in turns if k == "recap"][-1],
            "last_reply": None, "log_path": f"/{sid}.jsonl", "log_size": 1, "log_mtime": 1.0,
        }, turns)

    session("long", "Project status check", "daylens", [
        (at(D, "13:47"), "prompt", "Yes, fix names"),
        (at(D, "13:56"), "recap", "PR 14 is open with the naming fix."),
        (at(D, "23:18"), "prompt", "Merge it"),
        (at(D, "23:19"), "recap", "PR 14 is merged and the daemon restarted."),
        (at(later, "14:53"), "prompt", "Merge PR 18"),
        (at(later, "14:55"), "recap", "PR 18 is merged."),
    ])
    session("short", "btw: what are you doing", "daylens", [
        (at(D, "13:50"), "prompt", "what are you doing"),
        (at(D, "13:52"), "recap", "Answered a side question."),
    ])
    session("bank", "bank statements", "bank", [
        (at(D, "12:00"), "prompt", "sort the bank login"),
        (at(D, "12:05"), "recap", "Bank login sorted."),
    ])
    return retra


def call(retra, tool, **args):
    async def go():
        async with Client(retra.mcp) as client:
            return json.loads((await client.call_tool(tool, args)).content[0].text)
    return asyncio.run(go())


def test_as_of_a_day_shows_where_each_session_stood_that_night(history):
    out = call(history, "coding_sessions", project="daylens", as_of=D.isoformat())
    assert out["as_of"] == f"end of {D}"
    first = out["sessions"][0]
    assert first["title"] == "Project status check"
    assert first["status"] == "PR 14 is merged and the daemon restarted."
    assert first["status_at"] == f"{D} 23:19" and first["last_active"] == f"{D} 23:19"
    assert first["prompts"] == 2 and [p["text"] for p in first["recent_prompts"]] == ["Yes, fix names", "Merge it"]
    assert "files_edited" not in first and "prs_opened" not in first, "those cover the whole session"
    assert out["sessions"][1]["title"] == "btw: what are you doing"


def test_as_of_a_moment_cuts_mid_day(history):
    out = call(history, "coding_sessions", project="daylens", as_of=at(D, "14:00"))
    assert out["sessions"][0]["status"] == "PR 14 is open with the naming fix."


def test_without_as_of_the_latest_state_is_shown(history):
    out = call(history, "coding_sessions", project="daylens")
    assert out["sessions"][0]["status"] == "PR 18 is merged."


def test_bad_as_of_is_an_error(history):
    assert "error" in call(history, "coding_sessions", as_of="last friday")


def test_day_summary_lists_the_days_sessions_and_their_last_recap(history):
    coding = call(history, "day_summary", day=D.isoformat())["coding_sessions"]
    assert [s["title"] for s in coding] == ["Project status check", "btw: what are you doing"]
    long = coding[0]
    assert (long["first_turn"], long["last_turn"], long["prompts"]) == ("13:47", "23:19", 2)
    assert long["last_recap"] == {"at": "23:19", "text": "PR 14 is merged and the daemon restarted."}
    assert long["earlier_recaps"] == [{"at": "13:56", "text": "PR 14 is open with the naming fix."}]
    assert all("bank" not in s["title"] for s in coding), "blocked sessions stay hidden"


def test_day_without_turns_lists_no_session(history):
    assert call(history, "day_summary", day=(D + timedelta(days=1)).isoformat())["coding_sessions"] == []


def test_session_search_matches_word_prefixes(history):
    texts = [h["text"].replace("[", "").replace("]", "") for h in
             call(history, "search_coding_sessions", query="nam fix")["hits"]]
    assert "Yes, fix names" in texts and "PR 14 is open with the naming fix." in texts
    assert call(history, "search_coding_sessions", query="PR 1")["hits"] == [], "short words match whole"
