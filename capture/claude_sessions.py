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
# `gh pr create` prints the new PR's URL on a line of its own, sometimes behind
# a short label the command adds ("admin: <url>"). Only such lines count:
# other output (a test run, `gh pr list`) can mention many PRs. Read results
# prefix each line with its number.
_PR_LINE_RE = re.compile(
    r"^(?:\s*\d+\t)?(?:[\w .-]{1,30}:\s*)?(https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)\s*$", re.M)
# The command must run `gh pr create` (at the start of a line or after ;, &&,
# ||, a pipe or a subshell), not just mention it: a script that edits this
# parser has the phrase inside a string. Matched against _shell_code().
_PR_CREATE_RE = re.compile(r"(?:^|[;&|(])\s*gh\s+pr\s+create\b", re.M)
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
_READ_CMD = r"(?:^|[;&|(])\s*(?:cat|tail|head|less|more)\b[^;&|#\n]*"
# A long command is moved to the background; its output lands in a file that
# is read later, so the PR URL shows up in that later read.
_BACKGROUND_RE = re.compile(r"Output is being written to: (\S+?\.output)")
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


def _shell_code(command: str) -> str:
    """The command with literal text blanked out, so only what the shell runs
    is left: single-quoted strings, comments, heredoc bodies, and double-quoted
    text outside $(...). Unquoted heredoc bodies are blanked too; a command
    substitution inside one is rare enough to miss."""
    out, i, n = [], 0, len(command)
    in_dq = False
    stack = []  # for each open paren: whether it was opened inside double quotes
    heredocs = []  # delimiters whose bodies start after the current line
    while i < n:
        c = command[i]
        if in_dq:
            if c == "\\":
                i += 2
            elif c == '"':
                in_dq = False
                i += 1
            elif command.startswith("$(", i):
                stack.append(True)
                in_dq = False
                out.append("(")
                i += 2
            else:
                i += 1
            continue
        if c == "\n" and heredocs:
            out.append(c)
            i += 1
            for delim in heredocs:
                while i < n:
                    end = command.find("\n", i)
                    end = n if end < 0 else end
                    line, i = command[i:end], end + 1
                    if line.strip() == delim:
                        break
            heredocs = []
            continue
        if c == "\\":
            out.append(command[i:i + 2])
            i += 2
        elif c == "'":
            end = command.find("'", i + 1)
            i = n if end < 0 else end + 1
            out.append(" ")
        elif c == '"':
            in_dq = True
            out.append(" ")
            i += 1
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
            end = command.find("\n", i)
            i = n if end < 0 else end
        elif (m := _HEREDOC_RE.match(command, i)) and not command.startswith("<<<", i):
            heredocs.append(m.group(2))
            out.append(" ")
            i = m.end()
        elif c == "(":
            stack.append(False)
            out.append(c)
            i += 1
        elif c == ")":
            in_dq = stack.pop() if stack else False
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _runs_pr_create(command: str) -> bool:
    return bool(_PR_CREATE_RE.search(_shell_code(command)))


def _reads_file(command: str, path: str) -> bool:
    return bool(re.search(_READ_CMD + re.escape(path), command, re.M))


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def parse_session(path: Path) -> Optional[tuple[dict, list[tuple[str, str, str]]]]:
    """Read one session log. Returns (session row, messages) or None when the
    session has no prompts (e.g. opened and closed)."""
    cwd = branch = title = last_reply = last_status = None
    started = ended = None
    files: dict[str, str] = {}
    prs: list[str] = []
    pr_creates: set[str] = set()  # tool_use ids whose result holds a new PR's URL
    pr_outputs: set[str] = set()  # output files of backgrounded `gh pr create` calls
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
                            result = _result_text(block.get("content"))
                            for url in _PR_LINE_RE.findall(result):
                                if url not in prs:
                                    prs.append(url)
                            pr_outputs.update(_BACKGROUND_RE.findall(result))
                if entry.get("isMeta"):
                    continue
                text = _prompt_text(content)
                if text:
                    prompt_count += 1
                    messages.append((ts, "prompt", redact(text)[:MAX_PROMPT_CHARS]))
                    # The stopping point is the latest turn's; older recaps stay in messages.
                    last_status = last_reply = None
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
                    elif name == "Bash" and _runs_pr_create(str(args.get("command", ""))):
                        pr_creates.add(block.get("id"))
                    elif pr_outputs and (
                        (name == "Bash" and any(_reads_file(str(args.get("command", "")), p) for p in pr_outputs))
                        or (name == "Read" and args.get("file_path") in pr_outputs)
                    ):
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
        "title": redact(title)[:100] if title else next(t for _, k, t in messages if k == "prompt")[:100],
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
        # mtime can be recent for an old session (copied log, renamed title).
        if cutoff and session["ended_at"] and datetime.fromisoformat(session["ended_at"]) < cutoff:
            continue
        session.update(log_path=str(path), log_size=stat.st_size, log_mtime=stat.st_mtime)
        db.save_agent_session(session, messages)
        saved += 1
    return saved
