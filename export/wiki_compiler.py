"""
Retra Wiki Compiler
Reads daily notes from Retra's Obsidian folder and updates the retra-wiki/ pages.
Uses Claude API to do the actual compilation.
"""

import json
import os
import re
import ssl
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ssl_context = None

from config.settings import get_settings
from export import pipeline_status


def _get_wiki_dir() -> Path:
    settings = get_settings()
    return settings.obsidian.resolved_vault_path / "retra-wiki"


def _get_daylens_dir() -> Path:
    settings = get_settings()
    return settings.obsidian.output_dir


def compile_daily_note(target_date: date = None):
    """
    Read a daily note and trigger wiki update.
    Sends the daily note + CLAUDE.md schema + relevant existing wiki pages
    to Claude, and writes back the updated pages.
    """
    if target_date is None:
        target_date = date.today()

    wiki_dir = _get_wiki_dir()
    daylens_dir = _get_daylens_dir()

    # Read the daily note
    daily_note_path = daylens_dir / f"{target_date.isoformat()}.md"
    if not daily_note_path.exists():
        print(f"[Retra] No daily note found for {target_date}")
        return

    daily_note_content = daily_note_path.read_text(encoding="utf-8")

    # Read the schema
    schema_path = wiki_dir / "CLAUDE.md"
    if not schema_path.exists():
        print(f"[Retra] Wiki not initialized. Run scaffolding first.")
        return
    schema_content = schema_path.read_text(encoding="utf-8")

    # Read the current index
    index_path = wiki_dir / "index.md"
    index_content = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    # Read existing wiki pages
    existing_pages = _read_existing_pages(wiki_dir)

    # Build the prompt
    prompt = _build_ingest_prompt(
        schema=schema_content,
        daily_note=daily_note_content,
        daily_note_date=target_date.isoformat(),
        index=index_content,
        existing_pages=existing_pages,
    )

    # Call Claude API
    response = _call_claude(prompt)

    if response:
        # _apply_wiki_updates rebuilds index.md; log.md is ours too, so the
        # LLM can never silently drop either.
        written = _apply_wiki_updates(wiki_dir, response)
        _append_log_entry(
            wiki_dir,
            target_date,
            kind="ingest",
            summary=_summarize_written(written),
        )
        print(f"[Retra] Wiki compiled for {target_date}")
        pipeline_status.record("wiki", ok=True)

        # Auto weekly rollup on Sundays (weekday 6 = Sunday)
        if target_date.weekday() == 6:
            try:
                print(f"[Retra] Sunday detected — auto-generating weekly rollup...")
                compile_week(target_date)
            except Exception as e:
                print(f"[Retra] Auto weekly rollup failed: {e}")
    else:
        print(f"[Retra] Wiki compilation failed for {target_date}")
        pipeline_status.record("wiki", ok=False)


def compile_week(end_date: date = None):
    """Generate or update the weekly rollup."""
    if end_date is None:
        end_date = date.today()

    wiki_dir = _get_wiki_dir()
    daylens_dir = _get_daylens_dir()

    # Read all daily notes for the past 7 days
    daily_notes = {}
    for i in range(7):
        d = end_date - timedelta(days=i)
        note_path = daylens_dir / f"{d.isoformat()}.md"
        if note_path.exists():
            daily_notes[d.isoformat()] = note_path.read_text(encoding="utf-8")

    if not daily_notes:
        print("[Retra] No daily notes found for the week")
        return

    schema_path = wiki_dir / "CLAUDE.md"
    schema_content = schema_path.read_text(encoding="utf-8")

    existing_pages = _read_existing_pages(wiki_dir)

    prompt = _build_weekly_rollup_prompt(
        schema=schema_content,
        daily_notes=daily_notes,
        existing_pages=existing_pages,
    )

    response = _call_claude(prompt)

    if response:
        written = _apply_wiki_updates(wiki_dir, response)
        _append_log_entry(
            wiki_dir,
            end_date,
            kind="weekly-rollup",
            summary=_summarize_written(written),
        )
        print(f"[Retra] Weekly rollup compiled")
    else:
        print(f"[Retra] Weekly rollup failed")


