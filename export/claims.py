"""
Wiki claims with evidence (roadmap D1, with D5's evidence-based labels).

Every statement a wiki page makes is a row in `wiki_claims`, citing the
records it came from as `retra://` references in `wiki_claim_evidence`. The
resolver owns each reference's `version` (a hash of the cited rows); the
model only names references. Before a compile, `preflight` re-resolves every
live claim: a changed version makes it stale, missing rows make it
unresolved, and rows now covered by the privacy blocklist retract it
outright. New records about a claim's subject make it "needs recheck".

The model answers each page with sparse decisions (`confirmedClaimIds`,
`claims`, `retractedClaimIds`). `apply_decisions` checks them all before
writing any: every reference must resolve, and every flagged claim on the
page needs a decision ("evidence debt"). A revision never edits a row; it
inserts a new one and points the old one at it, so `facts(subject, as_of)`
can say what was true on any day and what it changed to.

Reference scheme (built by the compile input, roadmap D0):
    retra://session/<session_id>                       agent_sessions
    retra://span/<date>T<HH:MM>-<HH:MM>?app=<app>      window_events
    retra://url/<id>                                   url_events
    retra://screen/<id>                                screenshots.ocr_text
    retra://day/<date>                                 daily_summaries
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, unquote

from mcp_server.store import is_private, redact_text, session_is_private

# Window titles, browser pages and screen text: easy to see in passing, so on
# their own they back a claim only weakly.
WEAK_KINDS = {"span", "url", "screen"}
LABELS = ("confirmed", "source-backed", "watchlist", "contested")
# Issues the model must answer before a page can be verified.
DEBT = ("stale", "unresolved", "private", "recheck")
MAX_STATEMENT_CHARS = 500

REF_RE = re.compile(r"retra://(?:session|span|url|screen|day)/[^\s)\]>\"'`,]+")
_CLAIMS_BLOCK_RE = re.compile(r"===CLAIMS:\s*(.+?)===\s*\n(.*?)===END_CLAIMS===", re.DOTALL)
_SPAN_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})-(\d{2}:\d{2})")


# ── Resolver ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resolution:
    """What a reference points at now. status: ok, gone (rows deleted),
    private (rows the blocklist now covers) or invalid (not a reference)."""
    status: str
    kind: str = ""
    version: str = ""
    snippet: str = ""
    day: str = ""


def _hash(values) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, default=str).encode()).hexdigest()


def _short(text: Optional[str], n: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _session(conn, key: str) -> Resolution:
    row = conn.execute("SELECT * FROM agent_sessions WHERE session_id = ?", (unquote(key),)).fetchone()
    if row is None:
        return Resolution("gone", "session")
    if session_is_private(row):
        return Resolution("private", "session")
    version = _hash([row[k] for k in ("title", "ended_at", "prompt_count", "prs", "last_status")])
    snippet = _short(f"{row['title'] or ''}: {row['last_status'] or ''}")
    return Resolution("ok", "session", version, snippet, (row["started_at"] or row["ended_at"] or "")[:10])


def _span(conn, key: str) -> Resolution:
    path, _, query = key.partition("?")
    m = _SPAN_RE.fullmatch(path)
    if not m:
        raise ValueError(f"bad span {key!r}")
    day, a, b = m.groups()
    start, end = datetime.fromisoformat(f"{day}T{a}"), datetime.fromisoformat(f"{day}T{b}")
    if end <= start:
        raise ValueError(f"empty span {key!r}")
    sql = ("SELECT timestamp, app_name, window_title, url FROM window_events "
           "WHERE timestamp >= ? AND timestamp < ?")
    params = [start.isoformat(), end.isoformat()]
    if app := parse_qs(query).get("app", [None])[0]:
        sql += " AND app_name = ?"
        params.append(app)
    rows = conn.execute(sql + " ORDER BY timestamp, id", params).fetchall()
    if not rows:
        return Resolution("gone", "span")
    if any(is_private(r["app_name"], r["window_title"] or "", r["url"] or "") for r in rows):
        return Resolution("private", "span")
    titles = list(dict.fromkeys(r["window_title"] for r in rows if r["window_title"]))
    return Resolution("ok", "span", _hash([tuple(r) for r in rows]), _short(" | ".join(titles[:3])), day)


def _url(conn, key: str) -> Resolution:
    row = conn.execute("SELECT * FROM url_events WHERE id = ?", (int(key),)).fetchone()
    if row is None:
        return Resolution("gone", "url")
    if is_private(row["app_name"], row["page_title"] or "", row["url"] or ""):
        return Resolution("private", "url")
    version = _hash([row[k] for k in ("timestamp", "url", "page_title", "duration_seconds")])
    return Resolution("ok", "url", version, _short(row["page_title"] or row["url"]), row["timestamp"][:10])


def _screen(conn, key: str) -> Resolution:
    row = conn.execute("SELECT * FROM screenshots WHERE id = ?", (int(key),)).fetchone()
    if row is None or not row["ocr_text"]:
        return Resolution("gone", "screen")
    text = row["ocr_text"]
    if is_private(row["app_name"], row["window_title"] or "") or redact_text(text) != text:
        return Resolution("private", "screen")
    version = _hash([row[k] for k in ("timestamp", "app_name", "window_title", "ocr_text")])
    return Resolution("ok", "screen", version, _short(text), row["timestamp"][:10])


def _day(conn, key: str) -> Resolution:
    day = date.fromisoformat(key).isoformat()
    row = conn.execute("SELECT * FROM daily_summaries WHERE date = ?", (day,)).fetchone()
    if row is None:
        return Resolution("gone", "day")
    stats = {k: row[k] for k in row.keys() if k not in ("id", "created_at", "ai_summary_at", "ai_summary")}
    snippet = (f"{row['total_tracked_minutes']}m tracked, {row['focus_minutes']}m focus, "
               f"focus score {row['focus_score']}")
    return Resolution("ok", "day", _hash(sorted(stats.items())), snippet, day)


_RESOLVERS = {"session": _session, "span": _span, "url": _url, "screen": _screen, "day": _day}


def check(conn: sqlite3.Connection, resource: str) -> Resolution:
    """Resolve a reference against the current records, saying why when it
    doesn't. Works on read-only connections and on databases that predate
    the cited table."""
    m = re.fullmatch(r"retra://(\w+)/(.+)", (resource or "").strip())
    if not m or m.group(1) not in _RESOLVERS:
        return Resolution("invalid")
    try:
        return _RESOLVERS[m.group(1)](conn, m.group(2))
    except (ValueError, TypeError):
        return Resolution("invalid", m.group(1))
    except sqlite3.OperationalError:
        return Resolution("gone", m.group(1))


def resolve(conn: sqlite3.Connection, resource: str) -> Optional[tuple[str, str]]:
    """(version, snippet) of the rows a reference cites, or None when they're
    gone, private or the reference is malformed."""
    r = check(conn, resource)
    return (r.version, r.snippet) if r.status == "ok" else None


def find_refs(text: str) -> list[str]:
    return list(dict.fromkeys(REF_RE.findall(text or "")))


def label(resolutions: Iterable[Resolution]) -> str:
    """D5 confidence from the evidence: 2+ distinct days or source kinds is
    confirmed; only window titles, pages or screen text seen once is
    watchlist; anything else (one session or day) is source-backed.
    `contested` is the one label the model sets."""
    resolutions = list(resolutions)
    days = {r.day for r in resolutions if r.day}
    kinds = {r.kind for r in resolutions}
    if len(days) >= 2 or len(kinds) >= 2:
        return "confirmed"
    if kinds and kinds <= WEAK_KINDS:
        return "watchlist"
    return "source-backed"


# ── Claims and their issues ───────────────────────────────────────

def tables_exist(conn: sqlite3.Connection) -> bool:
    """False on databases the writable side hasn't migrated yet (the MCP
    reader never migrates)."""
    return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                        "AND name IN ('wiki_claims', 'wiki_claim_evidence')").fetchone()[0] == 2


_LIVE = "retracted_at IS NULL AND superseded_by IS NULL"


def live_claims(conn: sqlite3.Connection, page: Optional[str] = None) -> list[sqlite3.Row]:
    if page is None:
        return conn.execute(f"SELECT * FROM wiki_claims WHERE {_LIVE} ORDER BY id").fetchall()
    return conn.execute(f"SELECT * FROM wiki_claims WHERE page = ? AND {_LIVE} ORDER BY id", (page,)).fetchall()


def evidence(conn: sqlite3.Connection, claim_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT resource, version FROM wiki_claim_evidence WHERE claim_id = ? ORDER BY resource",
                        (claim_id,)).fetchall()


@dataclass(frozen=True)
class Issue:
    """Why a live claim needs a decision. kind: stale (cited rows changed),
    unresolved (cited rows gone), private (cited rows now blocked),
    recheck (newer records about its subject)."""
    claim_id: int
    kind: str
    resource: str = ""

    def describe(self) -> str:
        return {
            "stale": f"cites {self.resource}, which changed since",
            "unresolved": f"cites {self.resource}, which no longer exists",
            "private": f"cites {self.resource}, which is now private",
            "recheck": "newer records mention its subject",
        }[self.kind]


def day_subjects(conn: sqlite3.Connection, day: date) -> set[str]:
    """Lowercased project and repo names with activity on `day`, for
    subject staleness."""
    start, end = day.isoformat(), f"{day.isoformat()}T23:59:59.999999"
    names: set[str] = set()
    try:
        for r in conn.execute("SELECT project, repo FROM agent_sessions WHERE started_at <= ? AND ended_at >= ?",
                              (end, start)):
            names |= {n.lower() for n in (r["project"], r["repo"]) if n}
    except sqlite3.OperationalError:
        pass
    for r in conn.execute("SELECT DISTINCT project_hint FROM window_events "
                          "WHERE timestamp >= ? AND timestamp <= ? AND project_hint IS NOT NULL", (start, end)):
        names.add(r[0].lower())
    return names


def _about(subject: str, names: set[str]) -> bool:
    s = subject.lower()
    return any(s == n or s.startswith((f"{n}/", f"{n}:")) for n in names)


def claim_issues(conn: sqlite3.Connection, claim: sqlite3.Row, day: Optional[date] = None,
                 subjects: Optional[set[str]] = None) -> list[Issue]:
    issues = []
    for e in evidence(conn, claim["id"]):
        r = check(conn, e["resource"])
        if r.status == "private":
            issues.append(Issue(claim["id"], "private", e["resource"]))
        elif r.status != "ok":
            issues.append(Issue(claim["id"], "unresolved", e["resource"]))
        elif r.version != e["version"]:
            issues.append(Issue(claim["id"], "stale", e["resource"]))
    checked = claim["checked_on"] or claim["valid_from"]
    if day and subjects and checked < day.isoformat() and _about(claim["subject"], subjects):
        issues.append(Issue(claim["id"], "recheck"))
    return issues


def page_issues(conn: sqlite3.Connection, page: str, day: Optional[date] = None) -> list[Issue]:
    subjects = day_subjects(conn, day) if day else None
    return [i for c in live_claims(conn, page) for i in claim_issues(conn, c, day, subjects)]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def preflight(conn: sqlite3.Connection, day: Optional[date] = None) -> dict[str, list[Issue]]:
    """Re-resolve every live claim before a compile. Claims citing records
    the blocklist now covers are retracted here and never shown to the
    model: privacy, not accuracy, so they are dropped, not reworded.
    Returns the remaining issues by page; those pages need a job."""
    subjects = day_subjects(conn, day) if day else None
    out: dict[str, list[Issue]] = {}
    private: list[int] = []
    for c in live_claims(conn):
        issues = claim_issues(conn, c, day, subjects)
        if any(i.kind == "private" for i in issues):
            private.append(c["id"])
            out.setdefault(c["page"], [])
        elif issues:
            out.setdefault(c["page"], []).extend(issues)
    if private:
        conn.executemany("UPDATE wiki_claims SET retracted_at = ?, retracted_reason = 'private' WHERE id = ?",
                         [(_now(), i) for i in private])
    return out


def page_status(conn: sqlite3.Connection, page: str, day: Optional[date] = None) -> dict:
    """A page is verified when it has at least one live claim and none of
    them has an open issue."""
    live = live_claims(conn, page)
    issues = page_issues(conn, page, day)
    sources = sorted({e["resource"] for c in live for e in evidence(conn, c["id"])})
    return {"claims": len(live), "issues": issues, "sources": sources,
            "verified": bool(live) and not issues}


# ── Model decisions ───────────────────────────────────────────────

class ClaimsRejected(ValueError):
    """A page's decisions broke a rule. Nothing from the batch was applied."""

    def __init__(self, page: str, errors: list[str]):
        self.page, self.errors = page, errors
        super().__init__(f"{page}: " + "; ".join(errors))


