"""
Version history for the wiki.

The compiler overwrites whole pages with whatever the LLM returns, so a bad
compile can silently drop a correct fact. Every write is committed to a git
repo whose .git lives in Retra's data dir (data/wiki.git), with the wiki
folder as its work tree. Nothing is added to the Obsidian vault, and any page
can be diffed or restored with plain git.
"""

import fcntl
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from config.settings import DATA_DIR

WIKI_GIT_DIR = DATA_DIR / "wiki.git"
WIKI_LOCK_PATH = DATA_DIR / "wiki.lock"
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty tree


@contextmanager
def lock(lock_path: Optional[Path] = None) -> Iterator[None]:
    """Hold the wiki for one writer at a time, across processes (the nightly
    job, dashboard and menubar can all compile). Wrap everything from the
    pre-write snapshot to the final commit, so one compile's pages can't be
    committed under another's message."""
    path = lock_path or WIKI_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _git(wiki_dir: Path, *args: str, git_dir: Optional[Path] = None) -> subprocess.CompletedProcess:
    git_dir = git_dir or WIKI_GIT_DIR
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={wiki_dir}",
         "-c", "user.name=Retra", "-c", "user.email=retra@localhost", *args],
        capture_output=True, text=True, timeout=30,
    )


def commit(wiki_dir: Path, message: str, git_dir: Optional[Path] = None) -> Optional[str]:
    """Commit the wiki's current state. Returns the short commit id, or None
    when nothing changed or git failed (never raises: history is a bonus,
    not a reason to fail a compile)."""
    git_dir = git_dir or WIKI_GIT_DIR
    try:
        if not (git_dir / "HEAD").exists():
            if _git(wiki_dir, "init", "-q", git_dir=git_dir).returncode != 0:
                return None
        _git(wiki_dir, "add", "-A", git_dir=git_dir)
        if _git(wiki_dir, "diff", "--cached", "--quiet", git_dir=git_dir).returncode == 0:
            return None
        if _git(wiki_dir, "commit", "-q", "-m", message, git_dir=git_dir).returncode != 0:
            return None
        return _git(wiki_dir, "rev-parse", "--short", "HEAD", git_dir=git_dir).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[Retra] Wiki history commit failed: {e}")
        return None


def diff(wiki_dir: Path, day: Optional[str] = None, git_dir: Optional[Path] = None) -> str:
    """What compiles changed: the latest commit, or every commit made on
    `day` (YYYY-MM-DD)."""
    git_dir = git_dir or WIKI_GIT_DIR
    if not (git_dir / "HEAD").exists():
        return "No wiki history yet (it starts with the next compile)."
    if day:
        commits = _git(wiki_dir, "log", "--reverse", "--format=%H",
                       f"--since={day} 00:00", f"--until={day} 23:59:59", git_dir=git_dir).stdout.split()
        if not commits:
            return f"No wiki changes on {day}."
        first, last = commits[0], commits[-1]
        has_parent = _git(wiki_dir, "rev-parse", "--verify", "-q", f"{first}^", git_dir=git_dir).returncode == 0
        base = f"{first}^" if has_parent else _EMPTY_TREE
        span = f"{first}^..{last}" if has_parent else last
        out = _git(wiki_dir, "log", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M",
                   span, git_dir=git_dir).stdout
        return out + "\n" + _git(wiki_dir, "diff", base, last, git_dir=git_dir).stdout
    return _git(wiki_dir, "show", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M",
                "HEAD", git_dir=git_dir).stdout
