"""The nightly wiki compile reads the day's records from the database, runs
one LLM call per touched page, spends no call on an unchanged day, and
resumes a killed run page by page."""

import fcntl
import json
import re
import shutil
from datetime import date, datetime, timedelta

import pytest

from config.settings import ObsidianConfig, Settings
from export import compile_input, pipeline_status, wiki_compiler, wiki_history
from storage.database import Database
from storage.models import Screenshot, WindowEvent

DAY = date(2026, 11, 5)
T0 = datetime(2026, 11, 5, 10, 0)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _page_reply(prompt: str) -> str:
    """The page as it was, plus a line: keeps its aliases, like a real update."""
    page, body = re.search(r'<page path="(.+?)">\n(.*?)\n</page>', prompt, re.S).groups()
    return f"===FILE: {page}===\n{body}\nupdated\n===END_FILE===\n"


@pytest.fixture
def compiler(tmp_path, monkeypatch):
    from config import settings as settings_mod
    from mcp_server import store
    vault = tmp_path / "vault"
    settings = Settings(obsidian=ObsidianConfig(vault_path=str(vault)))
    wiki = vault / "retra-wiki"
    for d in ("projects", "people", "rollups"):
        (wiki / d).mkdir(parents=True)
    (wiki / "CLAUDE.md").write_text("schema\n")
    (wiki / "projects" / "retra.md").write_text("---\ntitle: Retra\naliases: [daylens]\n---\n# Retra\nRETRA BODY\n")
    (wiki / "projects" / "chatty-insights.md").write_text(
        "---\ntitle: Chatty\naliases: [chatty-cortex, chatty-backend]\n---\n# Chatty\nCHATTY BODY\n")
    (wiki / "rollups" / "2026-w44.md").write_text("# Week 44\nROLLUP BODY\n")

    db_path = tmp_path / "retra.db"
    db = Database(db_path)
    monkeypatch.setattr(settings_mod, "_settings", settings)
    monkeypatch.setenv("RETRA_DB_PATH", str(db_path))
    monkeypatch.setattr(store, "_db", None)
    monkeypatch.setattr(wiki_compiler, "MANIFEST_PATH", tmp_path / "wiki_manifest.json")
    monkeypatch.setattr(wiki_compiler, "RUN_PATH", tmp_path / "wiki_run.json")
    monkeypatch.setattr(wiki_history, "WIKI_GIT_DIR", tmp_path / "wiki.git")
    monkeypatch.setattr(wiki_history, "WIKI_LOCK_PATH", tmp_path / "wiki.lock")
    monkeypatch.setattr(pipeline_status, "PIPELINE_STATUS_PATH", tmp_path / "pipeline.json")

    class C:
        prompts: list[str] = []
        # A function of the prompt; the default answers each page job with
        # that page and the new-pages job with "nothing to add".
        reply = None

        def call(self, prompt):
            self.prompts.append(prompt)
            if self.reply:
                return self.reply(prompt)
            if '<page path="' in prompt:
                return _page_reply(prompt)
            return wiki_compiler.NO_NEW_PAGES

        def event(self, minute, title, app="Code", url=None, hint=None):
            db.insert_event(WindowEvent(timestamp=T0 + timedelta(minutes=minute), app_name=app,
                                        window_title=title, url=url, category="development", project_hint=hint))

        def session(self, sid, project, repo=None, title="Work", cwd=None, prompts=("do it",), **kw):
            db.save_agent_session({
                "session_id": sid, "source": "claude-code", "cwd": cwd or f"/work/{project}",
                "project": project, "repo": repo, "branch": "main", "title": title,
                "started_at": (T0 + timedelta(hours=1)).isoformat(), "ended_at": (T0 + timedelta(hours=2)).isoformat(),
                "prompt_count": len(prompts), "files": "[]", "prs": "[]", "last_status": kw.get("status", "Done"),
                "last_reply": None, "log_path": f"/{sid}.jsonl", "log_size": 1, "log_mtime": 1.0,
            }, [((T0 + timedelta(hours=1)).isoformat(), "prompt", p) for p in prompts])

        def url(self, minute, url, title, app="Brave Browser"):
            db.insert_url_event(T0 + timedelta(minutes=minute), url, url.split("/")[2], title, app, "browsing", 300)

        def screen(self, minute, app, title, text):
            sid = db.insert_screenshot(Screenshot(timestamp=T0 + timedelta(minutes=minute), filepath="",
                                                  app_name=app, window_title=title))
            db.set_screen_text(sid, text)

        def run(self, day=DAY, **kw):
            n = len(self.prompts)
            wiki_compiler.compile_daily_note(day, **kw)
            return self.prompts[n:]

        def manifest(self):
            return json.loads(wiki_compiler.MANIFEST_PATH.read_text())

    c = C()
    c.prompts = []
    c.wiki = wiki
    monkeypatch.setattr(wiki_compiler, "_call_claude", c.call)
    # A day with work on both projects.
    for m in range(0, 30, 5):
        c.event(m, "server.py - retra", hint="retra")
    for m in range(30, 60, 5):
        c.event(m, "api.py - chatty-cortex", hint="chatty-cortex")
    c.event(60, "idle", app="Finder")
    return c


