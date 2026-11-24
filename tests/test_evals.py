"""wiki-eval keeps only claims quoted verbatim from a page and judges them
against that day's records and the subject's latest activity; agent-eval
runs each scenario with and without Retra and scores both (roadmap D4)."""

import json

import pytest
from datetime import date

from evals import agent_eval, wiki_eval
from export.compile_input import Record

TODAY = date(2026, 11, 12)
PAGE = """---
title: Retra
aliases: [daylens]
---
# Retra

## Sessions

### 2026-11-10
Opened PR #14 about project names. (retra://session/s1)

## Notes
- Launch moved to Monday.
"""


def records(d: date) -> list[Record]:
    day = Record(f"retra://day/{d}", "day", f"stats for {d}")
    if d == date(2026, 11, 10):
        return [day, Record("retra://session/s1", "session", "daylens: opened PR #14 project names", ["daylens"]),
                Record("retra://url/9", "url", "youtube cooking video")]
    if d == date(2026, 11, 11):
        return [day, Record("retra://session/s2", "session", "retra: moved launch to Monday", ["retra"])]
    return [day]


def fake_llm(replies):
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        return replies.pop(0)
    llm.prompts = prompts
    return llm


def test_verbatim_ignores_whitespace_and_case_only():
    assert wiki_eval.verbatim("opened  PR #14\nabout project", PAGE)
    assert not wiki_eval.verbatim("Opened PR #15 about project names", PAGE)
    assert not wiki_eval.verbatim("PR", PAGE), "too short to prove anything"


def test_eval_page_drops_invented_quotes_and_judges_the_rest():
    extracted = json.dumps([
        {"claim": "PR 14 was opened on 2026-11-10", "quote": "Opened PR #14 about project names.",
         "dates": ["2026-11-10"], "current": False},
        {"claim": "Launch is on Monday", "quote": "Launch moved to Monday.", "dates": [], "current": True},
        {"claim": "Made up", "quote": "Retra shipped to the App Store", "dates": [], "current": True},
    ])
    verdicts = json.dumps([{"i": 0, "verdict": "supported", "reason": "session s1"},
                           {"i": 1, "verdict": "stale", "reason": "moved again"}])
    llm = fake_llm([extracted, verdicts])
    out = wiki_eval.eval_page("projects/retra.md", PAGE, llm, wiki_eval.Records(records), TODAY, 8)

    assert out["dropped_quotes"] == 1 and [c["verdict"] for c in out["claims"]] == ["supported", "stale"]
    evidence = llm.prompts[1]
    assert "--- 2026-11-10 ---" in evidence and "opened PR #14" in evidence, "the day the claim names"
    assert "--- 2026-11-11 ---" in evidence and "moved launch to Monday" in evidence, "latest activity"
    assert "youtube" not in evidence, "unrelated records are left out"


def test_missing_verdict_counts_as_unverified():
    extracted = json.dumps([{"claim": "Launch is on Monday", "quote": "Launch moved to Monday.",
                             "dates": [], "current": True}])
    out = wiki_eval.eval_page("projects/retra.md", PAGE, fake_llm([extracted, "[]"]),
                              wiki_eval.Records(records), TODAY, 8)
    assert out["claims"][0]["verdict"] == "unverified" and "error" not in out


@pytest.mark.parametrize("replies, error", [([None], "claim extraction failed"),
                                            (["not json"], "claim extraction failed"),
                                            (['[{"claim": "Launch is on Monday", "quote": "Launch moved to Monday.",'
                                              ' "dates": [], "current": true}]', None], "judging failed")])
def test_failed_calls_are_reported_not_scored(replies, error):
    out = wiki_eval.eval_page("projects/retra.md", PAGE, fake_llm(list(replies)), wiki_eval.Records(records), TODAY, 8)
    assert out["error"] == error
    s = wiki_eval.summarize([out])
    assert s["pages"] == 0 and s["failed_pages"] == ["projects/retra.md"] and s["claims"] == 0


def test_no_report_when_every_page_failed(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "retra.md").write_text(PAGE)
    with pytest.raises(RuntimeError, match="no report saved"):
        wiki_eval.run(pages=5, today=TODAY, llm=lambda p: None, records=wiki_eval.Records(records),
                      wiki_dir=wiki, out_dir=tmp_path / "evals")
    assert not (tmp_path / "evals").exists()


