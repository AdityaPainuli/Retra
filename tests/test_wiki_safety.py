"""Wiki writes stay inside the wiki, and every compile is kept in history."""

import fcntl
import os
import shutil

from datetime import date

import pytest

from export import wiki_history
from export.wiki_compiler import _apply_wiki_updates, _write_and_record


def _response(files: dict[str, str]) -> str:
    return "".join(f"===FILE: {path}===\n{body}\n===END_FILE===\n" for path, body in files.items())


@pytest.fixture
def wiki(tmp_path):
    root = tmp_path / "wiki"
    (root / "projects").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("schema\n")
    return root


@pytest.mark.parametrize("path", [
    "../../x.md",
    "/etc/x.md",
    "/projects/x.md",
    "projects/../../x.md",
    "projects/../CLAUDE.md",
    "projects\\..\\..\\x.md",
    "projects\\Free.md",
    "CLAUDE.md",
    "notes/x.md",
    "projects/x.txt",
    "projects/sub/x.md",
    "_quarantine/x.md",
])
def test_rejects_paths_outside_categories(wiki, tmp_path, path):
    before = sorted(p for p in tmp_path.rglob("*"))
    assert _apply_wiki_updates(wiki, _response({path: "pwned"})) == []
    after = sorted(p for p in tmp_path.rglob("*") if p.name != "index.md")
    assert after == [p for p in before if p.name != "index.md"]
    assert (wiki / "CLAUDE.md").read_text() == "schema\n"


def test_rejects_symlink_out_of_wiki(wiki, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("keep\n")
    os.symlink(outside, wiki / "projects" / "evil.md")
    assert _apply_wiki_updates(wiki, _response({"projects/evil.md": "pwned"})) == []
    assert outside.read_text() == "keep\n"


def test_writes_category_pages(wiki):
    written = _apply_wiki_updates(wiki, _response({
        "projects/retra.md": "a", "people/john-jeong.md": "b",
    }))
    assert written == ["projects/retra.md", "people/john-jeong.md"]
    assert (wiki / "projects" / "retra.md").read_text() == "a\n"
    assert (wiki / "index.md").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_history_keeps_every_compile(wiki, tmp_path):
    git_dir = tmp_path / "wiki.git"
    page = wiki / "projects" / "retra.md"

    page.write_text("launch friday\n")
    first = wiki_history.commit(wiki, "ingest 2026-11-01: projects: retra", git_dir=git_dir)
    page.write_text("launch monday\n")
    second = wiki_history.commit(wiki, "ingest 2026-11-02: projects: retra", git_dir=git_dir)

    assert first and second and first != second
    assert wiki_history.commit(wiki, "no-op", git_dir=git_dir) is None  # nothing changed

    log = wiki_history._git(wiki, "log", "--format=%s", git_dir=git_dir).stdout.splitlines()
    assert log[:2] == ["ingest 2026-11-02: projects: retra", "ingest 2026-11-01: projects: retra"]

    # Any page can be restored from an older compile.
    wiki_history._git(wiki, "checkout", first, "--", "projects/retra.md", git_dir=git_dir)
    assert page.read_text() == "launch friday\n"
    assert not (wiki / ".git").exists()  # history lives outside the vault
    assert "-launch friday" in wiki_history.diff(wiki, git_dir=git_dir)


@pytest.fixture
def history(tmp_path, monkeypatch):
    git_dir = tmp_path / "wiki.git"
    monkeypatch.setattr(wiki_history, "WIKI_GIT_DIR", git_dir)
    monkeypatch.setattr(wiki_history, "WIKI_LOCK_PATH", tmp_path / "wiki.lock")
    return git_dir


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_compile_keeps_state_from_before_it(wiki, history):
    page = wiki / "projects" / "retra.md"
    page.write_text("fact the LLM will drop\n")  # existing page, never committed

    _write_and_record(wiki, _response({"projects/retra.md": "rewritten"}), "ingest", date(2026, 11, 1))
    page.write_text("my edit in obsidian\n")
    _write_and_record(wiki, _response({"projects/retra.md": "rewritten again"}), "ingest", date(2026, 11, 2))

    log = wiki_history._git(wiki, "log", "--format=%s", git_dir=history).stdout.splitlines()
    assert [m.split(":")[0] for m in log] == [
        "ingest 2026-11-02", "before ingest 2026-11-02", "ingest 2026-11-01", "before ingest 2026-11-01",
    ]
    for rev, text in (("HEAD~3", "fact the LLM will drop"), ("HEAD~1", "my edit in obsidian")):
        shown = wiki_history._git(wiki, "show", f"{rev}:projects/retra.md", git_dir=history).stdout
        assert shown.strip() == text


def test_lock_is_exclusive_across_processes(history):
    with wiki_history.lock():
        with wiki_history.WIKI_LOCK_PATH.open("w") as other:  # separate open file = separate lock
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with wiki_history.WIKI_LOCK_PATH.open("w") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released after the block