def _pages(prompts):
    return sorted(re.search(r'<page path="(.+?)">', p)[1] if '<page path="' in p else "(new)" for p in prompts)


def _records(prompt):
    return re.search(r"<records[^>]*>\n(.*?)\n</records>", prompt, re.S)[1].splitlines()


# ── D2: jobs, manifest, no-op ────────────────────────────────────


def test_one_call_per_touched_page_with_only_that_page(compiler):
    prompts = compiler.run()
    assert _pages(prompts) == ["projects/chatty-insights.md", "projects/retra.md"]
    retra = next(p for p in prompts if 'path="projects/retra.md"' in p)
    assert "RETRA BODY" in retra
    assert "CHATTY BODY" not in retra and "ROLLUP BODY" not in retra
    assert "  - rollups/2026-w44.md" in retra, "other pages are listed by name"
    assert all("chatty" not in line for line in _records(retra)), "only its own records"


def test_unchanged_day_makes_no_calls(compiler):
    assert len(compiler.run()) == 2
    assert compiler.run() == []
    assert not wiki_compiler.RUN_PATH.exists()

    compiler.url(40, "https://github.com/chatty/chatty-cortex/pull/7", "chatty-cortex PR 7")
    assert _pages(compiler.run()) == ["projects/chatty-insights.md"], "retra's records didn't change"

    assert len(compiler.run(force=True)) == 2


def test_killed_run_resumes_only_the_remaining_pages(compiler):
    def killed_on_second_call(prompt):
        if len(compiler.prompts) == 2:
            raise KeyboardInterrupt
        return _page_reply(prompt)

    compiler.reply = killed_on_second_call
    with pytest.raises(KeyboardInterrupt):
        compiler.run()
    run = json.loads(wiki_compiler.RUN_PATH.read_text())
    assert [j["status"] for j in run["jobs"]] == ["done", "pending"]
    first, interrupted = run["jobs"][0]["page"], run["jobs"][1]["page"]

    compiler.reply = None
    assert _pages(compiler.run()) == [interrupted]
    assert not wiki_compiler.RUN_PATH.exists()
    assert compiler.manifest()["days"][DAY.isoformat()]["jobs"] == 2
    assert compiler.manifest()["pages"][first]["run_id"] == run["run_id"]
    assert compiler.run() == []


def test_failed_job_is_retried_alone(compiler):
    compiler.reply = lambda p: None if 'path="projects/retra.md"' in p else _page_reply(p)
    compiler.run()
    assert json.loads(compiler.wiki.parent.parent.joinpath("pipeline.json").read_text())["wiki"]["ok"] is False
    compiler.reply = None
    assert _pages(compiler.run()) == ["projects/retra.md"]
    assert compiler.run() == []


def test_malformed_reply_is_not_cached(compiler):
    compiler.reply = lambda p: _page_reply(p).replace("===END_FILE===", "cut off before the end marker")
    compiler.run()
    assert "RETRA BODY" in (compiler.wiki / "projects" / "retra.md").read_text()
    compiler.reply = None
    assert len(compiler.run()) == 2


def test_hand_edited_page_is_recompiled_on_a_changed_day(compiler):
    compiler.run()
    (compiler.wiki / "projects" / "retra.md").write_text("# Retra\nmy own notes\n")
    compiler.url(40, "https://github.com/chatty/chatty-cortex/pull/7", "chatty-cortex PR 7")
    assert _pages(compiler.run()) == ["projects/chatty-insights.md", "projects/retra.md"]