def test_latest_active_days_come_from_any_time(monkeypatch):
    calls = []
    recs = wiki_eval.Records(records, active_days=lambda names, today, n: calls.append(names) or [date(2026, 10, 1)])
    claims = [{"claim": "launch", "dates": [], "current": True}]
    evidence = wiki_eval.gather_evidence(claims, {"retra"}, recs, TODAY)
    assert "--- 2026-10-01 ---" in evidence and calls == [{"retra"}]


def test_summary_and_saved_report(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "retra.md").write_text(PAGE)
    (wiki / "index.md").write_text("# Index\n")
    extracted = json.dumps([
        {"claim": "a", "quote": "Opened PR #14 about project names.", "dates": [], "current": True},
        {"claim": "b", "quote": "Launch moved to Monday.", "dates": [], "current": True},
    ])
    verdicts = json.dumps([{"i": 0, "verdict": "supported"}, {"i": 1, "verdict": "stale"}])
    report, path = wiki_eval.run(pages=5, today=TODAY, llm=fake_llm([extracted, verdicts]),
                                 records=wiki_eval.Records(records), wiki_dir=wiki, out_dir=tmp_path / "evals")
    s = report["summary"]
    assert s["pages"] == 1 and s["claims"] == 2 and s["score"] == 0.5 and s["forgetting"] == 1
    assert json.loads(path.read_text())["summary"] == s
    assert "score (supported / current)" in wiki_eval.table(report, report)


def test_agent_arms_differ_only_by_retra(tmp_path):
    usage = tmp_path / "u.jsonl"
    assert agent_eval.mcp_config("none", usage) == {"mcpServers": {}}
    retra = agent_eval.mcp_config("retra", usage)["mcpServers"]["retra"]
    assert retra["env"] == {"RETRA_USAGE_PATH": str(usage)}
    with_r = agent_eval.claude_command("q", tmp_path / "c.json", "retra", "sonnet")
    without = agent_eval.claude_command("q", tmp_path / "c.json", "none", "sonnet")
    assert set(with_r) - set(without) == {"mcp__retra"}
    assert "--strict-mcp-config" in without


def test_missing_scenarios_writes_a_template(tmp_path):
    path = tmp_path / "scenarios.json"
    assert agent_eval.run(scenarios_path=path, out_dir=tmp_path) == (None, None)
    assert len(json.loads(path.read_text())) == 4
    assert agent_eval.run(scenarios_path=path, out_dir=tmp_path) == (None, None), "placeholders aren't runnable"


def test_agent_eval_scores_each_arm(tmp_path):
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps([{"id": "left-off", "question": "Where did I leave off on retra on 2026-11-11?",
                                 "gold": "Moving the launch to Monday."}]))

    def runner(question, arm, workdir):
        return {"answer": "Moved the launch to Monday." if arm == "retra" else "", "error": None,
                "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.01, "seconds": 3.0, "turns": 2,
                "retra_calls": 2 if arm == "retra" else 0}

    llm = fake_llm(['{"score": 2, "reason": "matches"}'])
    report, saved = agent_eval.run(runner=runner, llm=llm, scenarios_path=path, out_dir=tmp_path, today=TODAY)
    assert report["summary"]["retra"]["score"] == 2 and report["summary"]["none"]["score"] == 0
    assert report["summary"]["retra"]["retra_calls"] == 2
    assert len(llm.prompts) == 1, "an empty answer scores 0 without a judge call"
    assert saved.name == "agent-2026-11-12.json"
    assert "retra calls" in agent_eval.table(report)


def test_judge_rejects_scores_outside_the_scale():
    assert agent_eval.judge(lambda p: '{"score": 5}', "q", "g", "a")["score"] is None
    assert agent_eval.judge(lambda p: 'Sure: {"score": 1, "reason": "vague"}', "q", "g", "a")["score"] == 1


