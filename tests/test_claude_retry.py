"""A refused API call reports the API's reason, so the pipeline status and
the menubar say "credit balance is too low" instead of "400 Bad Request"."""

import io
import json
import urllib.error

import pytest

from export import claude_retry
from export.claude_retry import ApiError, call_with_retries


def http_error(code, body):
    return urllib.error.HTTPError("https://api.anthropic.com/v1/messages", code, "Bad Request", {},
                                  io.BytesIO(body.encode()))


def test_refused_call_reports_the_apis_reason():
    body = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                   "message": "Your credit balance is too low."}})

    def fail():
        raise http_error(400, body)

    with pytest.raises(ApiError, match=r"^HTTP 400: Your credit balance is too low\.$"):
        call_with_retries(fail, "Test")


def test_unreadable_body_keeps_the_http_error():
    def fail():
        raise http_error(400, "<html>")

    with pytest.raises(urllib.error.HTTPError):
        call_with_retries(fail, "Test")


def test_retryable_errors_still_retry(monkeypatch):
    monkeypatch.setattr(claude_retry.time, "sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise http_error(529, '{"error": {"message": "Overloaded"}}')
        return "ok"

    assert call_with_retries(flaky, "Test") == "ok" and len(calls) == 3
