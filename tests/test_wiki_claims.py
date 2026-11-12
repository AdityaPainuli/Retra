"""Wiki claims cite the records they came from, are re-checked against them
before every compile, and keep their history: a change is a new claim that
supersedes the old one, so `facts(subject, as_of)` can say what held on any
day (roadmap D1 / B1)."""

import asyncio
import json
import shutil
import sqlite3
from datetime import date, datetime, timedelta

import pytest
from mcp import Client

from config.settings import ObsidianConfig, Settings
from export import claims
from export.claims import ClaimsRejected, apply_decisions, check, facts, page_status, preflight, resolve
from storage.database import Database
from storage.models import Screenshot, WindowEvent

FRI, MON = date(2026, 11, 6), date(2026, 11, 9)
PAGE = "projects/retra.md"


def _session(db, sid, day, title, project="retra", status="done"):
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
    db.save_agent_session({
        "session_id": sid, "source": "claude-code", "cwd": f"/work/{project}", "project": project,
        "branch": "main", "title": title, "started_at": start.isoformat(),
        "ended_at": (start + timedelta(hours=1)).isoformat(), "prompt_count": 1, "files": "[]",
        "prs": "[]", "last_status": status, "last_reply": None, "log_path": f"/{sid}.jsonl",
        "log_size": 1, "log_mtime": 1.0,
    }, [(start.isoformat(), "prompt", title)])


@pytest.fixture
def db(tmp_path, monkeypatch):
    from config import settings as settings_mod
    # Default privacy rules (blocklist includes "bank"), never the local config.
    monkeypatch.setattr(settings_mod, "_settings", Settings(obsidian=ObsidianConfig(vault_path=str(tmp_path / "v"))))
    db = Database(tmp_path / "retra.db")
    _session(db, "s-fri", FRI, "Plan the Retra launch for Friday")
    _session(db, "s-mon", MON, "Move the Retra launch to Monday")
    return db


@pytest.fixture
def conn(db):
    with db._conn() as c:
        yield c


def claim(statement, *refs, subject="retra/launch-date", **kw):
    return {"subject": subject, "statement": statement, "evidence": list(refs), **kw}


def add(conn, day, *new, page=PAGE, **decisions):
    return apply_decisions(conn, page, {"claims": list(new), **decisions}, day=day, run_id=f"run-{day}")


# ── B1: facts with history ────────────────────────────────────────

def test_launch_moved_fri_to_mon(conn):
    first = add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))["added"][0]

    # Monday's records mention retra, so Friday's claim needs a recheck.
    issues = preflight(conn, MON)
    assert [(i.claim_id, i.kind) for i in issues[PAGE]] == [(first, "recheck")]
    with pytest.raises(ClaimsRejected, match="evidence debt"):
        add(conn, MON)  # silence about a flagged claim is rejected

    result = add(conn, MON, claim("Retra launch moved to Monday", "retra://session/s-mon", id=first))
    assert result["superseded"] == [first]

    on_fri = facts(conn, "retra/launch-date", as_of=FRI)
    assert [f["statement"] for f in on_fri] == ["Retra launches on Friday"]
    assert on_fri[0]["changed_to"]["statement"] == "Retra launch moved to Monday"
    assert on_fri[0]["evidence"][0]["resource"] == "retra://session/s-fri"

    today = facts(conn, "retra/launch-date")
    assert [f["statement"] for f in today] == ["Retra launch moved to Monday"]
    assert today[0]["valid_from"] == MON.isoformat()
    assert today[0]["evidence"][0]["resource"] == "retra://session/s-mon"
    assert today[0]["replaced"] == [{"id": first, "statement": "Retra launches on Friday",
                                     "valid_from": FRI.isoformat(), "until": MON.isoformat()}]
    # A project key also finds its topics; nothing held before Friday.
    assert [f["id"] for f in facts(conn, "retra")] == [today[0]["id"]]
    assert facts(conn, "retra", as_of=FRI - timedelta(days=1)) == []

    # The old row was never edited: same statement, same evidence, now superseded.
    old = conn.execute("SELECT * FROM wiki_claims WHERE id = ?", (first,)).fetchone()
    assert old["statement"] == "Retra launches on Friday" and old["superseded_by"] == today[0]["id"]


