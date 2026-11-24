"""What shipped and what was answered: each turn's final reply is kept with
its time, and git commits (with squash-merged PR descriptions) are a source
agents can search and see per day."""

import asyncio
import json
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from mcp import Client

from capture import claude_sessions, git_commits
from capture.claude_sessions import PARSE_VERSION, parse_session, sync_sessions
from storage.database import Database


def entry(kind, ts, content):
    return {"type": kind, "timestamp": ts, "cwd": "/work/app", "message": {"content": content}}


def text(t):
    return [{"type": "text", "text": t}]


LOG = [
    entry("user", "2026-11-09T08:00:00Z", "what's next?"),
    entry("assistant", "2026-11-09T08:01:00Z", text("Looking.")),
    entry("assistant", "2026-11-09T08:02:00Z", text("Next: D3 wiki search, then D4 evals.")),
    entry("user", "2026-11-09T09:00:00Z", "merge it"),
    entry("assistant", "2026-11-09T09:01:00Z", text("Merged PR 14.")),
]


def write_log(path: Path, entries=LOG):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def test_each_turns_final_reply_is_kept_with_its_time(tmp_path):
    session, messages = parse_session(write_log(tmp_path / "s.jsonl"))
    replies = [(ts, t) for ts, k, t in messages if k == "reply"]
    assert [t for _, t in replies] == ["Next: D3 wiki search, then D4 evals.", "Merged PR 14."]
    assert replies[0][0] == claude_sessions._local("2026-11-09T08:02:00Z"), "the reply's own time"
    assert session["last_reply"] == "Merged PR 14."


def test_logs_read_by_an_older_parser_are_read_again(tmp_path):
    db = Database(tmp_path / "r.db")
    logs = tmp_path / "logs"
    (logs / "p").mkdir(parents=True)
    write_log(logs / "p" / "s1.jsonl")
    assert sync_sessions(db, logs) == 1 and sync_sessions(db, logs) == 0
    with db._conn() as c:
        c.execute("UPDATE agent_sessions SET parse_version = ?", (PARSE_VERSION - 1,))
    assert sync_sessions(db, logs) == 1


def git(repo: Path, *args, when=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Me", "GIT_AUTHOR_EMAIL": "me@x", "GIT_COMMITTER_NAME": "Me",
           "GIT_COMMITTER_EMAIL": "me@x"}
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "daylens"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "me@x")
    git(repo, "config", "user.name", "Me")
    (repo / "a.txt").write_text("a")
    git(repo, "add", ".")
    yesterday = (datetime.now() - timedelta(days=1)).replace(hour=23, minute=18, second=0, microsecond=0)
    git(repo, "commit", "-q", "-m", "Find coding sessions by their repo's GitHub name too (#14)\n\n"
        "Sessions are named after their folder. The repo's GitHub name is stored as repo.\n"
        "token=sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789", when=yesterday.isoformat())
    return repo


def test_read_commits_masks_secrets(repo):
    [c] = git_commits.read_commits(repo, datetime.now() - timedelta(days=5))
    assert c["subject"].endswith("(#14)") and c["project"] == "daylens"
    assert "named after their folder" in c["body"] and "sk-ant-api03" not in c["body"]


def test_sync_reads_known_repos_once(repo, tmp_path, monkeypatch):
    db = Database(tmp_path / "r.db")
    monkeypatch.setattr(git_commits, "known_repos", lambda db: [repo])
    assert git_commits.sync_commits(db) == 1 and git_commits.sync_commits(db) == 0
    (repo / "b.txt").write_text("b")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Add evals")
    assert git_commits.sync_commits(db) == 1


@pytest.fixture
def stored(retra, repo, monkeypatch):
    db = Database(os.environ["RETRA_DB_PATH"])
    db.save_commits(git_commits.read_commits(repo, datetime.now() - timedelta(days=5)))
    private = tmp = repo.parent / "bank-app"
    tmp.mkdir()
    git(tmp, "init", "-q")
    (tmp / "x").write_text("x")
    git(tmp, "add", ".")
    git(tmp, "commit", "-q", "-m", "Rename project folder settings")
    db.save_commits(git_commits.read_commits(private, datetime.now() - timedelta(days=5)))
    return retra