def test_prompt_size_per_page_is_logged_and_stays_flat(compiler, capsys):
    compiler.run()
    out = capsys.readouterr().out
    assert re.search(r"Wiki job projects/retra.md: [\d,]+ chars, \d+ records", out)
    day = compiler.manifest()["days"][DAY.isoformat()]
    assert day["calls"] == 2 and day["max_page_prompt_chars"] > 0

    small = max(map(len, compiler.prompts))
    for i in range(50):
        (compiler.wiki / "rollups" / f"old-{i}.md").write_text("# Old\n" + "filler " * 500)
    big = max(map(len, compiler.run(force=True)))
    assert big - small < 50 * 60, "only the names of unrelated pages are added"


def test_pages_a_job_was_not_given_are_not_rewritten(compiler):
    compiler.reply = lambda p: ("===FILE: rollups/2026-w44.md===\n# Week 44\nguessed\n===END_FILE===\n"
                                "===FILE: people/matteo.md===\n# Matteo\n===END_FILE===\n"
                                + _page_reply(p))
    compiler.run()
    assert "ROLLUP BODY" in (compiler.wiki / "rollups" / "2026-w44.md").read_text()
    assert (compiler.wiki / "people" / "matteo.md").exists(), "new pages are still written"


def test_unclaimed_project_gets_a_new_pages_job(compiler):
    compiler.session("s9", "acme-api", title="Fix the acme billing bug")
    compiler.reply = lambda p: (_page_reply(p) if '<page path="' in p else
                                "===FILE: projects/acme-api.md===\n# Acme API\n===END_FILE===\n"
                                "===FILE: projects/retra.md===\n# overwritten\n===END_FILE===\n")
    prompts = compiler.run()
    assert "(new)" in _pages(prompts)
    new = next(p for p in prompts if '<page path="' not in p)
    assert any(line.startswith("[retra://session/s9]") for line in _records(new))
    assert "RETRA BODY" not in new, "existing pages by name only"
    assert (compiler.wiki / "projects" / "acme-api.md").exists()
    assert "overwritten" not in (compiler.wiki / "projects" / "retra.md").read_text()


def test_one_log_entry_per_day(compiler):
    compiler.run()
    log = (compiler.wiki / "log.md").read_text()
    assert log.count(f"[{DAY.isoformat()} ") == 1
    assert "] ingest" in log


def test_manifest_is_saved_while_the_wiki_lock_is_held(compiler, monkeypatch):
    held = []
    write = wiki_compiler._write_json

    def checking_write(path, data):
        if path == wiki_compiler.MANIFEST_PATH:
            with wiki_history.WIKI_LOCK_PATH.open("w") as fh:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    held.append(False)
                except BlockingIOError:
                    held.append(True)
        write(path, data)

    monkeypatch.setattr(wiki_compiler, "_write_json", checking_write)
    compiler.run()
    assert held and all(held)


def test_failed_sunday_rollup_is_retried_without_redoing_the_ingest(compiler, monkeypatch):
    sunday = date(2026, 11, 8)
    for m in range(0, 30, 5):
        compiler.event(m + 60 * 24 * 3, "server.py - retra", hint="retra")
    results = [False, True]
    calls = []
    monkeypatch.setattr(wiki_compiler, "compile_week", lambda d: calls.append(d) or results.pop(0))

    first = compiler.run(day=sunday)  # ingest ok, rollup fails
    assert first
    assert compiler.run(day=sunday) == []  # unchanged: no ingest, rollup retried and ok
    assert compiler.run(day=sunday) == []  # nothing left to do
    assert calls == [sunday, sunday]


def test_short_or_partial_names_do_not_match():
    assert not wiki_compiler._mentions("worked on retrace", "retra")
    assert wiki_compiler._mentions("chatty cortex sync", "chatty-cortex")
    assert not wiki_compiler._mentions("ai stuff", "ai")


def test_aliases_in_every_frontmatter_form():
    names = wiki_compiler._page_names
    assert "chatty-cortex" in names("projects/chatty-insights.md", "---\naliases: chatty-cortex\n---\n")
    assert {"a", "b"} <= names("projects/x.md", "---\naliases: [a, b]\n---\n")
    assert {"a", "b"} <= names("projects/x.md", "---\naliases:\n  - a\n  - b\ntags: [t]\n---\n")
    assert "t" not in names("projects/x.md", "---\naliases:\n  - a\ntags: [t]\n---\n")


