"""
Git commits as a source.

A coding session's turns say what was planned; the commits say what shipped
and why. A squash-merged PR's commit carries the PR description as its body.
This reads `git log --all` of every local repo Retra knows (the folders
coding sessions ran in, plus repos in the usual dev folders) and keeps each
of the user's commits (authored with the repo's git user.email or
user.name; a cloned open-source repo would otherwise bring thousands of other
people's): time,
author, subject and body. Secrets are masked before anything
is stored. Only commits newer than the last sync (or the retention window)
are read.
"""

import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from capture.screen_text import redact
from capture.window_tracker import _cwd_to_project_slug, _local_repos, repo_remote_name

log = logging.getLogger("retra")

MAX_BODY_CHARS = 3000
DEFAULT_DAYS = 90
GIT_TIMEOUT = 30
_FIELD, _RECORD = "\x1f", "\x1e"
_FORMAT = _FIELD.join(["%H", "%cI", "%aI", "%an", "%ae", "%s", "%b"]) + _RECORD


def git_root(folder: str) -> Optional[Path]:
    try:
        p = Path(os.path.expanduser(folder))
        return next((d for d in [p, *p.parents] if (d / ".git").exists()), None)
    except Exception:
        return None


def known_repos(db) -> list[Path]:
    """Git roots of the folders coding sessions ran in, plus repos directly
    under the usual dev folders."""
    roots = {git_root(cwd) for cwd, _, _ in db.get_agent_session_folders()}
    roots |= {p for p in _local_repos() if (p / ".git").exists()}
    return sorted(r for r in roots if r is not None)


def _local(iso: str) -> str:
    """Git's ISO time with offset -> naive local time, as Retra stores it."""
    return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None).isoformat()


def _git_user(root: Path) -> set[str]:
    """The repo's git user.email and user.name, lowercased. Both count: a PR
    squash-merged on GitHub is authored with a noreply address."""
    me = set()
    for key in ("user.email", "user.name"):
        out = subprocess.run(["git", "-C", str(root), "config", key], capture_output=True, text=True,
                             timeout=GIT_TIMEOUT)
        if value := out.stdout.strip().lower():
            me.add(value)
    return me


def read_commits(root: Path, since: datetime) -> list[dict]:
    """The user's commits on any branch of `root` committed after `since`,
    newest first. With no git user configured, everyone's."""
    me = _git_user(root)
    out = subprocess.run(
        ["git", "-C", str(root), "log", "--all", f"--since={since.isoformat()}", f"--format={_FORMAT}"],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200] or f"git log exited {out.returncode}")
    project, repo = _cwd_to_project_slug(str(root)), repo_remote_name(str(root))
    commits = []
    for raw in out.stdout.split(_RECORD):
        parts = raw.strip("\n").split(_FIELD)
        if len(parts) != 7:
            continue
        sha, committed, authored, author, email, subject, body = parts
        if me and not {email.lower(), author.lower()} & me:
            continue
        commits.append({
            "repo_path": str(root), "sha": sha, "project": project, "repo": repo,
            "committed_at": _local(committed), "authored_at": _local(authored),
            "author": author, "subject": redact(subject.strip()),
            "body": redact(body.strip())[:MAX_BODY_CHARS] or None,
        })
    return commits


def sync_commits(db, retention_days: int = 0, should_stop: Callable[[], bool] = lambda: False) -> int:
    """Store commits new since each repo's last sync. Returns how many."""
    days = retention_days if retention_days > 0 else DEFAULT_DAYS
    floor = datetime.now() - timedelta(days=days)
    latest = db.get_commit_sync_state()
    saved = 0
    for root in known_repos(db):
        if should_stop():
            break
        # A day of overlap: a rebase or a late push can land older commit times.
        since = max(floor, datetime.fromisoformat(latest[str(root)]) - timedelta(days=1)) \
            if str(root) in latest else floor
        try:
            commits = read_commits(root, since)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            log.warning(f"Couldn't read commits of {root.name}: {e}")
            continue
        saved += db.save_commits(commits)
    return saved
