"""
Register Retra's MCP server and agent skill with local coding agents.

`python main.py install <agent>` adds `retra` to the agent's user-level MCP
config and copies skills/retra/ into its skill directory. It only ever
touches its own entry and its own files:

- MCP entry: exact match = nothing to do, matches what we installed last
  time = replace, anything else (a hand-edited or foreign `retra`) = refuse.
- Skill files: a receipt with the sha256 of every file we wrote lets a
  reinstall or uninstall refuse to overwrite or delete files the user edited.

Every check runs before the first write, and every write is atomic.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "retra"
SKILL_NAME = "retra"
RECEIPT_NAME = ".retra-install.json"
LOCK_NAME = "agent-install.lock"

# User-level locations, relative to the home dir.
HOSTS = {
    "claude": {
        "id": "claude",
        "name": "Claude Code",
        "user": {
            "skillDirectory": ".claude/skills",
            "mcpConfig": {"kind": "json", "relativePath": ".claude.json", "key": "mcpServers"},
        },
    },
    "codex": {
        "id": "codex",
        "name": "Codex",
        "user": {
            "skillDirectory": ".agents/skills",
            "mcpConfig": {"kind": "toml", "relativePath": ".codex/config.toml", "key": "mcp_servers"},
        },
    },
    "cursor": {
        "id": "cursor",
        "name": "Cursor",
        "user": {
            "skillDirectory": ".cursor/skills",
            "mcpConfig": {"kind": "json", "relativePath": ".cursor/mcp.json", "key": "mcpServers"},
        },
    },
}


class InstallError(Exception):
    """Refused: something the user owns is in the way. Nothing was written."""


def server_entry(host_id: str, root: Path = PROJECT_ROOT) -> dict:
    """The MCP entry for this checkout: its venv's Python running main.py mcp,
    with absolute paths so the agent doesn't need the user's shell."""
    python = root / "venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)
    entry = {"command": str(python), "args": [str(root / "main.py"), "mcp"]}
    if host_id == "claude":
        # The shape `claude mcp add` writes, so a manual setup from the
        # README counts as an exact match.
        return {"type": "stdio", **entry, "env": {}}
    return entry


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _install_lock(root: Path):
    """One install or uninstall at a time, so two runs can't interleave
    their check-then-write steps."""
    path = root / "data" / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── MCP config files ─────────────────────────────────────────


class _JsonConfig:
    def __init__(self, path: Path, key: str):
        self.path, self.key = path, key
        self.raw = path.read_bytes() if path.exists() else None
        text = self.raw.decode() if self.raw is not None else ""
        try:
            self.doc = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as e:
            raise InstallError(f"{path} is not valid JSON ({e}); fix it by hand first.")
        if not isinstance(self.doc, dict) or not isinstance(self.doc.get(key, {}), dict):
            raise InstallError(f"{path} has an unexpected shape; fix it by hand first.")

    def get(self) -> dict | None:
        return self.doc.get(self.key, {}).get(SERVER_NAME)

    def render(self, entry: dict | None) -> bytes:
        doc = copy.deepcopy(self.doc)
        servers = doc.setdefault(self.key, {})
        if entry is None:
            servers.pop(SERVER_NAME, None)
        else:
            servers[SERVER_NAME] = entry
        return (json.dumps(doc, indent=2) + "\n").encode()


class _TomlConfig:
    """Edits only the `[mcp_servers.retra]` table in place (plus its
    subtables), so the user's comments and formatting elsewhere survive.
    The result is re-parsed and checked before it is written."""

    def __init__(self, path: Path, key: str):
        self.path, self.key = path, key
        self.raw = path.read_bytes() if path.exists() else None
        self.text = self.raw.decode() if self.raw is not None else ""
        try:
            self.doc = tomllib.loads(self.text)
        except tomllib.TOMLDecodeError as e:
            raise InstallError(f"{path} is not valid TOML ({e}); fix it by hand first.")

    def get(self) -> dict | None:
        return self.doc.get(self.key, {}).get(SERVER_NAME)

    def _without_table(self) -> str:
        table = re.escape(f"{self.key}.{SERVER_NAME}")
        header = re.compile(rf"^\s*\[\s*{table}\s*(\]|\.)")
        any_header = re.compile(r"^\s*\[")
        out, skipping = [], False
        for line in self.text.splitlines(keepends=True):
            if any_header.match(line):
                skipping = bool(header.match(line))
            if not skipping:
                out.append(line)
        return "".join(out).rstrip("\n")

    def render(self, entry: dict | None) -> bytes:
        text = self._without_table()
        if entry is not None:
            lines = [f"[{self.key}.{SERVER_NAME}]"]
            # JSON's escapes are valid TOML, except its surrogate pairs for
            # characters outside the BMP: keep those as UTF-8.
            lines += [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in entry.items()]
            text = (text + "\n\n" if text else "") + "\n".join(lines)
        text = text + "\n" if text else ""

        expected = copy.deepcopy(self.doc)
        servers = expected.setdefault(self.key, {})
        if entry is None:
            servers.pop(SERVER_NAME, None)
        else:
            servers[SERVER_NAME] = entry
        try:
            result = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            result = None
        if result is None or _drop_empty(result, self.key) != _drop_empty(expected, self.key):
            raise InstallError(
                f"{self.path} defines `{self.key}.{SERVER_NAME}` in a form this installer "
                f"can't edit safely (inline table or dotted keys). Edit it by hand."
            )
        return text.encode()


def _drop_empty(doc: dict, key: str) -> dict:
    """`[mcp_servers]` with nothing left in it parses the same as no table."""
    return {k: v for k, v in doc.items() if not (k == key and v == {})}


def _write_config(config, data: bytes):
    """Write the config unless it changed since it was read (an agent or
    the user saved it meanwhile), which would otherwise be overwritten."""
    now = config.path.read_bytes() if config.path.exists() else None
    if now != config.raw:
        raise InstallError(f"{config.path} changed while Retra was editing it; run the command again.")
    _atomic_write(config.path, data)


def _open_config(host: dict, home: Path):
    cfg = host["user"]["mcpConfig"]
    path = home / cfg["relativePath"]
    if cfg["kind"] == "toml":
        return _TomlConfig(path, cfg["key"])
    return _JsonConfig(path, cfg["key"])


# ── Skill files ──────────────────────────────────────────────


def _skill_source(root: Path) -> dict[str, bytes]:
    src = root / "skills" / SKILL_NAME
    return {
        p.relative_to(src).as_posix(): p.read_bytes()
        for p in sorted(src.rglob("*"))
        if p.is_file() and p.name != RECEIPT_NAME
    }


def _read_receipt(skill_dir: Path) -> dict:
    path = skill_dir / RECEIPT_NAME
    if not path.exists():
        return {}
    try:
        receipt = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise InstallError(f"{path} is unreadable; remove {skill_dir} by hand if you no longer need it.")
    # Install and uninstall delete the files a receipt lists, so each must
    # stay inside the skill directory.
    files = receipt.get("files", {}) if isinstance(receipt, dict) else None
    root = skill_dir.resolve()
    if not isinstance(files, dict) or not all(
        isinstance(rel, str) and rel and not Path(rel).is_absolute() and ".." not in Path(rel).parts
        and (skill_dir / rel).resolve().is_relative_to(root) and (skill_dir / rel).resolve() != root
        for rel in files
    ):
        raise InstallError(f"{path} was edited by hand; remove {skill_dir} by hand if you no longer need it.")
    return receipt


def _edited(skill_dir: Path, rel: str, receipt_files: dict) -> bool:
    """True when a file is there but isn't what we wrote."""
    path = skill_dir / rel
    return path.exists() and _sha256(path.read_bytes()) != receipt_files.get(rel)


