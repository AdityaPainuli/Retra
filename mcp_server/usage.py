"""
Local log of MCP tool calls, so we can tell whether agents actually use Retra.

One JSON line per call in data/mcp_usage.jsonl. It never leaves the machine
and is kept out of the database, which the MCP server only opens read-only.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from config.settings import DATA_DIR

USAGE_PATH = DATA_DIR / "mcp_usage.jsonl"
MAX_ARG_CHARS = 200


def log_call(tool: str, client: str | None, args: dict, ms: int, ok: bool, chars: int,
             path: Path = USAGE_PATH):
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "client": client or "unknown",
        "tool": tool,
        "args": {k: v if not isinstance(v, str) else v[:MAX_ARG_CHARS]
                 for k, v in args.items() if v is not None},
        "ms": ms,
        "ok": ok,
        "chars": chars,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break a tool call


def load_calls(path: Path = USAGE_PATH) -> list[dict]:
    if not path.exists():
        return []
    calls = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            calls.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return calls


def weekly_report(weeks: int = 8, path: Path = USAGE_PATH) -> str:
    """Calls per week (Monday start) by client and tool, newest first."""
    calls = load_calls(path)
    if not calls:
        return f"No agent calls logged yet ({path})."

    this_monday = date.today() - timedelta(days=date.today().weekday())
    first = this_monday - timedelta(weeks=weeks - 1)
    by_week: dict[date, list[dict]] = defaultdict(list)
    for c in calls:
        d = datetime.fromisoformat(c["ts"]).date()
        week = d - timedelta(days=d.weekday())
        if week >= first:
            by_week[week].append(c)

    lines = [f"Agent calls to Retra, last {weeks} weeks ({len(calls)} logged in total)", ""]
    for i in range(weeks):
        week = this_monday - timedelta(weeks=i)
        wc = by_week.get(week, [])
        failed = sum(1 for c in wc if not c.get("ok", True))
        head = f"Week of {week.isoformat()}: {len(wc)} call(s)"
        if failed:
            head += f", {failed} failed"
        lines.append(head)
        if not wc:
            continue
        clients = Counter(c["client"] for c in wc)
        tools = Counter(c["tool"] for c in wc)
        lines.append("  clients: " + ", ".join(f"{n} {k}" for k, n in clients.most_common()))
        lines.append("  tools:   " + ", ".join(f"{n} {k}" for k, n in tools.most_common()))
    return "\n".join(lines)
