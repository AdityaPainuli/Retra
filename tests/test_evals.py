"""wiki-eval keeps only claims quoted verbatim from a page and judges them
against that day's records and the subject's latest activity; agent-eval
runs each scenario with and without Retra and scores both (roadmap D4)."""

import json
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
    out = wiki_eval.eval_page("projects/retra.md", PAGE, fake_llm([extracted, "no json"]),
                              wiki_eval.Records(records), TODAY, 8)
    assert out["claims"][0]["verdict"] == "unverified"


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
