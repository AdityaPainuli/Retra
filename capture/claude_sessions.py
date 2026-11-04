"""
Claude Code sessions as a source.

Claude Code keeps one JSONL log per session under ~/.claude/projects/<dir>/.
For each session this keeps what an agent needs to pick the work back up:
where it ran (cwd, project, branch), its title, what was asked, the turn
recaps, files edited and PRs opened (via `gh pr create`). Secrets are masked before anything is
stored. Logs are only re-read when their size or mtime changes.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from capture.screen_text import redact
from capture.window_tracker import _cwd_to_project_slug

log = logging.getLogger("retra")

MAX_PROMPT_CHARS = 2000
MAX_REPLY_CHARS = 1500
MAX_FILES = 50

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_RECAP_TOOL = "mcp__ccd_turn__wrap_up"
# Scratch files an agent writes along the way aren't part of the work.
_SCRATCH_DIRS = ("/tmp/", "/private/tmp/", "/private/var/folders/", "/var/folders/")
_PR_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
_COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>(?:.*?<command-args>(.*?)</command-args>)?", re.S)
# Injected text that shows up as a "user" message but wasn't typed by the user.
_NOT_A_PROMPT = (
    "<system-reminder>", "<local-command-", "<task-notification>", "<side-sessions-event>",
    "<ci-monitor-event>", "<bash-", "<user-memory-input>", "Caveat:", "[Request interrupted",
    "This session is being continued from a previous conversation",
)


def _prompt_text(content) -> Optional[str]:
    """The user's own words from a user message, or None for tool results
    and injected context."""
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        if not parts:
            return None  # tool results only
        content = "\n".join(parts)
    if not isinstance(content, str):
        return None
    text = content.strip()
    m = _COMMAND_RE.match(text)
    if m:
        return f"{m.group(1).strip()} {(m.group(2) or '').strip()}".strip()
    if not text or text.startswith(_NOT_A_PROMPT):
        return None
    return text


def _repo_dir(cwd: str) -> str:
    """Map a Claude Code worktree back to the repo it belongs to."""
    return re.split(r"/\.claude/worktrees/", cwd, maxsplit=1)[0]


def parse_session(path: Path) -> Optional[tuple[dict, list[tuple[str, str, str]]]]:
    """Read one session log. Returns (session row, messages) or None when the
    session has no prompts (e.g. opened and closed)."""
    cwd = branch = title = last_reply = last_status = None
    started = ended = None
    files: dict[str, str] = {}
    prs: list[str] = []
    pr_creates: set[str] = set()  # tool_use ids of `gh pr create` calls
    messages: list[tuple[str, str, str]] = []
    prompt_count = 0

    with path.open(errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = entry.get("type")
            if kind == "custom-title":
                title = entry.get("customTitle") or title
                continue
            if kind not in ("user", "assistant") or entry.get("isSidechain"):
                continue
            ts = entry.get("timestamp")
            if ts:
                started = started or ts
                ended = ts
            cwd = entry.get("cwd") or cwd
            branch = entry.get("gitBranch") or branch
            content = (entry.get("message") or {}).get("content")

            if kind == "user":
                if isinstance(content, list):
                    for block in content:
                        if (isinstance(block, dict) and block.get("type") == "tool_result"
                                and block.get("tool_use_id") in pr_creates):
                            for url in _PR_RE.findall(json.dumps(block.get("content"))):
                                if url not in prs:
                                    prs.append(url)
                if entry.get("isMeta"):
                    continue
                text = _prompt_text(content)
                if text:
                    prompt_count += 1
                    messages.append((ts, "prompt", redact(text)[:MAX_PROMPT_CHARS]))
                continue

            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    last_reply = block["text"].strip()
                elif block.get("type") == "tool_use":
                    name, args = block.get("name"), block.get("input") or {}
                    if (name in _EDIT_TOOLS and args.get("file_path")
                            and not args["file_path"].startswith(_SCRATCH_DIRS)):
                        files.pop(args["file_path"], None)
                        files[args["file_path"]] = ts  # keeps most recent last
                    elif name == "Bash" and "gh pr create" in str(args.get("command", "")):
                        pr_creates.add(block.get("id"))
                    elif name == _RECAP_TOOL and args.get("recap"):
                        last_status = redact(args["recap"])
                        messages.append((ts, "recap", last_status))

    if not prompt_count:
        return None

    repo = _repo_dir(cwd) if cwd else None
    session = {
        "session_id": path.stem,
        "source": "claude-code",
        "cwd": repo,
        "project": _cwd_to_project_slug(repo) if repo else None,
        "branch": branch,
        "title": title or next(t for _, k, t in messages if k == "prompt")[:100],
        "started_at": _local(started),
        "ended_at": _local(ended),
        "prompt_count": prompt_count,
        "files": json.dumps(list(reversed(files))[:MAX_FILES]),
        "prs": json.dumps(prs),
        "last_status": last_status,
        "last_reply": redact(last_reply)[:MAX_REPLY_CHARS] if last_reply else None,
    }
    return session, [(_local(ts), k, t) for ts, k, t in messages]


def _local(ts: Optional[str]) -> Optional[str]:
    """Claude Code logs UTC ('...Z'); Retra stores naive local time."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat()
    except ValueError:
        return None


def sync_sessions(db, logs_dir: Path, retention_days: int = 0,
                  should_stop: Callable[[], bool] = lambda: False) -> int:
    """Ingest new or changed session logs. Returns how many were saved."""
    if not logs_dir.is_dir():
        return 0
    known = db.get_agent_log_state()
    cutoff = datetime.now() - timedelta(days=retention_days) if retention_days > 0 else None
    saved = 0
    # Top-level logs only; subagent transcripts live in <session>/subagents/.
    for path in logs_dir.glob("*/*.jsonl"):
        if should_stop():
            break
        try:
            stat = path.stat()
        except OSError:
            continue
        if known.get(str(path)) == (stat.st_size, stat.st_mtime):
            continue
        if cutoff and datetime.fromtimestamp(stat.st_mtime) < cutoff:
            continue
        try:
            parsed = parse_session(path)
        except OSError as e:
            log.warning(f"Couldn't read Claude Code session {path.name}: {e}")
            continue
        if parsed is None:
            continue
        session, messages = parsed
        session.update(log_path=str(path), log_size=stat.st_size, log_mtime=stat.st_mtime)
        db.save_agent_session(session, messages)
        saved += 1
    return saved
