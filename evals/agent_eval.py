"""
Does Retra make an agent answer better? (roadmap D4)

`python main.py agent-eval` asks Claude Code (`claude -p`) each scenario in
data/evals/scenarios.json twice: once with only Retra's MCP server and once
with no MCP server. Both arms get only Read, Glob and Grep and start in the
home folder, so the only difference is Retra. A judge scores each answer
0 to 2 against the scenario's gold answer. The report (score, tokens, cost,
time and Retra calls per arm) is saved under data/evals/.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from config.settings import PROJECT_ROOT
from evals.wiki_eval import EVAL_DIR

SCENARIOS_PATH = EVAL_DIR / "scenarios.json"
ARMS = ("retra", "none")
# The only built-in tools either arm has, so the agent can look around but
# never change a file. No Bash: even `git log --output=<file>` writes.
BASE_TOOLS = ["Read", "Glob", "Grep"]
# Turned off by name too, in case a user setting would allow them.
DENIED_TOOLS = ["Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task"]
RUN_TIMEOUT = 600
TEMPLATE = [
    {"id": "left-off", "question": "Where did I leave off on <project> as of <YYYY-MM-DD>?", "gold": ""},
    {"id": "error", "question": "Find the error I saw on <YYYY-MM-DD> while working on <project>.", "gold": ""},
    {"id": "decision", "question": "What did I decide about <topic> in the week of <YYYY-MM-DD>?", "gold": ""},
    {"id": "standup", "question": "Write my standup for <YYYY-MM-DD>: what I did the day before and what's next.",
     "gold": ""},
]

Runner = Callable[[str, str, Path], dict]
Llm = Callable[[str], Optional[str]]


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    """The scenarios with a question and a gold answer. Writes a template
    (and returns []) when the file doesn't exist yet."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(TEMPLATE, indent=2) + "\n")
        return []
    scenarios = json.loads(path.read_text())
    return [s for s in scenarios if s.get("question") and s.get("gold") and "<" not in s["question"]]


def mcp_config(arm: str, usage_path: Path) -> dict:
    if arm == "none":
        return {"mcpServers": {}}
    from mcp_server.install import server_entry
    # The same entry `install claude` writes (with its interpreter fallback).
    # Eval calls go to their own log, so they don't count as real use.
    entry = server_entry("claude", PROJECT_ROOT)
    return {"mcpServers": {"retra": {**entry, "env": {**entry.get("env", {}), "RETRA_USAGE_PATH": str(usage_path)}}}}


def claude_command(question: str, config_path: Path, arm: str, model: str) -> list[str]:
    allowed = BASE_TOOLS + (["mcp__retra"] if arm == "retra" else [])
    return ["claude", "-p", question, "--output-format", "json", "--model", model,
            "--strict-mcp-config", "--mcp-config", str(config_path),
            "--tools", ",".join(BASE_TOOLS), "--disallowedTools", *DENIED_TOOLS,
            "--allowedTools", *allowed, "--no-session-persistence"]


