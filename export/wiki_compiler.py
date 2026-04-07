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
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

try:
    import certifi
    _ssl_context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _ssl_context = None

from config.settings import get_settings


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
        _apply_wiki_updates(wiki_dir, response)
        print(f"[Retra] Wiki compiled for {target_date}")

        # Auto weekly rollup on Sundays (weekday 6 = Sunday)
        if target_date.weekday() == 6:
            try:
                print(f"[Retra] Sunday detected — auto-generating weekly rollup...")
                compile_week(target_date)
            except Exception as e:
                print(f"[Retra] Auto weekly rollup failed: {e}")
    else:
        print(f"[Retra] Wiki compilation failed for {target_date}")


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
        _apply_wiki_updates(wiki_dir, response)
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

    prompt = f"""You are the Retra Wiki assistant. Answer the user's question
using the wiki data.

<schema>
{schema_content}
</schema>

<wiki_pages>
{pages_block}
</wiki_pages>

<question>
{question}
</question>

Answer concisely using data from the wiki. Cite specific dates and numbers
where possible. If the answer would make a good reusable insight, end your
response with the insight file in the ===FILE=== format so it gets saved.

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

    return f"""You are the Retra Wiki compiler. Your job is to update the wiki based on a new daily activity note.

<schema>
{schema}
</schema>

<current_index>
{index}
</current_index>

<existing_wiki_pages>
{pages_block}
</existing_wiki_pages>

<new_daily_note date="{daily_note_date}">
{daily_note}
</new_daily_note>

TASK: Run the INGEST operation as defined in the schema.

Read the daily note. Update or create project pages, pattern pages, learning pages, and people pages as needed. Update the index. Append to the log.

OUTPUT FORMAT:
For each file you want to create or update, output it exactly like this:

===FILE: path/to/file.md===
(full file content here)
===END_FILE===

Output ALL files that need updating, with their COMPLETE content (not diffs).
Always include the updated index.md and the log.md with the new entry appended.
Only output files that actually changed or are new."""


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
    """Call Claude API and return the response text."""
    settings = get_settings()

    api_key = settings.ai.claude_api_key
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        print("[Retra] ANTHROPIC_API_KEY not set")
        return None

    payload = json.dumps({
        "model": settings.ai.claude_model,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120, context=_ssl_context) as resp:
            data = json.loads(resp.read().decode())
            if data.get("content"):
                return data["content"][0]["text"]
    except Exception as e:
        print(f"[Retra] Claude API error: {e}")

    return None


def _apply_wiki_updates(wiki_dir: Path, response: str):
    """Parse the LLM response and write files."""
    # Extract all file blocks
    pattern = r'===FILE:\s*(.+?)===\s*\n(.*?)===END_FILE==='
    matches = re.findall(pattern, response, re.DOTALL)

    for filepath, content in matches:
        filepath = filepath.strip()
        content = content.strip()

        full_path = wiki_dir / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content + "\n", encoding="utf-8")
        print(f"  Updated: {filepath}")