def test_recitation_keeps_the_day_it_became_true(conn):
    first = add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))["added"][0]
    add(conn, MON, claim("Retra launches on Friday", "retra://session/s-fri", "retra://session/s-mon", id=first))
    [f] = facts(conn, "retra/launch-date")
    assert f["valid_from"] == FRI.isoformat() and f["confidence"] == "confirmed"


def test_confirming_clears_the_recheck(conn):
    first = add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))["added"][0]
    add(conn, MON, confirmedClaimIds=[first])
    assert preflight(conn, MON) == {}
    assert page_status(conn, PAGE, MON)["verified"]


# ── Unresolved and private evidence ───────────────────────────────

def test_deleted_session_blocks_verified_until_retracted(conn):
    gone = add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))["added"][0]
    assert page_status(conn, PAGE, FRI)["verified"]

    conn.execute("DELETE FROM agent_messages WHERE session_id = 's-fri'")
    conn.execute("DELETE FROM agent_sessions WHERE session_id = 's-fri'")
    assert resolve(conn, "retra://session/s-fri") is None
    st = page_status(conn, PAGE, FRI)
    assert not st["verified"]
    assert [(i.claim_id, i.kind) for i in st["issues"]] == [(gone, "unresolved")]
    assert preflight(conn, FRI)[PAGE][0].kind == "unresolved"

    with pytest.raises(ClaimsRejected, match="no longer exists"):
        add(conn, FRI, confirmedClaimIds=[gone])
    with pytest.raises(ClaimsRejected, match="evidence debt"):
        add(conn, FRI, claim("Something else", "retra://session/s-mon", subject="retra"))
    assert not page_status(conn, PAGE, FRI)["verified"]

    add(conn, FRI, claim("Retra launch moved to Monday", "retra://session/s-mon"), retractedClaimIds=[gone])
    assert page_status(conn, PAGE, FRI)["verified"]
    # A retracted claim was never true, so it isn't in the history either.
    assert [f["statement"] for f in facts(conn, "retra/launch-date", as_of=FRI)] == ["Retra launch moved to Monday"]


def test_private_evidence_is_retracted_never_reworded(db, conn):
    _session(db, "s-bank", FRI, "Retra billing with the bank")  # "bank" is on the default blocklist
    # Cited before the blocklist covered it (written directly, as an older compile would have).
    cid = conn.execute("INSERT INTO wiki_claims (page, subject, statement, confidence, valid_from) "
                       "VALUES (?, 'retra/billing', 'Billing goes live', 'source-backed', ?)",
                       (PAGE, FRI.isoformat())).lastrowid
    conn.execute("INSERT INTO wiki_claim_evidence VALUES (?, 'retra://session/s-bank', 'v0')", (cid,))
    assert check(conn, "retra://session/s-bank").status == "private"

    with pytest.raises(ClaimsRejected, match="must be retracted"):
        add(conn, FRI, claim("Billing goes live soon", "retra://session/s-fri", id=cid))
    with pytest.raises(ClaimsRejected, match="private"):
        add(conn, FRI, claim("New claim", "retra://session/s-bank"))

    issues = preflight(conn, FRI)
    assert issues == {PAGE: []}  # the page needs a job, but the claim is never shown again
    row = conn.execute("SELECT retracted_at, retracted_reason FROM wiki_claims WHERE id = ?", (cid,)).fetchone()
    assert row["retracted_at"] and row["retracted_reason"] == "private"
    assert claims.live_claims(conn, PAGE) == []


# ── Validation ────────────────────────────────────────────────────

