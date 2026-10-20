"""Structured log row vocabulary, formatting and bounded query windows."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

from .. import paths

#: The levels a row can carry, quietest first. Deliberately lowercase strings
#: rather than `logging`'s ints: the file is read by a human and by a frontend
#: `<select>`, and neither wants to know that WARNING is 30. `_RANK` is the
#: comparison the threshold and the `?level=` filter both use.
LEVELS = ("debug", "info", "warning", "error", "critical")
_RANK = {name: i for i, name in enumerate(LEVELS)}

#: The levels that may be CHOSEN as a threshold, which is not the same list.
#: `critical` is a level a row can carry and never one the floor may sit at:
#: `store.errors` reads this file's ERROR rows back (#156), the error store is
#: the only record of a failure that was never a call, and a setting that could
#: switch it off would make "errors are recorded whatever this says" -- which
#: the size backstop already promises, and which Configuration says in
#: as many words -- a lie. `apply_level` clamps rather than rejects, so a
#: config file edited by hand to `critical` still records errors.
FLOORS = LEVELS[:LEVELS.index("error") + 1]

#: `logging`'s numeric levels, mapped down to ours. A `LogRecord` may carry any
#: int -- a custom level, or one of the odd values between the named ones -- so
#: this is resolved by threshold rather than by lookup: anything at or above
#: CRITICAL is critical, anything below DEBUG is still debug.
_FROM_LOGGING = ((logging.CRITICAL, "critical"), (logging.ERROR, "error"),
                 (logging.WARNING, "warning"), (logging.INFO, "info"))

#: The logger the handler attaches to. Everything under it is grimoire's own
#: code; everything outside it is somebody else's, and stays out of the file --
#: see the module docstring on why that is a privacy rule and not a preference.
ROOT_LOGGER = "grimoire"

#: What a row may say, at most. A log line is not a payload store: one runaway
#: message -- a whole prompt, a base64 image, a provider's HTML error page --
#: would otherwise put megabytes on a single line and make the file unreadable
#: by the very filter this feature is. Truncation is marked, never silent.
MAX_MESSAGE = 2000
MAX_TRACE = 4000

#: The point past which a month file stops accepting anything but errors. See
#: the module docstring: the threshold is the real control and this is the
#: backstop under it. Checked against a cached size rather than an `os.stat`
#: per row -- `_written` counts the bytes this process has appended since the
#: last stat, and a re-stat costs one syscall per `_STAT_EVERY` bytes.
MAX_MONTH_BYTES = 128 * 1024 * 1024
_STAT_EVERY = 1 * 1024 * 1024

#: The widest window a read may ask for, so `?days=100000` cannot walk every
#: month a library has ever had. Same ceiling as the usage ledger's.
MAX_DAYS = 366

#: The window a read covers when the caller names no dates. Bounded on purpose:
#: an absent `since` used to mean "no lower bound", so the default `GET /logs`
#: opened every month file a library had ever written -- a read whose cost grew
#: with the age of the install, on the one page somebody opens *because*
#: something is wrong. Matching the usage rollup's default so the two halves of
#: the stats page describe the same span unless asked otherwise.
DEFAULT_DAYS = 30

#: Rows one read may return. A filtered log view is a page, not an export.
MAX_LIMIT = 2000
DEFAULT_LIMIT = 200

#: Bytes one `tail` poll may consume. The tail is bounded by bytes rather than
#: by rows for the reason `tail` argues at length: a row cap and a byte cursor
#: cannot both be honoured, and the cap is the half that loses data. 256 KiB is
#: a thousand-odd rows a second, well past any real write rate, and whatever
#: exceeds it is still there for the next poll.
MAX_TAIL_BYTES = 256 * 1024

#: How far the tail will scan for the end of a line that is longer than its
#: budget. Nothing this module writes comes close -- `MAX_MESSAGE` plus a
#: handful of fields is a few kilobytes -- so a line past the budget is
#: corrupt, and the only question is how much is read to get past it. Reading
#: to its newline unbounded (a bare `readline`) could materialize most of a
#: month file, which is the allocation the byte budget exists to prevent.
MAX_LINE_BYTES = 4 * 1024 * 1024

#: The smallest window a tail read will use, whatever budget is left. Any row
#: this module writes fits comfortably inside it (`MAX_MESSAGE` plus
#: `MAX_TRACE` plus fields, even with every character escaped), which is what
#: lets "no newline in a full window" mean "this line is corrupt" rather than
#: "this poll was nearly out of budget" -- a distinction that decides whether
#: an ordinary row is read or stepped over and lost.
MIN_LINE_READ = 64 * 1024

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now() -> str:
    """Now, UTC, to the millisecond.

    Millisecond precision rather than the usage ledger's whole seconds because
    these rows arrive in bursts: a single request can log a dozen times, and a
    reader sorting by `ts` would otherwise be sorting a tie. The format stays
    lexicographically ordered and prefix-compatible with a `YYYY-MM-DD` filter,
    which is what lets `since` be a plain string comparison.
    """
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"


def log_dir() -> Path:
    return paths.home() / "logs"


def month_path(ts: str) -> Path:
    """The file a row stamped ``ts`` belongs in.

    A timestamp whose month is unreadable lands in the current month rather
    than in a file named after the junk -- the same rule `store.usage` applies,
    and for the same reason: a row is worth keeping even when its clock is not.
    """
    month = ts[:7]
    return log_dir() / f"{month if _MONTH.match(month) else _now()[:7]}.jsonl"


def level_name(value: object) -> str:
    """``value`` as one of `LEVELS`, or "info" for anything else."""
    if isinstance(value, str) and value.lower() in _RANK:
        return value.lower()
    return "info"


def _module_name(name: object) -> str:
    """A logger name as a row's ``module``.

    `grimoire.store.replay` reads as `store.replay`: the package prefix is on
    every single row, so carrying it costs bytes and a filter dropdown full of
    the same nine characters. A name from outside the package (an explicit
    `record` call naming its own subsystem, like "dossier") is kept whole.
    """
    text = str(name or "app")
    if text == ROOT_LOGGER:
        return "app"
    return text.removeprefix(ROOT_LOGGER + ".")


def _clip(text: str, limit: int, head: bool = True) -> str:
    """``text`` cut to ``limit``, marked where it was cut.

    ``head=False`` keeps the END, which is what a traceback needs -- the frames
    nearest the failure are the last ones, and a traceback clipped from the
    front is a stack of callers with the exception missing.
    """
    if len(text) <= limit:
        return text
    return (text[:limit] + " …[clipped]") if head else ("…[clipped] " + text[-limit:])


def _level_of(number: int) -> str:
    """A `logging` level number as one of `LEVELS`. See `_FROM_LOGGING`."""
    for floor, name in _FROM_LOGGING:
        if number >= floor:
            return name
    return "debug"


# ---- reading ----
def _valid_day(text: object) -> str:
    """``text`` as ``YYYY-MM-DD``, or "" if it is not one.

    `object` rather than `str` because every caller is ultimately a query
    string: the shape is whatever arrived over the wire, and narrowing it here
    is cheaper than a validator at each route that would answer a mistyped date
    with a 422 instead of a window.
    """
    text = str(text or "")
    if not _DAY.match(text):
        return ""
    try:
        date.fromisoformat(text)
    except ValueError:
        return ""
    return text


def span(since: object, until: object, days: int) -> tuple[str, str]:
    """The ``[since, until]`` pair a read covers, always bounded.

    Public because `store.errors` takes the same window vocabulary over the
    same file, and two readers resolving "which window is this" separately is
    how one of them ends up scanning an install's whole history -- which is
    precisely the bug this helper was extracted to fix.

    An unparseable or absent ``until`` is today; an unparseable or absent
    ``since`` is ``days`` back from it. A reversed pair is read rather than
    refused -- this backs a date control, and swapping two fields should draw
    the window, not a 422.

    Never wider than `MAX_DAYS`, however it was asked for. The bound is on the
    query rather than on trust: this is one HTTP request doing unbounded file
    reads otherwise, and a date typed into a field is as able to ask for the
    whole history as an absent one was.
    """
    end = _valid_day(until) or time.strftime("%Y-%m-%d", time.gmtime())
    start = _valid_day(since)
    if start and start > end:
        start, end = end, start
    span = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    floor_day = _back(end, MAX_DAYS - 1)
    if not start:
        start = _back(end, span - 1)
    # The ceiling applies to a NAMED `since` as well. Clamping only the derived
    # one left `?since=1970-01-01` reading every month file the install ever
    # wrote -- exactly the unbounded scan `DEFAULT_DAYS` was added to remove,
    # reachable by typing a date.
    return max(start, floor_day), end


def _back(day: str, days: int) -> str:
    """``days`` before ``day``, stopping at the earliest date there is.

    `_valid_day` accepts anything `date.fromisoformat` parses, and
    `until=0001-01-01` is a perfectly valid date -- so subtracting a window
    from it crossed `date.min` and raised `OverflowError`, which reached the
    caller as a 500 rather than as the bounded, empty report these endpoints
    promise. Clamping is the answer that keeps the promise: there is no
    history before the first day, so the window simply starts there.
    """
    try:
        return (date.fromisoformat(day) - timedelta(days=days)).isoformat()
    except (OverflowError, ValueError):
        return date.min.isoformat()


def _shaped(raw: str) -> dict | None:
    """One line as a row whose fields are the types every reader assumes, or
    None to skip it.

    Parsing is not enough. A hand-edited or half-synced line can be a perfectly
    good JSON object carrying `"level": 9`, `"module": null`,
    `"message": {...}` -- and every one of those reaches the browser, where
    `r.level.toUpperCase()` takes the whole page down. "A bad line costs that
    line" has to mean the SHAPE too, or the promise only covers the syntax.
    """
    line = raw.strip()
    if not line:
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    row["ts"] = _text(row.get("ts"))
    row["level"] = level_name(row.get("level"))
    row["module"] = _text(row.get("module")) or "app"
    row["message"] = _text(row.get("message"))
    for key in ("kind", "campaign", "scene", "task", "trace"):
        if key in row:
            text = _text(row[key])
            if text:
                row[key] = text
            else:
                del row[key]
    return row


def _text(value: object) -> str:
    """A field as the string a reader assumes it is; "" for anything else.

    Deliberately NOT `str(value)`: rendering `{'a': 1}` as a message is
    presenting corruption as content. An unusable field reads as absent.
    """
    return value if isinstance(value, str) else ""
