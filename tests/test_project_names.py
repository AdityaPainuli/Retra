"""Projects are named after their folder. A repo whose GitHub name differs
(folder "daylens", repo "Retra") is findable by either name, and a folder
with agent instructions but no repo still gets a project."""

import asyncio
import json
import os
from datetime import timedelta

import pytest
from mcp import Client

from capture import claude_sessions, window_tracker
from storage.database import Database


@pytest.fixture
def repos(tmp_path, monkeypatch):
    home = tmp_path / "home"
    desk = home / "Desktop"
    daylens = desk / "daylens"
    (daylens / ".git").mkdir(parents=True)
    (daylens / ".git" / "config").write_text(
        '[core]\n\tbare = false\n[remote "origin"]\n\turl = git@github.com:acme/Retra.git\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    plain = desk / "plain"
    (plain / ".git").mkdir(parents=True)
    (plain / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/acme/plain.git\n')
    notes = desk / "content-factory"
    (notes / "planner").mkdir(parents=True)
    (notes / "CLAUDE.md").write_text("# rules\n")
    monkeypatch.setattr(window_tracker.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(window_tracker, "_PROJECT_CACHE", {})
    monkeypatch.setattr(window_tracker, "_ALIASES", None)
    monkeypatch.setattr(window_tracker, "_KNOWN_PROJECT_SLUGS", None)
    return {"daylens": str(daylens), "plain": str(plain), "planner": str(notes / "planner"), "home": home}


def test_folder_name_stays_the_project(repos):
    assert window_tracker._cwd_to_project_slug(repos["daylens"]) == "daylens"
    assert window_tracker.repo_remote_name(repos["daylens"]) == "Retra"
    assert window_tracker.repo_remote_name(repos["plain"]) is None, "same name: nothing extra"


def test_folder_with_agent_instructions_is_a_project(repos):
    assert window_tracker._cwd_to_project_slug(repos["planner"]) == "content-factory"


def test_home_itself_is_never_a_project(repos):
    (repos["home"] / "CLAUDE.md").write_text("x")
    (repos["home"] / "scratch").mkdir()
    assert window_tracker._cwd_to_project_slug(str(repos["home"] / "scratch")) is None


def test_aliases_go_both_ways(repos):
    a = window_tracker.project_aliases()
    assert a["daylens"] == {"Retra"} and a["retra"] == {"daylens"}


def _session(sid, cwd, project, at):
    return {"session_id": sid, "source": "claude-code", "cwd": cwd, "project": project, "branch": "main",
            "title": f"Session {sid}", "started_at": at.isoformat(), "ended_at": at.isoformat(), "prompt_count": 1,
            "files": "[]", "prs": "[]", "last_status": None, "last_reply": None, "log_path": f"/{sid}.jsonl",
            "log_size": 1, "log_mtime": 1.0}


def test_stored_sessions_get_renamed_and_found_by_repo_name(retra, repos):
    db = Database(os.environ["RETRA_DB_PATH"])
    at = retra.now - timedelta(hours=2)
    db.save_agent_session(_session("d1", repos["daylens"], "daylens", at), [])
    db.save_agent_session(_session("c1", repos["planner"], None, at), [])  # saved before the marker rule

    claude_sessions.refresh_session_names(db)

    async def sessions(project):
        async with Client(retra.mcp) as client:
            res = await client.call_tool("coding_sessions", {"project": project, "days": 2})
            return json.loads(res.content[0].text)["sessions"]

    found = asyncio.run(sessions("retra"))
    # The conftest fixture's own "retra" session matches too.
    assert ("daylens", "Retra") in [(s["project"], s["repo"]) for s in found]
    assert [s["project"] for s in asyncio.run(sessions("content-factory"))] == ["content-factory"]


def test_marker_projects_outside_home_are_found(repos, tmp_path):
    ext = tmp_path / "Volumes" / "ext" / "notes-vault"
    (ext / "sub").mkdir(parents=True)
    (ext / "AGENTS.md").write_text("x")
    assert window_tracker._cwd_to_project_slug(str(ext / "sub")) == "notes-vault"


def test_alias_matches_whole_names_only(repos, monkeypatch):
    from mcp_server import store
    spans = [store.Span(**{**dict.fromkeys(store.Span.__dataclass_fields__), "project": p})
             for p in ("daylens", "daylens-docs", "Retra")]
    got = [s.project for s in store.filter_project(spans, "retra")]
    assert got == ["daylens", "Retra"]


def test_blocked_repo_name_hides_the_session(monkeypatch):
    from mcp_server import store
    monkeypatch.setattr(store, "redact_text", lambda t: t.replace("SecretClient", "[private]"))
    assert store.session_is_private({"cwd": "/x/daylens", "project": "daylens", "repo": "SecretClient", "title": "t"})
    assert not store.session_is_private({"cwd": "/x/daylens", "project": "daylens", "title": "t"})


def test_refresh_picks_up_new_markers_and_keeps_missing_folders(retra, repos, tmp_path):
    db = Database(os.environ["RETRA_DB_PATH"])
    at = retra.now - timedelta(hours=2)
    late = tmp_path / "home" / "Desktop" / "late"
    late.mkdir()
    gone = str(tmp_path / "home" / "Desktop" / "gone")
    db.save_agent_session(_session("l1", str(late), None, at), [])
    db.save_agent_session(_session("g1", gone, "gone", at), [])
    assert window_tracker._cwd_to_project_slug(str(late)) is None  # cached as unnamed

    (late / "CLAUDE.md").write_text("x")
    claude_sessions.refresh_session_names(db)

    names = {cwd: (p, r) for cwd, p, r in db.get_agent_session_folders()}
    assert names[str(late)] == ("late", None)
    assert names[gone] == ("gone", None), "a missing folder keeps its saved name"