def _count(conn):
    return conn.execute("SELECT COUNT(*) FROM wiki_claims").fetchone()[0]


def test_batches_are_all_or_nothing(conn):
    with pytest.raises(ClaimsRejected) as e:
        add(conn, FRI, claim("Good", "retra://session/s-fri"), claim("Bad", "retra://session/nope"))
    assert e.value.errors == ["claims[1]: retra://session/nope is gone"]
    assert _count(conn) == 0


@pytest.mark.parametrize("decisions, error", [
    ({"claims": [claim("x", "retra://session/s-fri", confidence="confirmed")]}, "can't be set"),
    ({"claims": [claim("x", "retra://session/s-fri", confidence="contested")]}, "both sides"),
    ({"claims": [claim("", "retra://session/s-fri")]}, "no statement"),
    ({"claims": [claim("x")]}, "evidence must be"),
    ({"claims": [claim("x", "retra://nowhere/1")]}, "is invalid"),
    ({"claims": [claim("x", "retra://session/s-fri", subject="")]}, "no subject"),
    ({"confirmedClaimIds": [99]}, "not a live claim"),
    ({"confirmedClaimIds": "1"}, "list of claim ids"),
    ({}, "at least one claim"),
])
def test_rejected_decisions(conn, decisions, error):
    with pytest.raises(ClaimsRejected, match=error):
        apply_decisions(conn, PAGE, decisions, day=FRI, run_id="r")
    assert _count(conn) == 0


def test_one_decision_per_claim_and_page_keeps_a_claim(conn):
    cid = add(conn, FRI, claim("A", "retra://session/s-fri"))["added"][0]
    with pytest.raises(ClaimsRejected, match="more than one decision"):
        add(conn, FRI, confirmedClaimIds=[cid], retractedClaimIds=[cid])
    with pytest.raises(ClaimsRejected, match="at least one claim"):
        add(conn, FRI, retractedClaimIds=[cid])
    with pytest.raises(ClaimsRejected, match="not a live claim on projects/other.md"):
        add(conn, FRI, page="projects/other.md", confirmedClaimIds=[cid])


def test_contested_cites_both_sides(conn):
    r = add(conn, MON, claim("Launch is Fri or Mon", "retra://session/s-fri", "retra://session/s-mon",
                             confidence="contested"))
    assert facts(conn, "retra/launch-date")[0]["confidence"] == "contested"
    assert r["added"]


# ── Resolver and labels ───────────────────────────────────────────

def test_session_version_changes_when_it_grows(db, conn):
    cid = add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))["added"][0]
    before = resolve(conn, "retra://session/s-fri")
    _session(db, "s-fri", FRI, "Plan the Retra launch for Friday", status="launch slipped")
    after = resolve(conn, "retra://session/s-fri")
    assert before[0] != after[0] and "launch slipped" in after[1]
    assert [(i.claim_id, i.kind) for i in preflight(conn, FRI)[PAGE]] == [(cid, "stale")]
    add(conn, FRI, confirmedClaimIds=[cid])  # confirming re-stamps the version
    assert page_status(conn, PAGE, FRI)["verified"]


