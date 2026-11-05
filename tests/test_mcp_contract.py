"""
The MCP tool list is a contract with every agent that uses Retra. A renamed
tool or parameter breaks them silently, so it's pinned in a snapshot and each
tool and parameter must be documented in the README and the agent guide.

After a deliberate change, refresh the snapshot with:
    UPDATE_SNAPSHOTS=1 venv/bin/python -m pytest tests/test_mcp_contract.py
"""

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from mcp import Client

from config.settings import ObsidianConfig, Settings
from storage.database import Database
from storage.models import WindowEvent

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "snapshots" / "mcp_contract.json"
GUIDE = ROOT / "skills" / "retra" / "SKILL.md"


@pytest.fixture
def server(tmp_path, monkeypatch):
    """The real server, pointed at a fixture database and wiki, with call
    logging captured instead of written to data/."""
    db_path = tmp_path / "retra.db"
    db = Database(db_path)
    now = datetime.now().replace(microsecond=0)
    for i in range(6):
        db.insert_event(WindowEvent(
            timestamp=now - timedelta(minutes=30 - i * 5), app_name="Code",
            window_title="server.py - retra", category="development", project_hint="retra",
        ))
    db.save_agent_session({
        "session_id": "s1", "source": "claude-code", "cwd": "/work/retra", "project": "retra",
        "branch": "main", "title": "Add contract test", "started_at": (now - timedelta(hours=1)).isoformat(),
        "ended_at": now.isoformat(), "prompt_count": 1, "files": "[]", "prs": "[]",
        "last_status": "Snapshot written", "last_reply": None, "log_path": "/x.jsonl",
        "log_size": 1, "log_mtime": 1.0,
    }, [((now - timedelta(minutes=50)).isoformat(), "prompt", "write the contract test")])

    # Default settings, not the local config: fixed privacy rules, and a vault in tmp_path so
    # day_summary and wiki_page never read the developer's real notes.
    vault = tmp_path / "vault"
    settings = Settings(obsidian=ObsidianConfig(vault_path=str(vault)))
    settings.obsidian.output_dir.mkdir(parents=True)
    (settings.obsidian.output_dir / f"{date.today().isoformat()}.md").write_text("Fixture note\n")
    wiki = vault / "retra-wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "retra.md").write_text("# Retra\n")

    from config import settings as settings_mod
    from mcp_server import server as srv, store
    monkeypatch.setattr(settings_mod, "_settings", settings)
    monkeypatch.setenv("RETRA_DB_PATH", str(db_path))
    monkeypatch.setattr(store, "_db", None)
    calls = []
    monkeypatch.setattr(srv, "log_call", lambda tool, client, args, **kw: calls.append((tool, client, kw["ok"])))
    srv.logged_calls = calls
    yield srv.mcp
    monkeypatch.setattr(store, "_db", None)


def _run(coro):
    return asyncio.run(coro)


async def _list_tools(server):
    async with Client(server) as client:
        return (await client.list_tools()).tools


def _contract(tools) -> dict:
    out = {}
    for t in sorted(tools, key=lambda t: t.name):
        schema = t.input_schema or {}
        out[t.name] = {
            "params": {
                name: {"type": prop.get("type") or [a.get("type") for a in prop.get("anyOf", [])],
                       "default": prop.get("default")}
                for name, prop in sorted(schema.get("properties", {}).items())
            },
            "required": sorted(schema.get("required", [])),
            "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else {},
        }
    return out


def test_tools_match_snapshot(server):
    contract = _contract(_run(_list_tools(server)))
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(contract, indent=2) + "\n")
    assert SNAPSHOT.exists(), "no snapshot yet: run with UPDATE_SNAPSHOTS=1"
    assert contract == json.loads(SNAPSHOT.read_text()), (
        "MCP tools changed. Agents depend on these names and params. If the change "
        "is deliberate, update README.md and skills/retra/SKILL.md, then rerun with "
        "UPDATE_SNAPSHOTS=1."
    )


def test_every_tool_is_read_only(server):
    for t in _run(_list_tools(server)):
        assert t.annotations and t.annotations.read_only_hint is True, t.name
        assert t.annotations.open_world_hint is False, t.name


def test_ctx_is_hidden_from_agents(server):
    for t in _run(_list_tools(server)):
        assert "ctx" not in (t.input_schema or {}).get("properties", {}), t.name


def _readme_params(tool: str):
    """Params in the tool's signature in the README's MCP table, or None."""
    m = re.search(rf"^\| `{tool}\(([^)]*)\)`", (ROOT / "README.md").read_text(), re.M)
    return None if m is None else {a.split("=")[0].strip() for a in m.group(1).split(",") if a.strip()}


def _guide_params(tool: str):
    """Params in the bullets of the tool's section in skills/retra/SKILL.md, or None."""
    m = re.search(rf"^### {tool}\n(.*?)(?=^#|\Z)", GUIDE.read_text(), re.M | re.S)
    if m is None:
        return None
    bullets = re.findall(r"^- ((?:`\w+`(?:, )?)+)", m.group(1), re.M)
    return {name for b in bullets for name in re.findall(r"`(\w+)`", b)}


@pytest.mark.parametrize("documented", [_readme_params, _guide_params], ids=["README", "SKILL"])
def test_every_tool_and_param_is_documented(server, documented):
    problems = []
    for t in _run(_list_tools(server)):
        params = set((t.input_schema or {}).get("properties", {}))
        doc = documented(t.name)
        if doc is None:
            problems.append(f"{t.name} missing")
        elif doc != params:
            if params - doc:
                problems.append(f"{t.name} undocumented: {sorted(params - doc)}")
            if doc - params:
                problems.append(f"{t.name} documents unknown: {sorted(doc - params)}")
    assert not problems, "; ".join(problems)


def test_instructions_cover_every_tool(server):
    for t in _run(_list_tools(server)):
        assert t.name in server.instructions, t.name


SMOKE_CALLS = {
    "current_activity": {},
    "recent_work": {"days": 1},
    "day_summary": {"day": "today"},
    "search_activity": {"query": "server.py"},
    "search_screen_text": {"query": "nothing"},
    "coding_sessions": {},
    "search_coding_sessions": {"query": "contract"},
    "list_projects": {},
    "wiki_page": {"name": "retra"},
}


def test_every_tool_answers_on_fixture_db(server):
    from mcp_server import server as srv

    async def call_all():
        async with Client(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == set(SMOKE_CALLS), "add new tools to SMOKE_CALLS"
            return {n: await client.call_tool(n, args) for n, args in SMOKE_CALLS.items()}

    results = _run(call_all())
    for name, res in results.items():
        assert not res.is_error, f"{name}: {res.content}"
        body = json.loads(res.content[0].text)
        assert "error" not in body, f"{name}: {body['error']}"

    assert "Fixture note" in (json.loads(results["day_summary"].content[0].text)["obsidian_note"] or ""), \
        "day_summary didn't read the fixture vault"
    assert "Retra" in json.loads(results["wiki_page"].content[0].text)["content"]
    sessions = json.loads(results["coding_sessions"].content[0].text)["sessions"]
    assert [s["title"] for s in sessions] == ["Add contract test"], "not reading the fixture DB"
    assert json.loads(results["search_activity"].content[0].text)["hits"], "fixture event not found"
    assert json.loads(results["search_coding_sessions"].content[0].text)["hits"], "fixture session not found"
    assert [c[0] for c in srv.logged_calls] == list(SMOKE_CALLS)
    assert all(ok for _, _, ok in srv.logged_calls)