def call(retra, tool, **args):
    async def go():
        async with Client(retra.mcp) as client:
            return json.loads((await client.call_tool(tool, args)).content[0].text)
    return asyncio.run(go())


def test_commits_search_by_words_and_project(stored):
    hits = call(stored, "commits", query="folder named", project="daylens")["commits"]
    assert len(hits) == 1 and hits[0]["pr"] == 14 and "named after their folder" in hits[0]["body"]
    assert call(stored, "commits", query="nam fold")["commits"][0]["pr"] == 14, "prefixes count"
    assert all("bank" not in (c["project"] or "") for c in call(stored, "commits")["commits"]), \
        "blocked repos stay hidden"


def test_day_summary_lists_that_days_commits(stored):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    commits = call(stored, "day_summary", day=yesterday)["commits"]
    assert [(c["at"], c["subject"][-5:]) for c in commits] == [("23:18", "(#14)")]


def test_only_the_users_commits_are_kept(repo):
    (repo / "c.txt").write_text("c")
    git(repo, "add", ".")
    env_commit = ["-c", "user.name=Other", "-c", "user.email=other@x", "commit", "-q", "--author=Other <other@x>",
                  "-m", "Someone else's change"]
    git(repo, *env_commit)
    git(repo, "commit", "-q", "--allow-empty", "--author=Me <123+me@users.noreply.github.com>",
        "-m", "Squash-merged on GitHub (#15)")
    subjects = [c["subject"] for c in git_commits.read_commits(repo, datetime.now() - timedelta(days=5))]
    assert subjects == ["Squash-merged on GitHub (#15)", "Find coding sessions by their repo's GitHub name too (#14)"]


def test_a_commit_in_two_worktrees_is_listed_once(stored, repo):
    db = Database(os.environ["RETRA_DB_PATH"])
    copy = [dict(c, repo_path=c["repo_path"] + "-worktree") for c in
            git_commits.read_commits(repo, datetime.now() - timedelta(days=5))]
    db.save_commits(copy)
    assert [c["pr"] for c in call(stored, "commits", project="daylens")["commits"]] == [14]


def test_session_search_also_shows_matching_commits(stored):
    out = call(stored, "search_coding_sessions", query="named folder")
    assert [c["subject"][-5:] for c in out["commits"]] == ["(#14)"]
    assert "commits" not in call(stored, "search_coding_sessions", query="named folder", offset=1)


def test_a_fetched_commit_older_than_stored_ones_is_still_read(repo, tmp_path, monkeypatch):
    db = Database(tmp_path / "r.db")
    monkeypatch.setattr(git_commits, "known_repos", lambda db: [repo])
    (repo / "new.txt").write_text("n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Monday work", when=datetime.now().isoformat())
    assert git_commits.sync_commits(db) == 2
    # A branch fetched later whose commit is days older than the newest stored one.
    git(repo, "checkout", "-q", "-b", "old-pr", "HEAD~1")
    (repo / "old.txt").write_text("o")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Friday squash merge (#20)",
        when=(datetime.now() - timedelta(days=4)).isoformat())
    assert git_commits.sync_commits(db) == 1


def test_repo_without_a_git_identity_is_skipped(tmp_path, monkeypatch):
    repo = tmp_path / "cloned"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a").write_text("a")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Someone's commit")
    monkeypatch.setattr(git_commits, "_git_user", lambda root: set())
    assert git_commits.read_commits(repo, datetime.now() - timedelta(days=5)) == []


def test_retention_cleanup_removes_old_commits_and_their_search_entries(repo, tmp_path):
    db = Database(tmp_path / "r.db")
    old = git_commits.read_commits(repo, datetime.now() - timedelta(days=5))
    db.save_commits([dict(c, committed_at=(datetime.now() - timedelta(days=40)).isoformat()) for c in old])
    db.cleanup_old_data(event_retention_days=30)
    with db._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM git_commits").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM git_commits_fts WHERE git_commits_fts MATCH 'folder'").fetchone()[0] == 0