# ── Commands ─────────────────────────────────────────────────


@dataclass
class Plan:
    host: dict
    config: object
    config_bytes: bytes | None
    skill_dir: Path
    writes: dict[str, bytes]
    deletes: list[str]
    receipt: bytes | None


def _host(agent: str) -> dict:
    if agent not in HOSTS:
        raise InstallError(f"Unknown agent: {agent}. Available: {', '.join(HOSTS)}")
    return HOSTS[agent]


def _plan_install(agent: str, home: Path, root: Path) -> Plan:
    host = _host(agent)
    config = _open_config(host, home)
    skill_dir = home / host["user"]["skillDirectory"] / SKILL_NAME
    receipt = _read_receipt(skill_dir)
    entry = server_entry(agent, root)

    current = config.get()
    if current == entry:
        config_bytes = None
    elif current is None or current == receipt.get("mcp_entry"):
        config_bytes = config.render(entry)
    else:
        raise InstallError(
            f"{config.path} already has a `{SERVER_NAME}` MCP server that Retra didn't write:\n"
            f"  {json.dumps(current)}\n"
            f"Remove it (or make it match the entry below) and run install again:\n"
            f"  {json.dumps(entry)}"
        )

    source = _skill_source(root)
    old_files = receipt.get("files", {})
    writes, deletes, edited = {}, [], []
    for rel, data in source.items():
        path = skill_dir / rel
        if path.exists() and path.read_bytes() == data:
            continue
        if _edited(skill_dir, rel, old_files):
            edited.append(rel)
        writes[rel] = data
    for rel in old_files:
        if rel not in source and (skill_dir / rel).exists():
            if _edited(skill_dir, rel, old_files):
                edited.append(rel)
            deletes.append(rel)
    if edited:
        raise InstallError(
            f"These files in {skill_dir} were changed since Retra installed them "
            f"(or Retra never installed them), so they were left alone:\n"
            + "".join(f"  {rel}\n" for rel in edited)
            + "Move your changes elsewhere or delete the files, then run install again."
        )

    new_receipt = {
        "agent": agent,
        "mcp_entry": entry,
        "files": {rel: _sha256(data) for rel, data in source.items()},
    }
    receipt_bytes = (json.dumps(new_receipt, indent=2) + "\n").encode()
    receipt_path = skill_dir / RECEIPT_NAME
    if receipt_path.exists() and receipt_path.read_bytes() == receipt_bytes:
        receipt_bytes = None
    return Plan(host, config, config_bytes, skill_dir, writes, deletes, receipt_bytes)