def lint_wiki():
    """Run a health check on the wiki."""
    wiki_dir = _get_wiki_dir()

    schema_path = wiki_dir / "CLAUDE.md"
    if not schema_path.exists():
        print("[Retra] Wiki not initialized.")
        return

    schema_content = schema_path.read_text(encoding="utf-8")
    existing_pages = _read_existing_pages(wiki_dir)

    prompt = _build_lint_prompt(schema=schema_content, existing_pages=existing_pages)

    response = _call_claude(prompt)

    if response:
        # Print findings (text before any file blocks)
        file_start = response.find("===FILE:")
        if file_start > 0:
            print(response[:file_start].strip())
        elif "===FILE:" not in response:
            print(response)

        _apply_wiki_updates(wiki_dir, response)
        print(f"[Retra] Wiki lint complete")
    else:
        print(f"[Retra] Wiki lint failed")


def query_wiki(question: str):
    """Ask a question against the wiki."""
    wiki_dir = _get_wiki_dir()

    schema_path = wiki_dir / "CLAUDE.md"
    if not schema_path.exists():
        print("[Retra] Wiki not initialized.")
        return

    schema_content = schema_path.read_text(encoding="utf-8")
    existing_pages = _read_existing_pages(wiki_dir)

    pages_block = ""
    for path, content in existing_pages.items():
        pages_block += f"\n--- FILE: {path} ---\n{content}\n"

    # Include the last 30 daily notes so the LLM can answer date-specific
    # questions ("yesterday", "last Tuesday") with session-level detail.
    # The wiki gives synthesis; the daily notes are the source of truth.
    daylens_dir = _get_daylens_dir()
    today = date.today()
    daily_notes_block = ""
    if daylens_dir.exists():
        for i in range(30):
            d = today - timedelta(days=i)
            note_path = daylens_dir / f"{d.isoformat()}.md"
            if note_path.exists():
                daily_notes_block += (
                    f"\n--- DAILY NOTE: {d.isoformat()} ---\n"
                    f"{note_path.read_text(encoding='utf-8')}\n"
                )

    prompt = f"""You are the Retra Wiki assistant. Answer the user's question
using the wiki and the recent daily notes.

Today's date is {today.isoformat()}. Use this when interpreting relative dates
like "yesterday", "last week", or "today".

<schema>
{schema_content}
</schema>

<wiki_pages>
{pages_block}
</wiki_pages>

<recent_daily_notes>
{daily_notes_block}
</recent_daily_notes>

<question>
{question}
</question>

Answer concisely. The wiki pages are higher-level synthesis; the daily notes
are the granular source of truth for specific dates — prefer the daily notes
when the question asks about a specific day or recent activity, and use the
wiki for trends, projects, and patterns. Cite specific dates and numbers
where possible. If a daily note for the asked-about date is missing from the
recent_daily_notes block, say so explicitly rather than guessing. If the
answer would make a good reusable insight, end your response with the
insight file in the ===FILE=== format so it gets saved.

OUTPUT FORMAT for saved insights:
===FILE: insights/suggested-filename.md===
(full file content)
===END_FILE==="""

    response = _call_claude(prompt)

    if response:
        # Print the answer (text before any file blocks)
        file_start = response.find("===FILE:")
        if file_start > 0:
            print(response[:file_start].strip())
        elif "===FILE:" not in response:
            print(response)

        # Save any insight files
        if "===FILE:" in response:
            _apply_wiki_updates(wiki_dir, response)
            print("\n[Retra] Insight saved to wiki.")
    else:
        print("[Retra] Query failed")


# ── Internal helpers ──


def _read_existing_pages(wiki_dir: Path) -> dict[str, str]:
    """Read all existing wiki pages into a dict of {relative_path: content}."""
    pages = {}
    for subdir in ["projects", "patterns", "learning", "people", "rollups", "insights"]:
        dir_path = wiki_dir / subdir
        if dir_path.exists():
            for f in dir_path.glob("*.md"):
                rel = f"{subdir}/{f.name}"
                pages[rel] = f.read_text(encoding="utf-8")

    # Also read index and log
    for f in ["index.md", "log.md"]:
        fp = wiki_dir / f
        if fp.exists():
            pages[f] = fp.read_text(encoding="utf-8")

    return pages