def run_claude(question: str, arm: str, workdir: Path, model: str = "sonnet") -> dict:
    """One `claude -p` run. Returns the answer, tokens, cost, time and how
    many Retra tools it called."""
    usage_path = workdir / f"usage-{arm}.jsonl"
    usage_path.unlink(missing_ok=True)
    config_path = workdir / f"mcp-{arm}.json"
    config_path.write_text(json.dumps(mcp_config(arm, usage_path)))
    started = datetime.now()
    try:
        proc = subprocess.run(claude_command(question, config_path, arm, model), capture_output=True,
                              text=True, timeout=RUN_TIMEOUT, cwd=Path.home())
        out = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
        error = None if out else (proc.stderr.strip()[-500:] or f"exit {proc.returncode}")
    except subprocess.TimeoutExpired:
        out, error = {}, f"timed out after {RUN_TIMEOUT}s"
    usage = out.get("usage") or {}
    calls = []
    if usage_path.exists():
        for raw in usage_path.read_text().splitlines():
            try:
                c = json.loads(raw)
            except json.JSONDecodeError:
                continue
            args = ", ".join(f"{k}={v!r}" for k, v in (c.get("args") or {}).items())
            calls.append(f"{c.get('tool')}({args})" + ("" if c.get("ok", True) else " failed"))
    return {
        "answer": out.get("result") or "",
        "error": error or (out.get("result") if out.get("is_error") else None),
        "input_tokens": sum(usage.get(k) or 0 for k in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "output_tokens": usage.get("output_tokens") or 0,
        "cost_usd": out.get("total_cost_usd") or 0.0,
        "seconds": round((out.get("duration_ms") or (datetime.now() - started).total_seconds() * 1000) / 1000, 1),
        "turns": out.get("num_turns") or 0,
        "retra_calls": len(calls),
        "retra_tools": calls,
    }


def judge_prompt(question: str, gold: str, answer: str) -> str:
    return f"""Score an assistant's answer about the user's own past work against a reference answer the user wrote.

<question>{question}</question>
<reference>{gold}</reference>
<answer>{answer or "(no answer)"}</answer>

2 = gets the reference's key facts right (projects, dates, decisions, next steps), nothing important wrong.
1 = partly right: some key facts, or right but vague, or one wrong detail.
0 = wrong, made up, a refusal, or says it can't know.
Extra correct detail beyond the reference is fine.

Reply with only JSON: {{"score": 0, "reason": "one short sentence"}}"""


def judge(llm: Llm, question: str, gold: str, answer: str) -> dict:
    text = llm(judge_prompt(question, gold, answer)) or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        v = {}
    score = v.get("score")
    if score not in (0, 1, 2):
        return {"score": None, "reason": "judge gave no score"}
    return {"score": score, "reason": v.get("reason") or ""}


def summarize(runs: list[dict]) -> dict:
    out = {}
    for arm in ARMS:
        rs = [r for r in runs if r["arm"] == arm]
        scored = [r["score"] for r in rs if r["score"] is not None]
        n = len(rs) or 1
        out[arm] = {
            "runs": len(rs),
            "score": round(sum(scored) / len(scored), 2) if scored else None,
            "max_score": 2,
            "tokens": round(sum(r["input_tokens"] + r["output_tokens"] for r in rs) / n),
            "cost_usd": round(sum(r["cost_usd"] for r in rs) / n, 4),
            "seconds": round(sum(r["seconds"] for r in rs) / n, 1),
            "retra_calls": round(sum(r["retra_calls"] for r in rs) / n, 1),
            "words": round(sum(len(r["answer"].split()) for r in rs) / n),
            "errors": sum(bool(r["error"]) for r in rs),
        }
    return out


def run(model: str = "sonnet", runner: Optional[Runner] = None, llm: Optional[Llm] = None,
        scenarios_path: Path = SCENARIOS_PATH, out_dir: Path = EVAL_DIR,
        today: Optional[date] = None, repeats: int = 1) -> tuple[Optional[dict], Optional[Path]]:
    """Each scenario `repeats` times per arm: one run's answer can flip, so
    compare averages over several."""
    from export.wiki_compiler import _call_claude

    scenarios = load_scenarios(scenarios_path)
    if not scenarios:
        return None, None
    llm = llm or (lambda prompt: _call_claude(prompt, record_errors=False))
    runner = runner or (lambda q, arm, wd: run_claude(q, arm, wd, model))
    today = today or date.today()

    runs = []
    with tempfile.TemporaryDirectory(prefix="retra-agent-eval-") as tmp:
        for s in scenarios:
            for n in range(1, max(1, repeats) + 1):
                for arm in ARMS:
                    print(f"[Retra] agent-eval: {s['id']} ({arm}, run {n})")
                    r = runner(s["question"], arm, Path(tmp))
                    r.update(judge(llm, s["question"], s["gold"], r["answer"]) if r["answer"]
                             else {"score": 0, "reason": "no answer"})
                    runs.append({"scenario": s["id"], "arm": arm, "run": n, **r})

    report = {"kind": "agent", "date": today.isoformat(), "at": datetime.now().isoformat(timespec="seconds"),
              "model": model, "repeats": max(1, repeats), "summary": summarize(runs), "runs": runs}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"agent-{today.isoformat()}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report, path


def table(report: dict, baseline: Optional[dict] = None) -> str:
    def fmt(s: dict) -> list[str]:
        score = "-" if s["score"] is None else f"{s['score']:.2f}/2"
        return [score, f"{s.get('words', 0)}", f"{s['tokens']:,}", f"${s['cost_usd']:.3f}", f"{s['seconds']:.0f}s",
                f"{s['retra_calls']:g}", str(s["errors"])]

    head = ["arm", "score", "words/answer", "tokens/run", "cost/run", "time/run", "retra calls", "errors"]
    rows = [[arm] + fmt(report["summary"][arm]) for arm in ARMS]
    if baseline:
        rows += [[f"{arm} ({baseline['date']})"] + fmt(baseline["summary"][arm]) for arm in ARMS]
    widths = [max(len(r[i]) for r in [head] + rows) for i in range(len(head))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in [head] + rows]
    per = [f"  {r['scenario']:<10} {r['arm']:<6} #{r.get('run', 1)} "
           f"{r['score'] if r['score'] is not None else '-'}  {r['reason']}" for r in report["runs"]]
    return "\n".join(lines + ["", "Per run:"] + per)
