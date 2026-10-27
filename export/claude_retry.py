"""
Retry with backoff for the nightly Claude calls (reflection + wiki).

The nightly job runs around 23:00 or right after the Mac wakes, when the
network is often flaky. Without a retry, one dropped connection meant a
missing reflection or wiki update for that day.
"""

import http.client
import socket
import time
import urllib.error
from typing import Callable, TypeVar

T = TypeVar("T")

# Seconds to wait before the 2nd and 3rd attempt.
RETRY_DELAYS = (15, 60)
MAX_RETRY_AFTER = 120

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
RETRYABLE_STREAM_ERRORS = {"overloaded_error", "api_error", "rate_limit_error"}


class RetryableStreamError(Exception):
    """An `error` event mid-stream that's worth another attempt (e.g. overloaded)."""


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code in RETRYABLE_STATUS
    return isinstance(e, (
        RetryableStreamError,
        urllib.error.URLError,   # DNS failure, connection refused, offline after wake
        ConnectionError,         # includes "Connection reset by peer"
        TimeoutError,            # includes "The read operation timed out"
        socket.timeout,
        http.client.IncompleteRead,
        http.client.RemoteDisconnected,
    ))


def _delay_for(e: Exception, default: int) -> int:
    if isinstance(e, urllib.error.HTTPError):
        retry_after = e.headers.get("retry-after") if e.headers else None
        if retry_after and retry_after.isdigit():
            return min(int(retry_after), MAX_RETRY_AFTER)
    return default


def call_with_retries(fn: Callable[[], T], label: str) -> T:
    """Run fn, retrying transient network/API failures. Re-raises the last error."""
    for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
        try:
            return fn()
        except Exception as e:
            if delay is None or not _is_retryable(e):
                raise
            wait = _delay_for(e, delay)
            print(f"[Retra] {label} attempt {attempt} failed ({e}); retrying in {wait}s")
            time.sleep(wait)
    raise AssertionError("unreachable")