def _build_ingest_prompt(
    schema: str,
    daily_note: str,
    daily_note_date: str,
    index: str,
    existing_pages: dict[str, str],
) -> str:
    """Build the prompt for daily note ingestion."""

    pages_block = ""
    for path, content in existing_pages.items():
        if path in ("index.md", "log.md"):
            continue
        pages_block += f"\n--- FILE: {path} ---\n{content}\n"

    # Build a clean list of existing project slugs so Claude can prefer reuse.
    existing_project_slugs = sorted(
        Path(p).stem for p in existing_pages if p.startswith("projects/")
    )
    existing_people_slugs = sorted(
        Path(p).stem for p in existing_pages if p.startswith("people/")
    )

    project_index = "\n".join(f"  - {s}" for s in existing_project_slugs) or "  (none yet)"
    people_index = "\n".join(f"  - {s}" for s in existing_people_slugs) or "  (none yet)"

    return f"""You are the Retra Wiki compiler. Your job is to update the wiki based on a new daily activity note.

<schema>
{schema}
</schema>

<existing_project_slugs>
{project_index}
</existing_project_slugs>

<existing_people_slugs>
{people_index}
</existing_people_slugs>

<existing_wiki_pages>
{pages_block}
</existing_wiki_pages>

<new_daily_note date="{daily_note_date}">
{daily_note}
</new_daily_note>

TASK: Run the INGEST operation as defined in the schema.

# Hard rules — violating these creates wiki noise

1. **DO NOT create a project page for any of the following:**
   - Linear / Jira ticket numbers (bare integers, CHA-XXXX, etc.)
   - Single English words pulled from window titles ("Free", "Watch", "Mario",
     "The", "Travel", "Your", "Consume", "Headless", "Slack", "Plans", "Deal",
     "Recall", "Team", "Upload", "Validate", "Onboarding", "Professional", etc.)
   - YouTube video titles, page titles, or browser tab titles
   - App names (Slack, Brave Browser, Cursor are NOT projects)
   - Domains (netmirror, youtube, linkedin are NOT projects)
2. **A project = a real codebase, repo, workstream, or named initiative.** If
   you can't point to a clear repo name, file path, or named effort, do not
   create a page.
3. **Reuse existing project pages.** Before creating ANY new project page,
   check `<existing_project_slugs>` above. If the workstream maps to an
   existing slug (or one of its `aliases:` in frontmatter), UPDATE that page
   — do not create a new one. Examples of merges:
     - "chatty-cortex", "Chatty-Insights/chatty-cortex", "chatty-cortex.git"
       all map to the existing `chatty-insights` page.
     - "presentation-generator", "presentation_generator" → same page.
4. **Project filenames MUST be lowercase-kebab-case** (e.g. `analysis-service.md`,
   `chatty-cortex.md`). Never `AI.md`, `Free.md`, `101.md`.
5. **People go in `people/`, not `projects/`.** A name like "Matteo", "Tejas",
   "Louis", "Mario" is a person — create or update `people/<name>.md`, not
   `projects/<Name>.md`.
6. **Update existing pages incrementally — do not regenerate verbose
   filler.** Append a single dated session entry; keep the existing structure.
   Do not produce sections like "Key Features", "Strategic Content Creation",
   "Activity Pattern" filled with repetitive prose for a 1-minute session.
7. **Always emit `index.md` and `log.md`** — even if only minor. (The Python
   wrapper will regenerate index.md from the filesystem and append log.md
   itself if you omit them, but emit your best attempt.)

OUTPUT FORMAT:
For each file you want to create or update, output it exactly like this:

===FILE: path/to/file.md===
(full file content here)
===END_FILE===

Output COMPLETE content for each file (not diffs). Only output files that
actually changed or are new. Keep updates concise."""