def _ids(value, field: str, errors: list[str]) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in value):
        errors.append(f"{field} must be a list of claim ids")
        return []
    return value


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def apply_decisions(conn: sqlite3.Connection, page: str, decisions: dict, *, day: date,
                    run_id: str) -> dict:
    """Validate a page's decisions and apply them as one batch, or raise
    ClaimsRejected and change nothing.

    Rules: ids must be live claims on this page, each in at most one list;
    a new or revised claim needs a subject, a statement and evidence that
    resolves now; only `contested` may be set as a label, and it must cite
    at least two records (both sides); every flagged claim needs a decision,
    an unresolved one can't simply be confirmed, and a private one can only
    be retracted; the page keeps at least one claim.
    """
    errors: list[str] = []
    if not isinstance(decisions, dict):
        raise ClaimsRejected(page, ["decisions must be a JSON object"])
    confirmed = _ids(decisions.get("confirmedClaimIds"), "confirmedClaimIds", errors)
    retracted = _ids(decisions.get("retractedClaimIds"), "retractedClaimIds", errors)
    proposed = decisions.get("claims") or []
    if not isinstance(proposed, list) or not all(isinstance(c, dict) for c in proposed):
        errors.append("claims must be a list of objects")
        proposed = []

    live = {c["id"]: c for c in live_claims(conn, page)}
    revised = [c["id"] for c in proposed if c.get("id") is not None]
    for i, n in Counter(confirmed + retracted + revised).items():
        if i not in live:
            errors.append(f"claim {i} is not a live claim on {page}")
        elif n > 1:
            errors.append(f"claim {i} has more than one decision")

    checked: list[tuple[dict, str, str, list[tuple[str, Resolution]], Optional[sqlite3.Row]]] = []
    for n, c in enumerate(proposed):
        where = f"claims[{n}]"
        old = live.get(c.get("id"))
        subject = str(c.get("subject") or (old["subject"] if old else "")).strip().lower()
        statement = " ".join(str(c.get("statement") or "").split())
        refs = c.get("evidence") or []
        if not subject:
            errors.append(f"{where}: no subject")
        if not statement:
            errors.append(f"{where}: no statement")
        elif len(statement) > MAX_STATEMENT_CHARS:
            errors.append(f"{where}: statement over {MAX_STATEMENT_CHARS} chars; split it")
        if not isinstance(refs, list) or not refs or not all(isinstance(r, str) for r in refs):
            errors.append(f"{where}: evidence must be a non-empty list of retra:// references")
            refs = []
        resolved = []
        for ref in dict.fromkeys(r.strip() for r in refs):
            r = check(conn, ref)
            if r.status != "ok":
                errors.append(f"{where}: {ref} is {r.status}")
            resolved.append((ref, r))
        conf = c.get("confidence")
        if conf not in (None, "contested"):
            errors.append(f"{where}: confidence {conf!r} can't be set; only 'contested' is, the rest come from evidence")
        elif conf == "contested" and len(resolved) < 2:
            errors.append(f"{where}: contested must cite both sides")
        checked.append((c, subject, statement, resolved, old))

    handled = set(confirmed) | set(retracted) | set(revised)
    for issue in page_issues(conn, page, day):
        cid = issue.claim_id
        if issue.kind == "private":
            if cid not in retracted:
                errors.append(f"claim {cid} {issue.describe()}: it must be retracted")
        elif issue.kind == "unresolved" and cid in confirmed:
            errors.append(f"claim {cid} {issue.describe()}: revise it with current evidence or retract it")
        elif cid not in handled:
            errors.append(f"evidence debt: claim {cid} {issue.describe()}; confirm, revise or retract it")

    if len(live) - len(set(retracted) | set(revised)) + len(proposed) < 1:
        errors.append(f"{page} must keep at least one claim")
    if errors:
        raise ClaimsRejected(page, errors)

    now, today = _now(), day.isoformat()
    added, superseded = [], []
    conn.execute("SAVEPOINT wiki_claims_batch")
    try:
        for cid in retracted:
            conn.execute("UPDATE wiki_claims SET retracted_at = ?, retracted_reason = 'retracted' WHERE id = ?",
                         (now, cid))
        for cid in confirmed:
            for e in evidence(conn, cid):
                conn.execute("UPDATE wiki_claim_evidence SET version = ? WHERE claim_id = ? AND resource = ?",
                             (check(conn, e["resource"]).version, cid, e["resource"]))
            conn.execute("UPDATE wiki_claims SET checked_on = ? WHERE id = ?", (today, cid))
        for c, subject, statement, resolved, old in checked:
            # Same statement with new evidence is a re-citation, not a change of fact.
            valid_from = old["valid_from"] if old and _same(old["statement"], statement) else today
            confidence = "contested" if c.get("confidence") == "contested" else label(r for _, r in resolved)
            new_id = conn.execute(
                "INSERT INTO wiki_claims (page, subject, statement, confidence, valid_from, checked_on, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (page, subject, statement, confidence, valid_from, today, run_id),
            ).lastrowid
            conn.executemany("INSERT INTO wiki_claim_evidence (claim_id, resource, version) VALUES (?, ?, ?)",
                             [(new_id, ref, r.version) for ref, r in resolved])
            added.append(new_id)
            if old:
                conn.execute("UPDATE wiki_claims SET superseded_by = ? WHERE id = ?", (new_id, old["id"]))
                superseded.append(old["id"])
        conn.execute("RELEASE wiki_claims_batch")
    except Exception:
        conn.execute("ROLLBACK TO wiki_claims_batch")
        conn.execute("RELEASE wiki_claims_batch")
        raise
    return {"confirmed": confirmed, "added": added, "superseded": superseded, "retracted": retracted}


