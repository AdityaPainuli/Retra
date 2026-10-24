"""
Project attribution.

Each WindowEvent may or may not carry a project_hint (set at capture time by
window_tracker.get_project_hint). The hints are sparse — terminal/editor
events tend to have them, but the user's surrounding browser tabs, Slack
checks, Linear scrolling etc. usually don't.

This module fills in the gaps with a "context inheritance" pass:

  Pass 1: every event whose hint is set anchors directly.

  Pass 2: every other event inherits the project of the *nearest anchor in
  time*, within ±CONTEXT_WINDOW_MIN. If no anchor is in range, the event
  stays unattributed.

The right intuition: a 5-min Linear detour while you were elbow-deep in
the `retra` repo belongs to retra, not to a category called "Browsing."
A 30-min YouTube break with no surrounding code work belongs to nothing.

The output is *per-event* attribution. Higher-level functions like
day_project_totals turn that into per-project seconds.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from storage.models import WindowEvent
from capture.window_tracker import project_hint_from_title_url


CONTEXT_WINDOW_MIN = 10           # ±10 min around an anchor counts as "in context"
HEARTBEAT_SEC = 60                # Database.HEARTBEAT_INTERVAL: tail past last confirmed activity
SAME_APP_MERGE_SEC = 600          # aggregate_sessions merges same-app gaps below this


def event_seconds(e: WindowEvent, nxt: Optional[WindowEvent]) -> int:
    """Seconds an event represents, using the same rules as aggregate_sessions
    so project totals reconcile with tracked time:
      - same app continues (not idle) within SAME_APP_MERGE_SEC: full gap
      - app switch, idle, long gap or end of data: at most HEARTBEAT_SEC
    """
    if nxt is None:
        return HEARTBEAT_SEC
    gap = max(0, int((nxt.timestamp - e.timestamp).total_seconds()))
    if not nxt.is_idle and nxt.app_name == e.app_name and gap < SAME_APP_MERGE_SEC:
        return gap
    return min(gap, HEARTBEAT_SEC)


def _event_anchor(e: WindowEvent) -> Optional[str]:
    """Effective project anchor for an event:
       - the stored project_hint, if any (captured live)
       - otherwise a post-hoc extraction from title/URL (no IPC)
    The post-hoc path matters for events captured before the project_hint
    column existed, AND for live events where the capture-time hint was
    None but the title/URL still encodes one.
    """
    if e.is_idle:
        return None
    if e.project_hint:
        return e.project_hint
    return project_hint_from_title_url(e.app_name, e.window_title, e.url)


def attribute_projects(
    events: Iterable[WindowEvent],
    context_window_min: int = CONTEXT_WINDOW_MIN,
) -> dict[int, Optional[str]]:
    """
    Return {event_id: project_slug | None} for non-idle events.

    Direct attribution (own anchor) takes precedence over inheritance.
    For events without an anchor, the *nearest* other-event anchor in time
    within `context_window_min` minutes is used.
    """
    events_list = sorted(
        (e for e in events if e.id is not None),
        key=lambda e: e.timestamp,
    )

    # Compute anchors once for everyone — these double as Pass 1 results
    # AND as the lookup source for Pass 2.
    event_anchor: dict[int, Optional[str]] = {}
    anchors: list[tuple[float, str]] = []
    for e in events_list:
        a = _event_anchor(e)
        event_anchor[e.id] = a
        if a is not None:
            anchors.append((e.timestamp.timestamp(), a))

    out: dict[int, Optional[str]] = {}
    window_sec = context_window_min * 60

    # Two-pointer scan over anchors for nearest-anchor lookup.
    lo = 0
    hi = 0
    for e in events_list:
        if e.is_idle:
            continue
        if event_anchor[e.id] is not None:
            out[e.id] = event_anchor[e.id]
            continue

        et = e.timestamp.timestamp()
        while lo < len(anchors) and anchors[lo][0] < et - window_sec:
            lo += 1
        while hi < len(anchors) and anchors[hi][0] <= et + window_sec:
            hi += 1

        nearest = None
        nearest_dist = float("inf")
        for i in range(lo, hi):
            ts, proj = anchors[i]
            d = abs(ts - et)
            if d < nearest_dist:
                nearest_dist = d
                nearest = proj

        out[e.id] = nearest

    return out


def day_project_totals(
    events: Iterable[WindowEvent],
    context_window_min: int = CONTEXT_WINDOW_MIN,
) -> dict[str, dict]:
    """
    Compute per-project seconds for a day, plus an unattributed bucket
    so totals stay honest.

    Walks events in time order; each event contributes event_seconds() to
    its attributed project.
    Idle events contribute nothing (they break attribution naturally).

    Returns:
      {
        "<project>": {
            "seconds": int,
            "direct_seconds": int,     # from project_hint anchors
            "inherited_seconds": int,  # from context inheritance
        },
        "_unattributed": {"seconds": int},
      }
    """
    events_list = sorted(
        (e for e in events if e.id is not None),
        key=lambda e: e.timestamp,
    )
    attribution = attribute_projects(events_list, context_window_min)
    # Recompute per-event anchors so we know which attributions were
    # "direct" (own signal) vs "inherited" (taken from a nearby event).
    own_anchor = {e.id: _event_anchor(e) for e in events_list}

    totals: dict[str, dict] = defaultdict(
        lambda: {"seconds": 0, "direct_seconds": 0, "inherited_seconds": 0}
    )
    unattributed_secs = 0

    for i, e in enumerate(events_list):
        if e.is_idle:
            continue
        nxt = events_list[i + 1] if i + 1 < len(events_list) else None
        secs = event_seconds(e, nxt)

        proj = attribution.get(e.id)
        if not proj:
            unattributed_secs += secs
            continue

        bucket = totals[proj]
        bucket["seconds"] += secs
        if own_anchor.get(e.id):
            bucket["direct_seconds"] += secs
        else:
            bucket["inherited_seconds"] += secs

    out = {p: dict(v) for p, v in totals.items()}
    out["_unattributed"] = {"seconds": unattributed_secs}
    return out