def test_resolver_kinds(db, conn):
    t = datetime.combine(FRI, datetime.min.time()) + timedelta(hours=9)
    for i in range(3):
        db.insert_event(WindowEvent(timestamp=t + timedelta(minutes=i * 5), app_name="Code",
                                    window_title="claims.py - retra", category="development"))
    shot = db.insert_screenshot(Screenshot(timestamp=t, filepath="", app_name="Code", window_title="x"))
    db.set_screen_text(shot, "launch checklist")
    db.insert_url_event(t, "https://github.com/x/retra/pull/9", "github.com", "Retra PR 9", "Chrome", "browsing")
    db._get_persistent_conn().commit()
    url_id = conn.execute("SELECT id FROM url_events").fetchone()[0]

    span = check(conn, f"retra://span/{FRI}T09:00-09:30?app=Code")
    assert span.status == "ok" and span.snippet == "claims.py - retra" and span.day == FRI.isoformat()
    assert check(conn, f"retra://span/{FRI}T09:00-09:30?app=Slack").status == "gone"
    assert check(conn, f"retra://span/{FRI}T10:00-09:00").status == "invalid"
    assert check(conn, f"retra://screen/{shot}").snippet == "launch checklist"
    assert check(conn, f"retra://url/{url_id}").snippet == "Retra PR 9"
    assert check(conn, "retra://day/2026-11-06").status == "gone"
    assert check(conn, "retra://url/abc").status == "invalid"
    assert check(conn, "https://example.com").status == "invalid"

    # D5 labels come from the evidence.
    assert add(conn, FRI, claim("Seen once on screen", f"retra://screen/{shot}", subject="a"))
    assert add(conn, FRI, claim("Window title and session", f"retra://span/{FRI}T09:00-09:30?app=Code",
                                "retra://session/s-fri", subject="b"))
    assert add(conn, FRI, claim("Two days", "retra://session/s-fri", "retra://session/s-mon", subject="c"))
    assert add(conn, FRI, claim("One session", "retra://session/s-fri", subject="d"))
    labels = dict(conn.execute("SELECT subject, confidence FROM wiki_claims"))
    assert labels == {"a": "watchlist", "b": "confirmed", "c": "confirmed", "d": "source-backed"}


def test_readers_tolerate_missing_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert facts(conn, "retra") == []
    assert check(conn, "retra://session/s1").status == "gone"
    assert check(conn, "retra://span/2026-11-06T09:00-10:00").status == "gone"


def test_stamp_frontmatter():
    text = "---\ntitle: Retra\nsources:\n  - retra://session/old\nverified: 2026-01-01\ntags: [a]\n---\n# Retra\n"
    out = claims.stamp_frontmatter(text, ["retra://session/s1"], None)
    assert out == "---\ntitle: Retra\ntags: [a]\nsources:\n  - retra://session/s1\n---\n# Retra\n"
    assert claims.stamp_frontmatter("# Bare\n", [], "2026-11-09T08:00:00") == \
        "---\nverified: 2026-11-09T08:00:00\n---\n# Bare\n"


# ── Compile hook and MCP tool ─────────────────────────────────────

