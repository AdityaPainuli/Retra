"""search_wiki finds wiki sections (dated session entries are their own
sections), and wiki_page returns only the sections asked for (D3). The
compile prompts ask for confidence labels, and search reports them (D5)."""

import asyncio
import json

import pytest
from mcp import Client

from export import wiki_compiler
from mcp_server import server as srv
from mcp_server import wiki_search

RETRA = """---
title: Retra
type: project
aliases: [daylens]
tags: [project, activity-tracker]
---

# Retra

Repo `AdityaPainuli/Retra`, the local work-memory server.

## Total Time

~48m

## Sessions

### 2026-11-10
Glanced at the repo home page.

### 2026-11-09
Opened PR #14 about project names. Decided to keep folder names as projects. #source-backed

## Notes

- Launch moved from Friday to Monday. #confirmed
- Maybe switching the compiler to per-page jobs. #watchlist

## Related

- [[projects/launch-plan]]
"""

OTHER = """---
title: Launch Plan
type: project
---

# Launch Plan

## Notes

- Launch checklist drafted; launch date not settled.
"""


@pytest.fixture
def wiki(retra):
    root = srv._wiki_dir()
    (root / "projects" / "retra.md").write_text(RETRA)
    (root / "projects" / "launch-plan.md").write_text(OTHER)
    (root / "index.md").write_text("# Index\n- [[projects/retra]] launch\n")
    return root


def call(retra, tool, **args):
    async def go():
        async with Client(retra.mcp) as client:
            return json.loads((await client.call_tool(tool, args)).content[0].text)
    return asyncio.run(go())


def test_sessions_split_into_dated_sections_and_related_is_skipped():
    sections = wiki_search.split_page("projects/retra", RETRA)
    heads = [s.heading for s in sections]
    assert heads == [None, "Total Time", "2026-11-10", "2026-11-09", "Notes"]
    assert "daylens" in sections[0].names and sections[0].title == "Retra"
    assert sections[0].description.startswith("Repo `AdityaPainuli/Retra`")


def test_search_returns_section_refs_best_first(retra, wiki):
    out = call(retra, "search_wiki", query="launch monday")
    refs = [h["ref"] for h in out["hits"]]
    assert refs[0] == "projects/retra#Notes", "covers both words"
    assert out["hits"][0]["matched_words"] == 2 and out["hits"][0]["of_words"] == 2
    assert "projects/launch-plan#Notes" in refs
    assert not any(r.startswith("index") for r in refs), "the index isn't a note"
    assert "Launch moved from Friday to Monday" in out["hits"][0]["excerpt"]
    assert out["hits"][0]["labels"] == ["confirmed", "watchlist"]


def test_dated_session_and_identifiers_are_searchable(retra, wiki):
    hit = call(retra, "search_wiki", query="folder names")["hits"][0]
    assert hit["ref"] == "projects/retra#2026-11-09" and hit["section"] == "2026-11-09"
    assert call(retra, "search_wiki", query="AdityaPainuli")["hits"][0]["page"] == "projects/retra"


def test_project_ranks_its_page_first_by_alias(retra, wiki):
    plain = [h["page"] for h in call(retra, "search_wiki", query="launch checklist")["hits"]]
    assert plain[0] == "projects/launch-plan"
    boosted = [h["page"] for h in call(retra, "search_wiki", query="launch checklist", project="daylens")["hits"]]
    assert boosted[0] == "projects/retra"


def test_search_pages(retra, wiki):
    first = call(retra, "search_wiki", query="launch", limit=1)
    assert first["has_more"] and first["next_offset"] == 1 and len(first["hits"]) == 1
    rest = call(retra, "search_wiki", query="launch", limit=20, offset=1)
    assert first["hits"][0]["ref"] not in [h["ref"] for h in rest["hits"]]


def test_blocked_text_is_neither_searched_nor_shown(retra, wiki, monkeypatch):
    monkeypatch.setattr(srv, "redact_text", lambda t: t and t.replace("Friday", "[private]"))
    hits = call(retra, "search_wiki", query="friday")["hits"]
    assert hits == []
    notes = call(retra, "wiki_page", name="retra", sections=["Notes"])["sections"][0]["content"]
    assert "Friday" not in notes and "[private]" in notes


def test_read_sections(retra, wiki):
    out = call(retra, "wiki_page", name="retra", sections=["sessions", "2026-11-09", "Nope"])
    by = {s["section"]: s["content"] for s in out["sections"]}
    assert "### 2026-11-10" in by["Sessions"] and "### 2026-11-09" in by["Sessions"], "an H2 includes its H3s"
    assert by["2026-11-09"].startswith("### 2026-11-09") and "Notes" not in by["2026-11-09"]
    assert out["missing"] == ["Nope"] and "Notes" in out["available"]


def test_long_page_lists_its_sections(retra, wiki, monkeypatch):
    monkeypatch.setattr(srv, "MAX_WIKI_CHARS", len(OTHER))
    out = call(retra, "wiki_page", name="retra")
    assert out["content"].endswith("[truncated]")
    assert out["sections"] == ["Total Time", "Sessions", "2026-11-10", "2026-11-09", "Notes", "Related"]
    short = call(retra, "wiki_page", name="launch-plan")
    assert "sections" not in short
    rest = call(retra, "wiki_page", name="retra", offset=out["next_offset"])
    assert out["next_offset"] == len(OTHER) and rest["content"].startswith(RETRA[len(OTHER):len(OTHER) + 40])


def test_long_section_pages_by_offset(retra, wiki, monkeypatch):
    monkeypatch.setattr(srv, "MAX_WIKI_CHARS", 40)
    first = call(retra, "wiki_page", name="retra", sections=["Notes"])["sections"][0]
    assert first["next_offset"] == 40
    second = call(retra, "wiki_page", name="retra", sections=["Notes"], offset=40)["sections"][0]
    assert "Monday" in first["content"] + second["content"]


def test_repeated_heading_returns_every_occurrence(retra, wiki):
    (srv._wiki_dir() / "projects" / "retra.md").write_text(RETRA + "\n## Notes\n\n- Second notes block.\n")
    notes = call(retra, "wiki_page", name="retra", sections=["Notes"])["sections"][0]["content"]
    assert "Launch moved" in notes and "Second notes block" in notes


def test_search_skips_symlinks_outside_the_wiki(retra, wiki, tmp_path):
    secret = tmp_path / "outside.md"
    secret.write_text("# Outside\n\n## Notes\n\nzebracorn secret\n")
    (wiki / "projects" / "leak.md").symlink_to(secret)
    assert call(retra, "search_wiki", query="zebracorn")["hits"] == []


def test_project_alias_matches_the_whole_name(retra, wiki, monkeypatch):
    from capture import window_tracker
    monkeypatch.setattr(window_tracker, "project_aliases", lambda: {"myapp": {"retra"}, "retra": {"myapp"}})
    hits = call(retra, "search_wiki", query="launch checklist", project="app")["hits"]
    assert hits[0]["page"] == "projects/launch-plan", "'app' is not 'myapp'"


def test_compile_prompts_ask_for_confidence_labels():
    ingest = wiki_compiler._build_ingest_prompt("schema", "note", "2026-11-10", "", {})
    rollup = wiki_compiler._build_weekly_rollup_prompt("schema", {"2026-11-10": "note"}, {})
    for prompt in (ingest, rollup):
        for label in wiki_search.LABELS:
            assert f"#{label}" in prompt
        assert "## Contested" in prompt