# ── D0: records, references, privacy ─────────────────────────────

_REF_RE = re.compile(
    r"^\[retra://(session/[\w-]+|span/\d{4}-\d\d-\d\dT\d\d:\d\d-\d\d:\d\d\?app=[^\]\s]+|url/\d+|screen/\d+"
    r"|day/\d{4}-\d\d-\d\d)\] ")


def test_coding_session_is_credited_to_its_one_project_page(compiler):
    # Folder "daylens", repo "Retra": the page with both names wins.
    (compiler.wiki / "projects" / "daylens.md").write_text("# Daylens\nOLD DUPLICATE\n")
    compiler.session("s1", "daylens", repo="Retra", title="Open the wiki jobs PR", cwd="/Users/me/daylens")
    prompts = compiler.run()
    holders = [p for p in prompts if "retra://session/s1" in p]
    assert len(holders) == 1
    assert 'path="projects/retra.md"' in holders[0]
    assert "Open the wiki jobs PR" in holders[0]


def test_without_an_alias_the_repo_name_wins(compiler):
    (compiler.wiki / "projects" / "retra.md").write_text("# Retra\nRETRA BODY\n")
    (compiler.wiki / "projects" / "daylens.md").write_text("# Daylens\nOLD DUPLICATE\n")
    compiler.session("s1", "daylens", repo="Retra", cwd="/Users/me/daylens")
    holders = [p for p in compiler.run() if "retra://session/s1" in p]
    assert len(holders) == 1 and 'path="projects/retra.md"' in holders[0]


def test_every_prompt_item_carries_a_reference(compiler):
    compiler.session("s1", "retra", title="Add jobs")
    compiler.url(10, "https://github.com/AdityaPainuli/Retra/pull/15", "Retra PR 15")
    compiler.screen(12, "Code", "server.py - retra", "def compile_daily_note retra")
    compiler.screen(40, "Code", "api.py - chatty-cortex", "chatty-cortex handler")
    prompts = compiler.run()
    lines = [line for p in prompts for line in _records(p)]
    assert lines and all(_REF_RE.match(line) for line in lines), [l for l in lines if not _REF_RE.match(l)]
    kinds = {re.match(r"\[retra://(\w+)/", line)[1] for line in lines}
    assert kinds == {"session", "span", "url", "screen"}


def test_records_reference_formats(compiler):
    compiler.event(70, "notes.md - retra", app="Brave Browser", hint="retra")
    for m in range(71, 80):
        compiler.event(m, "notes.md - retra", app="Brave Browser", hint="retra")
    records = compile_input.day_records(DAY)
    refs = [r.ref for r in records]
    assert refs[0] == f"retra://day/{DAY.isoformat()}"
    assert f"retra://span/{DAY.isoformat()}T10:00-10:30?app=Code" in refs
    assert any(r.startswith(f"retra://span/{DAY.isoformat()}T11:10-") and r.endswith("?app=Brave%20Browser")
               for r in refs)
    assert "Projects: " in records[0].text and "retra" in records[0].text


def test_private_content_never_reaches_the_prompt(compiler):
    compiler.event(65, "SECRET-VAULT entry - retra", app="1Password", hint="retra")
    compiler.event(70, "SECRET-STATEMENT retra", app="Brave Browser", url="https://chase.com/x", hint="retra")
    compiler.event(75, "server.py - retra", hint="retra")
    compiler.url(66, "https://www.chase.com/statement", "SECRET-URL retra")
    compiler.session("p1", "bank-tools", title="SECRET-SESSION retra", cwd="/work/bank-tools")
    compiler.session("s1", "retra", title="Ok session", prompts=("SECRET-PROMPT open chase.com", "fine prompt"),
                     status="Checked my bank\nall good")
    compiler.screen(20, "1Password", "Vault - retra", "SECRET-SCREEN-APP retra")
    compiler.screen(21, "Code", "server.py - retra", "retra code\nlogin to chase.com SECRET-SCREEN-LINE")

    lines = "\n".join(r.line() for r in compile_input.day_records(DAY))
    prompts = "\n".join(compiler.run())
    for text in (lines, prompts):
        assert "SECRET" not in text
        assert "1Password" not in text and "chase.com" not in text and "bank" not in text.lower()
    assert "fine prompt" in prompts and "retra code" in prompts