# ── Facts with history (B1) ───────────────────────────────────────

def facts(conn: sqlite3.Connection, subject: str, as_of: Optional[date] = None) -> list[dict]:
    """Claims about `subject` (and keys under it, 'retra' → 'retra/launch')
    that held on `as_of` (default: now), each with the versions it replaced
    and, for a past day, what it later changed to. Retracted claims were
    never true and are left out. Newest first."""
    if not tables_exist(conn):
        return []
    s = subject.strip().lower()
    rows = conn.execute("SELECT * FROM wiki_claims WHERE lower(subject) = ? OR lower(subject) LIKE ? "
                        "ESCAPE '\\' ORDER BY valid_from, id",
                        (s, s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%")).fetchall()
    by_id = {r["id"]: r for r in rows}
    cutoff = (as_of or date.max).isoformat()
    preds: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        if r["superseded_by"] is not None:
            preds.setdefault(r["superseded_by"], []).append(r)

    def successor(r) -> Optional[sqlite3.Row]:
        # A revision may have moved the claim to another subject.
        sid = r["superseded_by"]
        if sid is not None and sid not in by_id:
            by_id[sid] = conn.execute("SELECT * FROM wiki_claims WHERE id = ?", (sid,)).fetchone()
        return by_id.get(sid)

    def ends(r) -> Optional[str]:
        nxt = successor(r)
        return nxt["valid_from"] if nxt is not None else None

    def held(r) -> bool:
        if r["retracted_at"] or r["valid_from"] > cutoff:
            return False
        if r["superseded_by"] is None:
            return True
        end = ends(r)
        return end is not None and end > cutoff

    out = []
    for r in rows:
        if not held(r):
            continue
        replaced, stack = [], list(preds.get(r["id"], []))
        while stack:
            p = stack.pop()
            if not p["retracted_at"]:
                replaced.append({"id": p["id"], "statement": p["statement"],
                                 "valid_from": p["valid_from"], "until": ends(p)})
            stack.extend(preds.get(p["id"], []))
        fact = {
            "id": r["id"], "subject": r["subject"], "statement": r["statement"], "page": r["page"],
            "confidence": r["confidence"], "valid_from": r["valid_from"],
            "evidence": [dict(e) for e in evidence(conn, r["id"])],
            "replaced": sorted(replaced, key=lambda p: p["valid_from"], reverse=True),
        }
        nxt = successor(r)
        if nxt is not None:
            fact["changed_to"] = {"id": nxt["id"], "statement": nxt["statement"], "valid_from": nxt["valid_from"]}
        out.append(fact)
    return sorted(out, key=lambda f: (f["valid_from"], f["id"]), reverse=True)


# ── Page frontmatter ──────────────────────────────────────────────

def stamp_frontmatter(text: str, sources: list[str], verified: Optional[str]) -> str:
    """Set the page's `sources:` list and `verified:` time, dropping
    `verified:` when there is evidence debt. Other frontmatter is kept."""
    if text.startswith("---\n") and (end := text.find("\n---", 4)) >= 0:
        lines, body = text[4:end].splitlines(), text[end + 4:]
    else:
        lines, body = [], "\n" + text
    kept, skipping = [], False
    for line in lines:
        key = line.split(":", 1)[0].strip()
        if not line.startswith((" ", "\t", "-")):
            skipping = key in ("sources", "verified")
            if skipping:
                continue
        elif skipping:
            continue
        kept.append(line)
    if sources:
        kept += ["sources:", *(f"  - {s}" for s in sources)]
    if verified:
        kept.append(f"verified: {verified}")
    return "---\n" + "\n".join(kept) + "\n---" + body


# ── Compile hook ──────────────────────────────────────────────────

CLAIM_RULES = """\
Each wiki page also keeps a list of claims: the statements it makes, each
citing the records it came from. A claim is one idea that could be shown
false and would change the reader's picture if it were, not trivia.

- subject: a lowercase key for what it is about: a project slug, a person,
  or "<project>/<topic>" (e.g. "retra/launch-date").
- evidence: retra:// references copied exactly from the input. Never invent
  one. Don't give versions or confidence; they come from the records.
- After each page you output, give its decisions:
===CLAIMS: projects/example.md===
{"confirmedClaimIds": [], "claims": [{"subject": "...", "statement": "...", "evidence": ["retra://..."]}], "retractedClaimIds": []}
===END_CLAIMS===
- Claims you don't mention are kept, except FLAGGED ones: each needs a
  decision (confirm, revise or retract) or all of the page's decisions are
  rejected. A claim whose record no longer exists can't be confirmed.
- To change a claim (a date moved, a plan changed), send it again with its
  "id" and the new statement and evidence. The old one is kept as history,
  so don't retract a claim that was true until it changed.
- Retract only what was never true.
- Set "confidence": "contested" only when records disagree and it isn't
  settled; cite both sides and keep both, with dates, under "## Contested".
- Every page keeps at least one claim."""


def prompt_section(conn: sqlite3.Connection, pages: Iterable[str], issues: dict[str, list[Issue]]) -> str:
    """The claims block for a compile prompt: the rules, then each page's live
    claims with their flags. Pages with issues are included even when the
    day's records don't mention them."""
    blocks = []
    for page in sorted(set(pages) | set(issues)):
        flags: dict[int, list[str]] = {}
        for i in issues.get(page, []):
            flags.setdefault(i.claim_id, []).append(i.describe())
        lines = []
        for c in live_claims(conn, page):
            refs = ", ".join(e["resource"] for e in evidence(conn, c["id"]))
            flag = f"  FLAGGED: {'; '.join(flags[c['id']])}" if c["id"] in flags else ""
            lines.append(f"[{c['id']}] {c['subject']} ({c['confidence']}, since {c['valid_from']}): "
                         f"{c['statement']}  evidence: {refs}{flag}")
        blocks.append(f"--- CLAIMS: {page} ---\n" + ("\n".join(lines) or "(none yet)"))
    return f"\n\n<claims>\n{CLAIM_RULES}\n\n" + "\n\n".join(blocks) + "\n</claims>\n"


class CompileHook:
    """The seam between export/wiki_compiler.py and claims.

    Inactive (no prompt text, no writes) when the compile input carries no
    retra:// references. When active it runs the preflight on creation, adds
    each job's claims block to its prompt, and after the job's pages are
    written applies each page's decisions and stamps `sources:` /
    `verified:` into its frontmatter.
    """

    def __init__(self, day: date, compile_input: str, db_path=None):
        from mcp_server.store import db_path as default_db_path
        from storage.database import Database
        self.day = day
        self.active = bool(REF_RE.search(compile_input or ""))
        self.run_id = _now()
        self.issues: dict[str, list[Issue]] = {}
        self.results: dict[str, dict] = {}
        self._shown: set[str] = set()
        self._db = Database(db_path or default_db_path()) if self.active else None
        if self.active:
            with self._db._conn() as conn:
                self.issues = preflight(conn, day)

    def prompt_section(self, pages: Iterable[str]) -> str:
        """The claims block for one compile job: the rules, then the live
        claims of the job's pages with their flags."""
        if not self.active:
            return ""
        self._shown = set(pages)
        with self._db._conn() as conn:
            return prompt_section(conn, self._shown, {p: self.issues[p] for p in self._shown if p in self.issues})

    def after_write(self, wiki_dir: Path, response: str, written: list[str]) -> dict[str, dict]:
        """Apply the response's claims blocks, one all-or-nothing batch per
        page, then stamp every page this compile wrote or flagged. Call
        with the wiki lock held, before the compile is committed."""
        if not self.active:
            return {}
        root = Path(wiki_dir).resolve()
        with self._db._conn() as conn:
            for page, raw in _CLAIMS_BLOCK_RE.findall(response or ""):
                page = page.strip()
                full = (root / page).resolve()
                if not full.is_relative_to(root) or full.suffix != ".md" or not full.exists():
                    self.results[page] = {"rejected": ["no such page"]}
                    continue
                if page not in self._shown and page not in written:
                    self.results[page] = {"rejected": ["page wasn't part of this compile"]}
                    continue
                try:
                    self.results[page] = apply_decisions(conn, page, json.loads(raw), day=self.day,
                                                         run_id=self.run_id)
                except (ClaimsRejected, ValueError) as e:
                    self.results[page] = {"rejected": getattr(e, "errors", [str(e)])}
                    print(f"  Claims rejected for {page}: {e}")
            # Pages outside this compile keep their stamp; preflight put any
            # page with new issues into self.issues.
            for page in sorted(set(written) | set(self.results) | set(self.issues)):
                full = (root / page).resolve()
                if not full.is_relative_to(root) or not full.exists():
                    continue
                st = page_status(conn, page, self.day)
                text = full.read_text(encoding="utf-8")
                stamped = stamp_frontmatter(text, st["sources"], self.run_id if st["verified"] else None)
                if stamped != text:
                    full.write_text(stamped, encoding="utf-8")
                self.results.setdefault(page, {})["verified"] = st["verified"]
        return self.results