pytestmark_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytestmark_git
def test_compile_hook_stamps_pages(tmp_path, monkeypatch, db):
    from config import settings as settings_mod
    from export import compile_input, pipeline_status, wiki_compiler, wiki_history
    settings = settings_mod._settings
    wiki = settings.obsidian.resolved_vault_path / "retra-wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "CLAUDE.md").write_text("schema\n")
    (wiki / "projects" / "retra.md").write_text("---\ntitle: Retra\n---\n# Retra\n")
    monkeypatch.setenv("RETRA_DB_PATH", str(db.db_path))
    monkeypatch.setattr(wiki_compiler, "MANIFEST_PATH", tmp_path / "wiki_manifest.json")
    monkeypatch.setattr(wiki_compiler, "RUN_PATH", tmp_path / "wiki_run.json")
    monkeypatch.setattr(wiki_history, "WIKI_GIT_DIR", tmp_path / "wiki.git")
    monkeypatch.setattr(wiki_history, "WIKI_LOCK_PATH", tmp_path / "wiki.lock")
    monkeypatch.setattr(pipeline_status, "PIPELINE_STATUS_PATH", tmp_path / "pipeline.json")
    prompts, replies = [], []
    monkeypatch.setattr(wiki_compiler, "_call_claude", lambda p: prompts.append(p) or replies.pop(0))

    def compile_day(day, ref, decisions):
        monkeypatch.setattr(compile_input, "day_records", lambda d: [
            compile_input.Record(f"retra://day/{d}", "day", "stats"),
            compile_input.Record(ref, "session", "retra: work", ["retra"]),
        ])
        replies.append("===FILE: projects/retra.md===\n---\ntitle: Retra\n---\n# Retra\nbody\n===END_FILE===\n"
                       f"===CLAIMS: projects/retra.md===\n{json.dumps(decisions)}\n===END_CLAIMS===\n")
        wiki_compiler.compile_daily_note(day)
        assert not replies, "one job, for the retra page"
        return (wiki / "projects" / "retra.md").read_text()

    page = compile_day(FRI, "retra://session/s-fri",
                       {"claims": [claim("Retra launches on Friday", "retra://session/s-fri")]})
    assert "<claims>" in prompts[-1] and "--- CLAIMS: projects/retra.md ---" in prompts[-1]
    assert "sources:\n  - retra://session/s-fri\nverified: " in page

    with db._conn() as c:
        c.execute("DELETE FROM agent_sessions WHERE session_id = 's-fri'")
    # The model ignores the flagged claim: its decisions are rejected and the stamp goes.
    page = compile_day(MON, "retra://session/s-mon", {"claims": []})
    assert "FLAGGED: cites retra://session/s-fri, which no longer exists" in prompts[-1]
    assert "verified:" not in page and "retra://session/s-fri" in page

    page = compile_day(MON + timedelta(days=1), "retra://session/s-mon",
                       {"claims": [claim("Retra launch moved to Monday", "retra://session/s-mon")],
                        "retractedClaimIds": [1]})
    assert "sources:\n  - retra://session/s-mon\nverified: " in page

    # A day that never names retra still gives its flagged page a job.
    with db._conn() as c:
        c.execute("DELETE FROM agent_sessions WHERE session_id = 's-mon'")
    monkeypatch.setattr(compile_input, "day_records", lambda d: [
        compile_input.Record(f"retra://day/{d}", "day", "stats"),
        compile_input.Record("retra://session/s-x", "session", "other: work", ["other"]),
    ])
    replies += ["===FILE: projects/retra.md===\n# Retra\n===END_FILE===\n", wiki_compiler.NO_NEW_PAGES]
    wiki_compiler.compile_daily_note(MON + timedelta(days=2))
    retra_prompt = next(p for p in prompts[-2:] if '<page path="projects/retra.md">' in p)
    assert "FLAGGED: cites retra://session/s-mon, which no longer exists" in retra_prompt


def test_hook_is_inactive_without_refs(tmp_path):
    hook = claims.CompileHook(FRI, "# Fri\nWorked on retra.\n", db_path=tmp_path / "never.db")
    assert hook.prompt_section(["projects/retra.md"]) == ""
    assert hook.after_write(tmp_path, "===CLAIMS: x.md===\n{}\n===END_CLAIMS===", []) == {}
    assert not (tmp_path / "never.db").exists()


def test_facts_tool(retra, monkeypatch):
    from mcp_server import store
    db = Database(store.db_path())
    _session(db, "s-fri", FRI, "Plan the Retra launch for Friday")
    _session(db, "s-mon", MON, "Move the Retra launch to Monday")
    _session(db, "s-bank", MON, "bank export")
    with db._conn() as c:
        first = add(c, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))["added"][0]
        add(c, MON, claim("Retra launch moved to Monday", "retra://session/s-mon", id=first))
        # Written before the blocklist covered "bank"; the reader hides it until a compile retracts it.
        cid = c.execute("INSERT INTO wiki_claims (page, subject, statement, confidence, valid_from) "
                        "VALUES (?, 'retra/exports', 'Exports run nightly', 'source-backed', ?)",
                        (PAGE, MON.isoformat())).lastrowid
        c.execute("INSERT INTO wiki_claim_evidence VALUES (?, 'retra://session/s-bank', 'v0')", (cid,))

    async def call(**args):
        async with Client(retra.mcp) as client:
            res = await client.call_tool("facts", args)
            return json.loads(res.content[0].text)

    now = asyncio.run(call(subject="retra"))
    assert [f["statement"] for f in now["facts"]] == ["Retra launch moved to Monday"]
    assert now["facts"][0]["replaced"][0]["statement"] == "Retra launches on Friday"
    assert now["facts"][0]["evidence"] == [{"resource": "retra://session/s-mon", "status": "ok"}]
    assert now["has_more"] is False and "freshness" in now

    then = asyncio.run(call(subject="retra/launch-date", as_of=FRI.isoformat()))
    assert [f["statement"] for f in then["facts"]] == ["Retra launches on Friday"]
    assert then["facts"][0]["changed_to"]["valid_from"] == MON.isoformat()