def test_every_named_day_keeps_its_records_and_reflection(monkeypatch):
    monkeypatch.setattr(wiki_eval, "MAX_EVIDENCE_CHARS", 3000)
    days = [date(2026, 11, d) for d in range(1, 8)]

    def busy(d):
        return [Record(f"retra://day/{d}", "reflection", f"AI reflection for {d}: " + "y" * 600 + " shipped retra"),
                *(Record(f"retra://screen/{d.day}{i}", "screen", "retra " + "x" * 2000) for i in range(5))]

    claims = [{"claim": "retra shipped", "dates": [d.isoformat() for d in days], "current": False}]
    evidence = wiki_eval.gather_evidence(claims, {"retra"}, wiki_eval.Records(busy), date(2026, 11, 7))
    for d in days:
        assert f"--- {d} ---" in evidence and f"AI reflection for {d}" in evidence
    assert evidence.count(" shipped retra") == 7, "a reflection is never cut"
    assert "left out" not in evidence and len(evidence) < 9000


def test_repeats_run_each_scenario_several_times(tmp_path):
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps([{"id": "a", "question": "q on 2026-11-11?", "gold": "g"}]))
    calls = []

    def runner(question, arm, workdir):
        calls.append(arm)
        return {"answer": "one two three", "error": None, "input_tokens": 1, "output_tokens": 1,
                "cost_usd": 0.0, "seconds": 1.0, "turns": 1, "retra_calls": 0}

    report, _ = agent_eval.run(runner=runner, llm=lambda p: '{"score": 1}', scenarios_path=path,
                               out_dir=tmp_path, today=TODAY, repeats=3)
    assert calls == ["retra", "none"] * 3 and [r["run"] for r in report["runs"]] == [1, 1, 2, 2, 3, 3]
    assert report["summary"]["retra"]["words"] == 3


def test_session_evidence_is_the_days_turns_only(retra):
    import os
    from datetime import timedelta
    from storage.database import Database

    db = Database(os.environ["RETRA_DB_PATH"])
    d = date.today() - timedelta(days=3)
    later = d + timedelta(days=2)
    db.save_agent_session({
        "session_id": "long", "source": "claude-code", "cwd": "/work/retra", "project": "retra", "repo": None,
        "branch": "main", "title": "Launch", "started_at": f"{d}T10:00:00", "ended_at": f"{later}T10:00:00",
        "prompt_count": 2, "files": "[]", "prs": "[]", "last_status": "Launch moved to Monday.",
        "last_reply": None, "log_path": "/l.jsonl", "log_size": 1, "log_mtime": 1.0,
    }, [(f"{d}T10:00:00", "prompt", "plan the launch"), (f"{d}T10:05:00", "recap", "Launch set for Friday."),
        (f"{later}T10:00:00", "recap", "Launch moved to Monday.")])
    [rec] = [r for r in wiki_eval._day_sessions(d) if r.ref == "retra://session/long"]
    assert "Launch set for Friday" in rec.text and "Monday" not in rec.text
    assert rec.text.index("where it stood") < rec.text.index("asked:"), "outcome before prompts"
    assert all(r.ref != "retra://session/long" for r in wiki_eval._day_sessions(d + timedelta(days=1)))


def test_one_blocked_line_drops_only_that_line_of_the_reflection(retra):
    import os
    import sqlite3

    d = date.today()
    conn = sqlite3.connect(os.environ["RETRA_DB_PATH"])
    conn.execute("INSERT OR REPLACE INTO daily_summaries (date, ai_summary) VALUES (?, ?)",
                 (d.isoformat(), "Shipped the evals.\nPaid the bank bill.\nPlanned D5."))
    conn.commit()
    conn.close()
    [refl] = [r for r in wiki_eval._with_reflection(d) if r.kind == "reflection"]
    assert "Shipped the evals." in refl.text and "Planned D5." in refl.text and "bank" not in refl.text


def test_agent_runs_have_only_read_only_tools(tmp_path):
    cmd = agent_eval.claude_command("q", tmp_path / "c.json", "retra", "sonnet")
    assert cmd[cmd.index("--tools") + 1] == "Read,Glob,Grep"
    denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--allowedTools")]
    assert {"Bash", "Edit", "Write"} <= set(denied)
    assert not any(t.startswith("Bash") for t in cmd[cmd.index("--allowedTools") + 1:])