def install(agent: str, home: Path | None = None, root: Path = PROJECT_ROOT) -> list[str]:
    """Install for one agent. Returns what changed (empty = already installed)."""
    home = home or Path.home()
    with _install_lock(root):
        return _install(agent, home, root)


def _install(agent: str, home: Path, root: Path) -> list[str]:
    plan = _plan_install(agent, home, root)
    changed = []
    if plan.config_bytes is not None:
        _write_config(plan.config, plan.config_bytes)
        changed.append(f"MCP server `{SERVER_NAME}` in {plan.config.path}")
    for rel, data in plan.writes.items():
        _atomic_write(plan.skill_dir / rel, data)
        changed.append(f"skill file {plan.skill_dir / rel}")
    for rel in plan.deletes:
        (plan.skill_dir / rel).unlink()
        changed.append(f"removed old skill file {plan.skill_dir / rel}")
    if plan.receipt is not None:
        _atomic_write(plan.skill_dir / RECEIPT_NAME, plan.receipt)
    return changed


def uninstall(agent: str, home: Path | None = None, root: Path = PROJECT_ROOT) -> list[str]:
    """Remove Retra's MCP entry and skill files for one agent, but only what
    is exactly as Retra left it. Returns what was removed."""
    home = home or Path.home()
    with _install_lock(root):
        return _uninstall(agent, home, root)


def _uninstall(agent: str, home: Path, root: Path) -> list[str]:
    host = _host(agent)
    config = _open_config(host, home)
    skill_dir = home / host["user"]["skillDirectory"] / SKILL_NAME
    receipt = _read_receipt(skill_dir)
    files = receipt.get("files", {})

    current = config.get()
    ours = (server_entry(agent, root), receipt.get("mcp_entry"))
    if current is not None and current not in ours:
        raise InstallError(
            f"The `{SERVER_NAME}` MCP server in {config.path} was changed since Retra installed it:\n"
            f"  {json.dumps(current)}\n"
            f"Remove it by hand if you want it gone."
        )
    edited = [rel for rel in files if _edited(skill_dir, rel, files)]
    if edited:
        raise InstallError(
            f"These files in {skill_dir} were changed since Retra installed them, so nothing was removed:\n"
            + "".join(f"  {rel}\n" for rel in edited)
            + "Delete them by hand if you no longer need them, then run uninstall again."
        )

    removed = []
    if current is not None:
        _write_config(config, config.render(None))
        removed.append(f"MCP server `{SERVER_NAME}` from {config.path}")
    for rel in files:
        path = skill_dir / rel
        if path.exists():
            path.unlink()
            removed.append(f"skill file {path}")
    (skill_dir / RECEIPT_NAME).unlink(missing_ok=True)
    if skill_dir.exists():
        for d in sorted((p for p in skill_dir.rglob("*") if p.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        if not any(skill_dir.iterdir()):
            skill_dir.rmdir()
    return removed


def status(agent: str, home: Path | None = None, root: Path = PROJECT_ROOT) -> str:
    """One word for `install --list`: installed, not installed, outdated or conflict."""
    home = home or Path.home()
    try:
        plan = _plan_install(agent, home, root)
    except InstallError:
        return "conflict"
    receipt_path = plan.skill_dir / RECEIPT_NAME
    if plan.config_bytes is None and not plan.writes and not plan.deletes:
        return "installed" if receipt_path.exists() else "MCP only"
    if plan.config.get() is None and not receipt_path.exists():
        return "not installed"
    return "outdated"


def list_hosts(home: Path | None = None, root: Path = PROJECT_ROOT) -> str:
    home = home or Path.home()
    rows = []
    for agent, host in HOSTS.items():
        user = host["user"]
        rows.append(
            f"  {agent:<8} {status(agent, home, root):<14} "
            f"~/{user['mcpConfig']['relativePath']}, ~/{user['skillDirectory']}/{SKILL_NAME}/"
        )
    return "\n".join(rows)