# ── Review fixes ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [{}, [], True, "1"])
def test_bad_revision_id_is_rejected_not_a_crash(conn, bad):
    add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))
    with pytest.raises(ClaimsRejected, match=r"claims\[0\]\.id must be a claim id"):
        add(conn, FRI, claim("Retra launches on Monday", "retra://session/s-fri", id=bad))


def test_session_version_covers_its_reply_and_files(conn):
    before = check(conn, "retra://session/s-fri").version
    conn.execute("UPDATE agent_sessions SET last_reply = 'corrected', files = '[\"a.py\"]' WHERE session_id = 's-fri'")
    assert check(conn, "retra://session/s-fri").version != before


def test_skipped_recheck_stays_due(conn):
    add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))
    # Monday had retra activity; Tuesday has none, but the claim was never rechecked.
    assert [i.kind for i in preflight(conn, MON + timedelta(days=1))[PAGE]] == ["recheck"]


def test_history_follows_a_claim_to_its_new_subject(conn):
    first = add(conn, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))["added"][0]
    add(conn, MON, claim("Retra releases on Monday", "retra://session/s-mon", id=first, subject="retra/release-date"))
    [f] = facts(conn, "retra/release-date")
    assert [p["id"] for p in f["replaced"]] == [first]


def _hook(db, tmp_path, day=FRI):
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True, exist_ok=True)
    (wiki / "CLAUDE.md").write_text("schema\n")
    (wiki / PAGE).write_text("---\ntitle: Retra\n---\n# Retra\n- launch Friday (retra://session/s-fri)\n")
    return wiki, claims.CompileHook(day, "retra://session/s-fri", db_path=db.db_path)


def test_rejected_claims_block_withholds_verified_and_touches_nothing_else(db, tmp_path):
    with db._conn() as c:
        add(c, FRI, claim("Retra launches on Friday", "retra://session/s-fri"))
    wiki, hook = _hook(db, tmp_path)
    hook.prompt_section([PAGE])
    out = hook.after_write(wiki, f"===CLAIMS: {PAGE}===\n{{\"claims\": 5}}\n===END_CLAIMS===\n"
                                 "===CLAIMS: CLAUDE.md===\n{}\n===END_CLAIMS===\n", [PAGE])
    assert out[PAGE]["verified"] is False and "verified:" not in (wiki / PAGE).read_text()
    assert (wiki / "CLAUDE.md").read_text() == "schema\n"

    # The same page with an accepted (empty) batch is verified.
    hook.after_write(wiki, f"===CLAIMS: {PAGE}===\n{{}}\n===END_CLAIMS===\n", [PAGE])
    assert "verified: " in (wiki / PAGE).read_text()


def test_lines_citing_private_records_are_removed_before_compile(db, tmp_path):
    _session(db, "s-bank", FRI, "Sort out the bank login")
    wiki, hook = _hook(db, tmp_path)
    (wiki / PAGE).write_text("# Retra\n- launch Friday (retra://session/s-fri)\n- bank login (retra://session/s-bank)\n")
    assert hook.scrub_private(wiki) == [PAGE]
    assert (wiki / PAGE).read_text() == "# Retra\n- launch Friday (retra://session/s-fri)\n"
