"""PRs opened in a Claude Code session: only PRs the session created, read
from the `gh pr create` output, including commands moved to the background."""

import json

from capture.claude_sessions import parse_session

TS = "2026-11-06T10:00:00.000Z"
URL = "https://github.com/acme/app/pull/{}"


def _assistant(*tools):
    return {"type": "assistant", "timestamp": TS, "cwd": "/work/app",
            "message": {"content": [{"type": "tool_use", "id": i, "name": n, "input": a} for i, n, a in tools]}}


def _result(tool_id, text):
    return {"type": "user", "timestamp": TS, "cwd": "/work/app",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


def _prs(tmp_path, *entries):
    log = tmp_path / "s.jsonl"
    prompt = {"type": "user", "timestamp": TS, "cwd": "/work/app", "message": {"content": "open the PR"}}
    log.write_text("\n".join(json.dumps(e) for e in (prompt, *entries)) + "\n")
    session, _ = parse_session(log)
    return json.loads(session["prs"])


def test_pr_url_from_create_output(tmp_path):
    assert _prs(tmp_path,
        _assistant(("t1", "Bash", {"command": 'cd /work/app && gh pr create --title "X" --body "Y"'})),
        _result("t1", "Warning: 1 uncommitted change\n" + URL.format(1)),
    ) == [URL.format(1)]


def test_create_after_multiline_commit_message(tmp_path):
    cmd = 'git commit -m "Subject\n\nBody line" && git push && gh pr create --title "X" --body "$(cat <<\'EOF\'\nSee #3\nEOF\n)"'
    assert _prs(tmp_path, _assistant(("t1", "Bash", {"command": cmd})), _result("t1", URL.format(2))) == [URL.format(2)]


def test_labelled_urls_from_one_command(tmp_path):
    cmd = 'A=$(gh pr create --title "a" | tail -1); B=$(gh pr create --title "b" | tail -1); echo "admin: $A"; echo "web: $B"'
    out = f"admin: {URL.format(3)}\nweb: {URL.format(4)}"
    assert _prs(tmp_path, _assistant(("t1", "Bash", {"command": cmd})), _result("t1", out)) == [URL.format(3), URL.format(4)]


def test_command_that_only_mentions_gh_pr_create_is_ignored(tmp_path):
    # A script that edits PR detection has the phrase in a string, and its
    # output (a test run) lists many PRs the session didn't open.
    cmd = "python - <<'EOF'\nif name == \"Bash\" and \"gh pr create\" in command:\n    pass\nEOF"
    out = "\n".join(URL.format(n) for n in range(10, 15))
    assert _prs(tmp_path, _assistant(("t1", "Bash", {"command": cmd})), _result("t1", out)) == []


def test_other_pr_urls_in_create_output_are_ignored(tmp_path):
    out = f"Related: see {URL.format(20)} and {URL.format(21)} for context\n{URL.format(22)}"
    assert _prs(tmp_path,
        _assistant(("t1", "Bash", {"command": "gh pr create --fill"})), _result("t1", out),
    ) == [URL.format(22)]


def test_backgrounded_create_read_later(tmp_path):
    out_file = "/private/tmp/claude-501/x/tasks/b1.output"
    moved = ("Command was moved to the background (ID: b1) so that a message that arrived while it was "
             f"running can reach you. Output is being written to: {out_file}. You will be notified.")
    assert _prs(tmp_path,
        _assistant(("t1", "Bash", {"command": "git push && gh pr create --fill"})),
        _result("t1", moved),
        _assistant(("t2", "Bash", {"command": f"sleep 5; cat {out_file}"})),
        _result("t2", f"remote: https://github.com/acme/app/pull/new/branch\n{URL.format(30)}"),
        _assistant(("t3", "Read", {"file_path": out_file})),
        _result("t3", f"1\t{URL.format(30)}\n2\t"),
        _assistant(("t4", "Bash", {"command": "gh pr list"})),
        _result("t4", URL.format(31)),
    ) == [URL.format(30)]