def _build_weekly_rollup_prompt(
    schema: str,
    daily_notes: dict[str, str],
    existing_pages: dict[str, str],
) -> str:
    """Build the prompt for weekly rollup."""

    notes_block = ""
    for date_str, content in sorted(daily_notes.items()):
        notes_block += f"\n--- DAILY NOTE: {date_str} ---\n{content}\n"

    pages_block = ""
    for path, content in existing_pages.items():
        pages_block += f"\n--- FILE: {path} ---\n{content}\n"

    return f"""You are the Retra Wiki compiler. Generate a weekly rollup.

<schema>
{schema}
</schema>

<daily_notes_for_week>
{notes_block}
</daily_notes_for_week>

<existing_wiki_pages>
{pages_block}
</existing_wiki_pages>

TASK: Run the WEEKLY ROLLUP operation as defined in the schema.

OUTPUT FORMAT:
===FILE: path/to/file.md===
(full file content)
===END_FILE===

Output the weekly rollup file and any other pages that need updating.
Always include updated index.md and log.md."""


def _build_lint_prompt(schema: str, existing_pages: dict[str, str]) -> str:
    """Build the prompt for wiki linting."""

    pages_block = ""
    for path, content in existing_pages.items():
        pages_block += f"\n--- FILE: {path} ---\n{content}\n"

    return f"""You are the Retra Wiki compiler. Run a health check on the wiki.

<schema>
{schema}
</schema>

<all_wiki_pages>
{pages_block}
</all_wiki_pages>

TASK: Run the LINT operation as defined in the schema.

Check for: stale pages, orphan pages, missing cross-references, inconsistencies,
missing pages that should exist, data gaps.

First output a summary of findings as plain text.
Then output any fixed or new files.

OUTPUT FORMAT for file updates:
===FILE: path/to/file.md===
(full file content)
===END_FILE==="""


def _call_claude(prompt: str) -> Optional[str]:
    """Call Claude API via streaming and return the assembled response text.

    Streaming keeps the socket active during long generations so the read
    timeout doesn't fire mid-response.
    """
    settings = get_settings()

    api_key = settings.ai.claude_api_key
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        print("[Retra] ANTHROPIC_API_KEY not set")
        pipeline_status.note_error("ANTHROPIC_API_KEY not set")
        return None

    payload = json.dumps({
        "model": settings.ai.claude_model,
        # Adaptive thinking shares this budget with the page rewrites.
        "max_tokens": 32000,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Accept": "text/event-stream",
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers=headers,
        method="POST",
    )

    chunks: list[str] = []
    stop_reason = None
    try:
        with urllib.request.urlopen(req, timeout=600, context=_ssl_context) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        chunks.append(delta.get("text", ""))
                elif etype == "message_delta":
                    stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
                elif etype == "error":
                    err = event.get("error") or {}
                    print(f"[Retra] Claude API stream error: {err.get('message', err)}")
                    pipeline_status.note_error(f"Claude API: {err.get('message', err)}")
                    return None
    except Exception as e:
        print(f"[Retra] Claude API error: {e}")
        pipeline_status.note_error(f"Claude API: {e}")
        return None

    if stop_reason in ("max_tokens", "refusal"):
        # A cut-off response would write half-finished wiki pages.
        print(f"[Retra] Claude response unusable (stop_reason={stop_reason})")
        pipeline_status.note_error(f"Claude API: stop_reason={stop_reason}")
        return None

    text = "".join(chunks)
    return text or None


def _apply_wiki_updates(wiki_dir: Path, response: str) -> list[str]:
    """Parse the LLM response and write files. Returns list of relpaths written.

    Skips index.md / log.md — those are regenerated deterministically by
    _rebuild_index (called here, so every caller keeps the index current) and
    _append_log_entry, so we never trust the LLM's version.
    Also skips files inside _quarantine/ to avoid resurrecting junk pages.
    """
    pattern = r'===FILE:\s*(.+?)===\s*\n(.*?)===END_FILE==='
    matches = re.findall(pattern, response, re.DOTALL)

    written: list[str] = []
    for filepath, content in matches:
        filepath = filepath.strip()
        content = content.strip()

        # Skip the two files we maintain ourselves
        if filepath in ("index.md", "log.md"):
            continue
        # Defense against the LLM resurrecting quarantined pages
        if filepath.startswith("_quarantine/"):
            continue

        # Block writes to filenames that violate the schema's lowercase-kebab
        # rule (for projects/). Junk like "projects/Free.md" or "projects/101.md".
        if filepath.startswith("projects/") and not _is_clean_project_filename(filepath):
            print(f"  Rejected (bad slug): {filepath}")
            continue

        full_path = wiki_dir / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content + "\n", encoding="utf-8")
        written.append(filepath)
        print(f"  Updated: {filepath}")

    _rebuild_index(wiki_dir)
    return written


