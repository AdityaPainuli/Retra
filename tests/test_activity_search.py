"""search_activity over the full-text index: words in any order, prefixes,
bm25 ranking, a substring fallback, and an index that repairs itself."""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta

from mcp import Client

from storage.database import Database
from storage.models import WindowEvent

T0 = datetime(2026, 11, 5, 10, 0)
RANGE = {"since": "2026-11-05", "until": "2026-11-05"}


def _events(*titles, app="Code"):
    db = Database(os.environ["RETRA_DB_PATH"])
    for i, title in enumerate(titles):
        db.insert_event(WindowEvent(timestamp=T0 + timedelta(minutes=5 * i), app_name=app,
                                    window_title=title, category="development"))
    db.insert_event(WindowEvent(timestamp=T0 + timedelta(minutes=5 * len(titles)), app_name="Finder",
                                window_title="Desktop", category="other"))


def _search(retra, query, **kw):
    async def go():
        async with Client(retra.mcp) as client:
            res = await client.call_tool("search_activity", {"query": query, **RANGE, **kw})
            return json.loads(res.content[0].text)
    return asyncio.run(go())


def test_words_in_any_order_and_mid_title(retra):
    _events("Fix the flaky login test in CI")
    r = _search(retra, "ci login")
    assert r["match"] == "words"
    assert [h["title"] for h in r["hits"]] == ["Fix the flaky login test in CI"]


def test_word_prefix_matches(retra):
    _events("Fix the flaky login test in CI")
    assert _search(retra, "flak")["hits"]


def test_falls_back_to_substring_inside_a_word(retra):
    _events("Fix the flaky login test in CI")
    r = _search(retra, "laky")
    assert r["match"] == "substring" and r["hits"]


def test_best_match_ranks_first(retra):
    # The newer window matches only loosely (long title); the older one is all about it.
    _events("login test", "login test notes plus a long list of other things about the sprint and planning")
    hits = _search(retra, "login test")["hits"]
    assert hits[0]["title"] == "login test"


def test_url_words_match(retra):
    db = Database(os.environ["RETRA_DB_PATH"])
    db.insert_event(WindowEvent(timestamp=T0, app_name="Brave Browser", window_title="Pull request",
                                url="https://github.com/acme/retra/pull/42", category="development"))
    _events()
    assert _search(retra, "acme retra")["hits"]


def test_stale_index_falls_back_then_rebuilds(retra):
    _events("Fix the flaky login test in CI")
    path = os.environ["RETRA_DB_PATH"]
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO window_events_fts(window_events_fts) VALUES ('delete-all')")
    conn.commit()
    conn.close()

    # The read-only server can't repair it, so it scans instead.
    r = _search(retra, "login")
    assert r["match"] == "substring" and r["hits"]

    Database(path)  # what the daemon does on start
    r = _search(retra, "ci login")
    assert r["match"] == "words" and r["hits"]


def test_matching_pages_ranked_from_the_url_index(retra):
    db = Database(os.environ["RETRA_DB_PATH"])
    for i, (title, url) in enumerate([("Retra pull request", "https://github.com/acme/retra/pull/42"),
                                      ("Lunch menu", "https://food.example.com/menu")]):
        db.insert_url_event(T0 + timedelta(minutes=i), url, url.split("/")[2], title, "Brave Browser",
                            "development", 60)
    _events("Pull request")
    pages = _search(retra, "retra pull")["pages"]
    assert [p["title"] for p in pages] == ["Retra pull request"]


def test_underscores_split_into_separate_words(retra):
    _events("Request for Retra pull")
    r = _search(retra, "pull_request retra")
    assert r["match"] == "words" and r["hits"]


def test_falls_back_when_only_idle_rows_match_the_words(retra):
    db = Database(os.environ["RETRA_DB_PATH"])
    db.insert_event(WindowEvent(timestamp=T0 - timedelta(minutes=10), app_name="Code", window_title="erver idle",
                                category="idle", is_idle=True))
    _events("server.py - retra")
    r = _search(retra, "erver")
    assert r["match"] == "substring"
    assert [h["title"] for h in r["hits"]] == ["server.py - retra"]


def test_pages_report_their_own_match(retra):
    db = Database(os.environ["RETRA_DB_PATH"])
    db.insert_url_event(T0, "https://github.com/acme/retra/pull/42", "github.com", "Retra pull request",
                        "Brave Browser", "development", 60)
    _events("Pull request")
    r = _search(retra, "retra pull")
    assert r["match"] == "substring" and r["pages_match"] == "words"


def test_daemon_check_repairs_an_index_with_stale_content(retra):
    _events("Old title")
    path = os.environ["RETRA_DB_PATH"]
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER window_events_fts_au")  # a title change the index never saw
    conn.execute("UPDATE window_events SET window_title = 'Renamed window' WHERE window_title = 'Old title'")
    conn.commit()
    conn.close()

    db = Database(path)  # row counts still agree, so the cheap check passes
    assert db.verify_fts() == ["window_events_fts"]
    r = _search(retra, "renamed")
    assert r["match"] == "words" and [h["title"] for h in r["hits"]] == ["Renamed window"]
