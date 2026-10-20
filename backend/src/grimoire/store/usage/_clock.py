"""One session start and validated calendar windows for ledger queries."""

from __future__ import annotations

import calendar
import time
from datetime import date, timedelta

#: Longest window ``summary`` will scan, in days. A rollup is one HTTP request
#: doing unbounded file reads, so the bound is on the query rather than on
#: trust: a year is past any question this view answers, and refusing more
#: keeps ``?days=100000`` from walking every month a library has ever had.
MAX_DAYS = 366

#: When this process started, as an ISO timestamp. "Session" has no server-side
#: concept in grimoire — there is no login, and a browser tab is not a session
#: the backend can see — so the endpoint documents it as *this backend process*,
#: which is the one boundary the server genuinely knows. Restarting the app
#: therefore resets the session bucket, while ``today`` and the 30-day window
#: carry straight over.
_SESSION_START = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today() -> str:
    """Today, UTC, as ``YYYY-MM-DD``. A function so tests can pin the window
    without freezing the clock for everything else in the process."""
    return time.strftime("%Y-%m-%d", time.gmtime())


def _valid_day(text: object) -> str:
    """``YYYY-MM-DD`` if that is what this is, else ``""``.

    Parsed rather than pattern-matched, for the reason `campaigns.read` gives
    about stamps: a regex accepts ``2026-13-45``, and `date.fromisoformat` on
    that is a ValueError one call further on, inside a report that must not
    fail because a scene's frontmatter was hand-edited.
    """
    if not isinstance(text, str):
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


#: How far back a scan reaches when the caller supplies no usable start date --
#: the same default window every other read here takes.
_DEFAULT_DAYS = 30


def _scan_since(since: object, until: str) -> str:
    """Where a scan asked to start at ``since`` actually starts.

    ``since`` is a caller's date — the scene's own ``created`` stamp, for the
    panel asking what one scene cost — so it decides the window, and the window
    is the one cost this module bounds. Three clamps, each for a real value a
    campaign.md or a scene's frontmatter can hold:

    - absent or unparseable falls back to `_DEFAULT_DAYS`, which is what every
      other read here defaults to;
    - further back than `MAX_DAYS` is clamped to it, so a scene created two
      years ago costs the same read as one created last month;
    - *after* today — a scene stamped in the future by a wrong clock or a hand
      edit — scans today alone rather than producing an empty window that
      `_window_files` would walk backwards.
    """
    day = _valid_day(since)
    floor = (date.fromisoformat(until) - timedelta(days=MAX_DAYS - 1)).isoformat()
    if not day:
        return (date.fromisoformat(until) - timedelta(days=_DEFAULT_DAYS - 1)).isoformat()
    return min(max(day, floor), until)


def month_bounds(month: str) -> tuple[str, str]:
    """The inclusive UTC dates for a strictly formatted calendar month."""
    if len(month) != 7 or month[4] != "-" or not (month[:4] + month[5:]).isdigit():
        raise ValueError("month must be YYYY-MM")
    try:
        first = date.fromisoformat(month + "-01")
    except ValueError as exc:
        raise ValueError("month must be YYYY-MM") from exc
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    return first.isoformat(), last.isoformat()