# ── Deterministic index.md + log.md maintenance ──────────────────────────


_CATEGORY_DIRS = ("projects", "patterns", "learning", "people", "rollups", "insights")


def _is_clean_project_filename(relpath: str) -> bool:
    """Reject obvious junk filenames before we ever write them.

    Allowed: lowercase letters, digits, hyphen, underscore, dot. Must contain
    at least one letter, must not be a bare integer (Linear ticket #), and
    must not match a known stopword.
    """
    name = Path(relpath).stem
    if not name:
        return False
    if name.lower() != name:
        return False
    if name.isdigit():
        return False
    # Stopwords mirror summarizer._SKIP_PROJECTS (kept small here on purpose)
    stop = {
        "free", "love", "your", "the", "watch", "travel", "consume", "mario",
        "headless", "id", "simple", "slack", "deal", "plans", "replay",
        "team", "upload", "validate", "recall", "professional", "onboarding",
        "ai", "ml", "dav", "tejas", "louis", "nvidia", "externa", "external",
        "open-source",
    }
    if name in stop:
        return False
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", name):
        return False
    return True


def _read_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter as a flat dict (string values only)."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fm = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _rebuild_index(wiki_dir: Path):
    """Regenerate index.md from filesystem state. Source of truth = files."""
    lines = [
        "---",
        "title: Retra Wiki Index",
        f"updated: {date.today().isoformat()}",
        "note: This file is auto-generated after each ingest. Do not edit.",
        "---",
        "",
        "# Retra Wiki — Index",
        "",
        "Generated from the wiki's filesystem state. Updated on every ingest.",
        "",
    ]

    for subdir in _CATEGORY_DIRS:
        dir_path = wiki_dir / subdir
        if not dir_path.exists():
            continue
        entries = []
        for f in sorted(dir_path.glob("*.md")):
            fm = _read_frontmatter(f)
            title = fm.get("title", f.stem)
            updated = fm.get("updated", "")
            tail = f" — updated {updated}" if updated else ""
            entries.append(f"- [[{subdir}/{f.stem}|{title}]]{tail}")
        if entries:
            lines.append(f"## {subdir.capitalize()}")
            lines.append("")
            lines.extend(entries)
            lines.append("")

    (wiki_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _summarize_written(written: list[str]) -> str:
    """Build a one-line summary of what an ingest changed, for log.md."""
    if not written:
        return "no changes"
    by_dir: dict[str, list[str]] = {}
    for p in written:
        parts = p.split("/", 1)
        if len(parts) == 2:
            by_dir.setdefault(parts[0], []).append(Path(parts[1]).stem)
        else:
            by_dir.setdefault("(root)", []).append(Path(p).stem)
    chunks = []
    for d in _CATEGORY_DIRS:
        if d in by_dir:
            names = ", ".join(sorted(by_dir[d]))
            chunks.append(f"{d}: {names}")
    return "; ".join(chunks) if chunks else "no changes"


def _append_log_entry(wiki_dir: Path, when: date, kind: str, summary: str):
    """Append a log entry to log.md. Creates the file if missing."""
    log_path = wiki_dir / "log.md"
    header = (
        "---\n"
        "title: Retra Wiki Log\n"
        "note: Append-only log of compile operations. Maintained by Python.\n"
        "---\n\n"
        "# Retra Wiki — Log\n\n"
    )
    if not log_path.exists():
        log_path.write_text(header, encoding="utf-8")

    stamp = datetime.now().strftime("%H:%M")
    entry = (
        f"## [{when.isoformat()} {stamp}] {kind}\n\n"
        f"{summary}\n\n"
    )
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
