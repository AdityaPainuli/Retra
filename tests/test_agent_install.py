import json
import tomllib
from pathlib import Path

import pytest

from mcp_server.install import HOSTS, RECEIPT_NAME, InstallError, install, list_hosts, server_entry, uninstall

AGENTS = list(HOSTS)

OTHER_SERVER = {"command": "/usr/bin/other", "args": ["serve"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # Anything that falls back to the real home dir lands here instead.
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def root(tmp_path):
    """A fake Retra checkout: a venv python, main.py and a two-file skill."""
    root = tmp_path / "retra"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text("")
    (root / "main.py").write_text("")
    skill = root / "skills" / "retra"
    (skill / "reference").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: retra\n---\n# Using Retra\n")
    (skill / "reference" / "tools.md").write_text("tools\n")
    return root


def config_path(home: Path, agent: str) -> Path:
    return home / HOSTS[agent]["user"]["mcpConfig"]["relativePath"]


def skill_dir(home: Path, agent: str) -> Path:
    return home / HOSTS[agent]["user"]["skillDirectory"] / "retra"


def read_servers(home: Path, agent: str) -> dict:
    path = config_path(home, agent)
    if HOSTS[agent]["user"]["mcpConfig"]["kind"] == "toml":
        return tomllib.loads(path.read_text()).get("mcp_servers", {})
    return json.loads(path.read_text()).get("mcpServers", {})


def seed_config(home: Path, agent: str, retra: dict | None = None):
    """A config the user already has: another server, and maybe a retra entry."""
    path = config_path(home, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    if HOSTS[agent]["user"]["mcpConfig"]["kind"] == "toml":
        text = '# my codex config\nmodel = "gpt-5"\n\n[mcp_servers.other]\ncommand = "/usr/bin/other"\nargs = ["serve"]\n'
        if retra:
            text += "\n[mcp_servers.retra]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in retra.items())
        path.write_text(text)
    else:
        servers = {"other": OTHER_SERVER}
        if retra:
            servers["retra"] = retra
        path.write_text(json.dumps({"theme": "dark", "mcpServers": servers}, indent=2))


def snapshot(home: Path) -> dict:
    return {p: p.read_bytes() for p in sorted(home.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("agent", AGENTS)
def test_install_adds_entry_and_skill(home, root, agent):
    seed_config(home, agent)

    changed = install(agent, home, root)

    assert changed
    servers = read_servers(home, agent)
    assert servers["retra"] == server_entry(agent, root)
    assert servers["retra"]["command"] == str(root / "venv" / "bin" / "python")
    assert servers["retra"]["args"] == [str(root / "main.py"), "mcp"]
    assert servers["other"] == OTHER_SERVER
    skill = skill_dir(home, agent)
    assert (skill / "SKILL.md").read_text().startswith("---\nname: retra")
    assert (skill / "reference" / "tools.md").read_text() == "tools\n"
    receipt = json.loads((skill / RECEIPT_NAME).read_text())
    assert set(receipt["files"]) == {"SKILL.md", "reference/tools.md"}
    if agent == "codex":
        assert config_path(home, agent).read_text().startswith('# my codex config\nmodel = "gpt-5"')
    else:
        assert json.loads(config_path(home, agent).read_text())["theme"] == "dark"


@pytest.mark.parametrize("agent", AGENTS)
def test_install_without_existing_config(home, root, agent):
    install(agent, home, root)
    assert read_servers(home, agent) == {"retra": server_entry(agent, root)}


def test_install_into_empty_cursor_config(home, root):
    config_path(home, "cursor").parent.mkdir(parents=True)
    config_path(home, "cursor").write_text("")
    install("cursor", home, root)
    assert read_servers(home, "cursor") == {"retra": server_entry("cursor", root)}


@pytest.mark.parametrize("agent", AGENTS)
def test_reinstall_changes_nothing(home, root, agent):
    seed_config(home, agent)
    install(agent, home, root)
    before = snapshot(home)

    assert install(agent, home, root) == []
    assert snapshot(home) == before


def test_manual_readme_setup_counts_as_installed(home, root):
    """An entry added by hand with `claude mcp add` is an exact match."""
    seed_config(home, "claude", retra=server_entry("claude", root))
    changed = install("claude", home, root)
    assert not any("MCP server" in line for line in changed)


@pytest.mark.parametrize("agent", AGENTS)
def test_reinstall_upgrades_what_we_installed(home, root, agent, tmp_path):
    seed_config(home, agent)
    install(agent, home, root)

    (root / "skills" / "retra" / "SKILL.md").write_text("---\nname: retra\n---\n# v2\n")
    (root / "skills" / "retra" / "reference" / "tools.md").unlink()
    moved = tmp_path / "retra-moved"
    root.rename(moved)

    assert install(agent, home, moved)
    assert read_servers(home, agent)["retra"] == server_entry(agent, moved)
    assert read_servers(home, agent)["other"] == OTHER_SERVER
    assert (skill_dir(home, agent) / "SKILL.md").read_text().endswith("# v2\n")
    assert not (skill_dir(home, agent) / "reference" / "tools.md").exists()


@pytest.mark.parametrize("agent", AGENTS)
def test_hand_edited_config_is_refused(home, root, agent):
    seed_config(home, agent)
    install(agent, home, root)
    edited = {**server_entry(agent, root), "args": ["/somewhere/else/main.py", "mcp"]}
    seed_config(home, agent, retra=edited)
    before = snapshot(home)

    with pytest.raises(InstallError, match="didn't write"):
        install(agent, home, root)
    with pytest.raises(InstallError, match="changed since Retra installed it"):
        uninstall(agent, home, root)
    assert snapshot(home) == before


@pytest.mark.parametrize("agent", AGENTS)
def test_foreign_entry_is_refused_on_first_install(home, root, agent):
    seed_config(home, agent, retra={"command": "/opt/retra-fork", "args": []})
    before = snapshot(home)
    with pytest.raises(InstallError):
        install(agent, home, root)
    assert snapshot(home) == before


@pytest.mark.parametrize("agent", AGENTS)
def test_hand_edited_skill_file_is_refused(home, root, agent):
    seed_config(home, agent)
    install(agent, home, root)
    skill = skill_dir(home, agent) / "SKILL.md"
    skill.write_text("my own notes\n")
    (root / "skills" / "retra" / "reference" / "tools.md").write_text("tools v2\n")
    before = snapshot(home)

    with pytest.raises(InstallError, match="SKILL.md"):
        install(agent, home, root)
    with pytest.raises(InstallError, match="SKILL.md"):
        uninstall(agent, home, root)
    # Refused before any write: the other file wasn't upgraded either.
    assert snapshot(home) == before


@pytest.mark.parametrize("agent", AGENTS)
def test_someone_elses_retra_skill_is_refused(home, root, agent):
    skill = skill_dir(home, agent)
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("not ours\n")
    with pytest.raises(InstallError, match="SKILL.md"):
        install(agent, home, root)
    assert (skill / "SKILL.md").read_text() == "not ours\n"


@pytest.mark.parametrize("agent", AGENTS)
def test_uninstall_removes_only_ours(home, root, agent):
    seed_config(home, agent)
    install(agent, home, root)
    user_file = skill_dir(home, agent) / "my-notes.md"
    user_file.write_text("keep me\n")

    assert uninstall(agent, home, root)
    servers = read_servers(home, agent)
    assert "retra" not in servers
    assert servers["other"] == OTHER_SERVER
    assert sorted(p.name for p in skill_dir(home, agent).rglob("*")) == ["my-notes.md"]
    if agent == "codex":
        assert config_path(home, agent).read_text().startswith('# my codex config\nmodel = "gpt-5"')

    user_file.unlink()
    install(agent, home, root)
    uninstall(agent, home, root)
    assert not skill_dir(home, agent).exists()
    assert uninstall(agent, home, root) == []


def test_codex_inline_entry_is_refused(home, root):
    path = config_path(home, "codex")
    path.parent.mkdir(parents=True)
    path.write_text('mcp_servers = { retra = { command = "x", args = [] } }\n')
    with pytest.raises(InstallError):
        install("codex", home, root)


def test_unknown_agent(home, root):
    with pytest.raises(InstallError, match="Available: claude, codex, cursor"):
        install("vim", home, root)


def test_list_shows_each_agent(home, root):
    install("cursor", home, root)
    seed_config(home, "codex", retra={"command": "/opt/retra-fork", "args": []})
    lines = {line.split()[0]: line for line in list_hosts(home, root).splitlines()}
    assert "not installed" in lines["claude"]
    assert "conflict" in lines["codex"]
    assert "installed" in lines["cursor"] and "not" not in lines["cursor"]


def test_codex_replaces_table_in_the_middle(home, root, tmp_path):
    install("codex", home, root)
    path = config_path(home, "codex")
    ours = path.read_text()
    path.write_text(
        '# top\n[mcp_servers.first]\ncommand = "a"\n\n'
        + ours
        + '\n[profiles.work]\nmodel = "o3"  # keep\n'
    )
    moved = tmp_path / "retra-moved"
    root.rename(moved)

    install("codex", home, moved)
    text = path.read_text()
    assert text.startswith('# top\n[mcp_servers.first]\ncommand = "a"\n')
    assert 'model = "o3"  # keep' in text
    doc = tomllib.loads(text)
    assert doc["mcp_servers"]["retra"] == server_entry("codex", moved)
    assert doc["mcp_servers"]["first"] == {"command": "a"}
    assert doc["profiles"] == {"work": {"model": "o3"}}


# ── Review fixes ─────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../../../notes.txt", "/etc/hosts", "", "reference/../../escape.md"])
def test_receipt_paths_outside_the_skill_are_refused(home, root, bad):
    from mcp_server import install as inst
    notes = home / "notes.txt"
    notes.write_text("mine\n")
    install("cursor", home, root)
    receipt_path = skill_dir(home, "cursor") / RECEIPT_NAME
    receipt = json.loads(receipt_path.read_text())
    receipt["files"][bad] = inst._sha256(notes.read_bytes())
    receipt_path.write_text(json.dumps(receipt))
    for run in (uninstall, install):
        with pytest.raises(InstallError, match="edited by hand"):
            run("cursor", home, root)
    assert notes.read_text() == "mine\n"


def test_codex_install_from_a_path_with_an_emoji(home, root, tmp_path):
    import shutil
    fancy = tmp_path / "retra \U0001F680"
    shutil.copytree(root, fancy)
    install("codex", home, fancy)
    assert read_servers(home, "codex")["retra"] == server_entry("codex", fancy)


def test_codex_config_with_a_date_value(home, root):
    seed_config(home, "codex")
    path = config_path(home, "codex")
    path.write_text("last_used = 2026-01-01\n" + path.read_text())
    install("codex", home, root)
    assert "installed" in list_hosts(home, root)
    uninstall("codex", home, root)
    assert tomllib.loads(path.read_text())["last_used"].isoformat() == "2026-01-01"


def test_config_changed_after_it_was_read_is_not_overwritten(home, root, monkeypatch):
    from mcp_server import install as inst
    seed_config(home, "cursor")
    path = config_path(home, "cursor")
    real_open = inst._open_config

    def open_then_someone_saves(host, h):
        config = real_open(host, h)
        path.write_text(json.dumps({"mcpServers": {"other": OTHER_SERVER, "new": OTHER_SERVER}}))
        return config

    monkeypatch.setattr(inst, "_open_config", open_then_someone_saves)
    with pytest.raises(InstallError, match="changed while Retra was editing it"):
        install("cursor", home, root)
    assert set(json.loads(path.read_text())["mcpServers"]) == {"other", "new"}
    assert not skill_dir(home, "cursor").exists()


def test_install_holds_the_lock(home, root, monkeypatch):
    import fcntl
    from mcp_server import install as inst
    real_plan = inst._plan_install
    seen = []

    def plan(*args):
        with open(root / "data" / inst.LOCK_NAME, "w") as f:
            with pytest.raises(BlockingIOError):
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seen.append(True)
        return real_plan(*args)

    monkeypatch.setattr(inst, "_plan_install", plan)
    install("cursor", home, root)
    assert seen
