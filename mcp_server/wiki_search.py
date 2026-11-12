"""Section-level search and reads over the retra-wiki folder.

The index is an in-memory FTS5 table built from the files on each call, so it
can never be stale (the whole wiki is under a megabyte and builds in
milliseconds). A page is split at its H2 headings, and an H2 that has H3
children (the dated entries under "## Sessions") is split at those instead.
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from export.wiki_compiler import _page_names

# Root pages that aren't notes: the generated index, the ingest log and the
# compiler's schema.
SKIP_PAGES = {"index", "log", "CLAUDE"}
# Link lists that would match every query about their targets.
SKIP_SECTIONS = {"related", "related pages", "see also", "links"}
EXCERPT_CHARS = 600
# Longer sections (a dated log kept as paragraphs under one heading) are
# ranked by paragraph chunks, or they'd contain every query word somewhere.
CHUNK_CHARS = 1500
# Confidence labels the compiler writes inline as Obsidian tags.
LABELS = ("confirmed", "source-backed", "contested", "watchlist", "saved-context")
_LABEL_RE = re.compile(r"(?<![\w/#])#(" + "|".join(LABELS) + r")(?![\w-])")
_FRONT_RE = re.compile(r"\A---\n(.*?)\n---[ \t]*\n?", re.S)
_HEADING_RE = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$", re.M)
# Code spans, wikilink targets, PR numbers and ticket keys.
_IDENT_RE = re.compile(r"`([^`\n]+)`|\[\[([^\]|#\n]+)|(#\d+)\b|\b([A-Z][A-Z0-9]+-\d+)\b")
_WORD_RE = re.compile(r"[^\W_]+")


@dataclass
class Section:
    page: str
    title: str
    description: str
    heading: Optional[str]  # None for the text before the first H2
    body: str
    names: set[str]  # the page's slug, aliases and title, lowercased

    @property
    def ref(self) -> str:
        return f"{self.page}#{self.heading}" if self.heading else self.page


def _front_field(front: str, key: str) -> str:
    m = re.search(rf"^{key}:[ \t]*(.+)$", front, re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def headings(text: str) -> list[tuple[int, str, int, int]]:
    """(level, heading, start, end) for each H2/H3, where a section runs until
    the next heading of the same or a higher level."""
    found = [(len(m.group(1)), m.group(2).strip(), m.start()) for m in _HEADING_RE.finditer(text)]
    found = [h for h in found if h[0] > 1]
    out = []
    for i, (level, heading, start) in enumerate(found):
        end = next((s for lv, _, s in found[i + 1:] if lv <= level), len(text))
        out.append((level, heading, start, end))
    return out


def split_page(page: str, text: str) -> list[Section]:
    front_m = _FRONT_RE.match(text)
    front = front_m.group(1) if front_m else ""
    body = text[front_m.end():] if front_m else text
    h1 = re.search(r"^#[ \t]+(.+)$", body, re.M)
    title = _front_field(front, "title") or (h1.group(1).strip() if h1 else Path(page).name)
    names = _page_names(page + ".md", text) | {title.lower()}
    tags = _front_field(front, "tags").strip("[]")

    hs = headings(body)
    first = hs[0][2] if hs else len(body)
    preamble = body[h1.end() if h1 and h1.start() < first else 0:first].strip()
    description = preamble.split("\n\n", 1)[0][:300]

    def section(heading, start, end):
        chunk = body[start:end].split("\n", 1)
        return Section(page, title, description, heading, chunk[1].strip() if len(chunk) > 1 else "", names)

    def chunks(s: Section) -> list[Section]:
        if len(s.body) <= CHUNK_CHARS:
            return [s]
        out, cur = [], ""
        for para in s.body.split("\n\n"):
            if cur and len(cur) + len(para) > CHUNK_CHARS:
                out.append(cur)
                cur = ""
            cur = f"{cur}\n\n{para}" if cur else para
        return [Section(s.page, s.title, s.description, s.heading, c, s.names) for c in out + [cur]]

    sections = [Section(page, title, description, None, (preamble + "\n" + tags).strip(), names)]
    skipped = [(s, e) for lv, h, s, e in hs if lv == 2 and h.lower() in SKIP_SECTIONS]
    for level, heading, start, end in hs:
        if any(s <= start < e for s, e in skipped):
            continue
        children = [h for h in hs if h[0] == 3 and start < h[2] < end] if level == 2 else []
        # An H2 with H3s is searched as its H3s, plus any text before the first.
        sections.append(section(heading, start, children[0][2] if children else end))
    return [c for s in sections if s.body for c in chunks(s)]


def _identifiers(s: Section) -> str:
    ids = {next(g for g in m.groups() if g) for m in _IDENT_RE.finditer(s.body)}
    return " ".join(sorted(ids | s.names))


def _excerpt(body: str, words: list[str]) -> str:
    """Up to EXCERPT_CHARS around the line with the most query words."""
    best, best_n, pos = 0, 0, 0
    for line in body.split("\n"):
        tokens = set(_WORD_RE.findall(line.lower()))
        n = sum(any(t.startswith(w) for t in tokens) if len(w) >= 3 else w in tokens for w in words)
        if n > best_n:
            best, best_n = pos, n
        pos += len(line) + 1
    start = max(0, best - 120)
    text = body[start:start + EXCERPT_CHARS]
    return ("…" if start else "") + text + ("…" if start + EXCERPT_CHARS < len(body) else "")


def search(sections: Iterable[Section], query: str, project_names: set[str] = frozenset()) -> list[dict]:
    """Sections matching any word of `query`, best first: sections of a page
    named in `project_names` first, then by how many query words they contain,
    then by bm25 (title 8, description 4, heading 6, prose 1, identifiers 3)."""
    words = [w.lower() for w in _WORD_RE.findall(query)]
    if not words:
        return []
    sections = list(sections)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE w USING fts5(title, description, heading, prose, identifiers)")
    conn.executemany(
        "INSERT INTO w(rowid, title, description, heading, prose, identifiers) VALUES (?, ?, ?, ?, ?, ?)",
        [(i, s.title, s.description, s.heading or "", s.body, _identifiers(s)) for i, s in enumerate(sections)],
    )
    # Short words ("pr", "14") match whole words only, or they'd match half the wiki.
    words = list(dict.fromkeys(words))
    match = " OR ".join(f'"{w}"*' if len(w) >= 3 else f'"{w}"' for w in words)
    rows = conn.execute("SELECT rowid, bm25(w, 8, 4, 6, 1, 3) FROM w WHERE w MATCH ?", (match,)).fetchall()
    conn.close()

    results = []
    for rowid, rank in rows:
        s = sections[rowid]
        tokens = set(_WORD_RE.findall(f"{s.title} {s.description} {s.heading or ''} {s.body} {_identifiers(s)}".lower()))
        coverage = sum(any(t.startswith(w) for t in tokens) if len(w) >= 3 else w in tokens for w in words)
        results.append((not (s.names & project_names), -coverage, rank, s))
    results.sort(key=lambda r: (r[0], r[1], r[2], r[3].page, r[3].heading or ""))
    seen: set[str] = set()
    results = [r for r in results if not (r[3].ref in seen or seen.add(r[3].ref))]
    return [
        {
            "ref": s.ref, "page": s.page, "section": s.heading, "title": s.title,
            "matched_words": -neg_cov, "of_words": len(words),
            "labels": sorted(set(_LABEL_RE.findall(s.body))),
            "excerpt": _excerpt(s.body, words),
        }
        for _, neg_cov, _, s in results
    ]


def read_sections(text: str, wanted: list[str]) -> tuple[list[dict], list[str]]:
    """The named H2/H3 sections of a page, whole (an H2 includes its H3s),
    matched case-insensitively, plus the names that matched nothing. A heading
    used more than once returns every occurrence, joined, since search refs
    them all as one `page#heading`."""
    body = _FRONT_RE.sub("", text, count=1)
    hs = headings(body)
    found, missing = [], []
    for name in wanted:
        key = name.strip().lstrip("#").strip().lower()
        hits, end = [], -1
        for _, heading, start, stop in hs:
            if heading.lower() == key and start >= end:  # skip one nested in the last
                hits.append(body[start:stop].strip())
                end = stop
        if hits:
            found.append({"section": next(h[1] for h in hs if h[1].lower() == key), "content": "\n\n".join(hits)})
        else:
            missing.append(name)
    return found, missing
