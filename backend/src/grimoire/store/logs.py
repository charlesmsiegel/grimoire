"""The structured debug log: what the backend did, in a file a filter can read
(#155).

Fifteen modules call `logging.getLogger(__name__)` and log to nobody. Nothing
in `main.create_app` ever attached a handler, so `logging`'s last-resort
handler prints WARNING and above to stderr and drops everything below it --
which in a packaged desktop build or an Android APK is a stream no user ever
sees. `store.failsoft` is the clearest cost: it exists *specifically* to make
three silent fallbacks traceable ("failure adds content, and content from a
stranger"), and its whole output went to a closed pipe.

This module is the destination those calls were always missing, and the durable
half of two more features: `store.errors` reads its ERROR rows back
(#156) and `store.metrics` counts them (#154).

Layout, home-scoped rather than campaign-scoped::

    <home>/logs/YYYY-MM.jsonl      one JSON object per line, append-only

Home-scoped for the same reason the usage ledger is: most of what gets logged
belongs to no campaign at all -- a migration, a backup, a corrupt bootstrap
pointer -- and a per-campaign log would either drop those or invent a campaign
to hold them. A row that *does* know its campaign says so in a field.

**Month files, not `RotatingFileHandler`.** #155 proposed the stdlib rotating
handler and this deliberately does not use it, because #156 aggregates errors
over a 30-day window out of these same rows: size-based rotation deletes the
oldest file whenever the newest fills, so a chatty afternoon at DEBUG would
silently destroy the error history the other feature exists to report. Rotation
throws away by *volume*; a window query needs to throw away by *age*. Month
files also match what the rest of the store already does (`store.usage`), so
one idiom covers both ledgers and `_window_files` is the same shape in each.

Volume is controlled at the source instead, by the level threshold -- the knob
`logging` has always had for exactly this -- and DEBUG is off by default.
`MAX_MONTH_BYTES` is the backstop under that, for the failure the threshold
cannot cover: a retry storm at WARNING. Past the cap the month stops taking new
rows *except* errors, which are what #156 needs and are rare enough to be
affordable; one `log_capped` row records that it happened, so a short log is
never mistaken for a quiet one.

Three properties this file must have, each learned from a specific way the
obvious version breaks:

- **Nothing here raises.** `record` runs beside a turn, inside exception
  handlers, and from a `logging.Handler` that is itself invoked from arbitrary
  code. A bookkeeping row that can fail the operation it is describing is a
  worse bug than the one it was written to diagnose -- the judgement
  `store.usage.record` and `store.prompt_log.record` already make.

- **It cannot re-enter itself.** Writing a row resolves the store root, and
  `paths.home()` reads the bootstrap pointer through `store.failsoft`, which
  *logs* when that file is corrupt. Handler → `record` → `home` → `failsoft` →
  handler is an unbounded recursion that only fires on a store that is already
  broken, i.e. exactly when the log matters most. `_busy` is a thread-local
  latch that answers the nested call with silence instead.

- **Only grimoire's own loggers are bridged.** Attaching to the root logger
  would pull in `httpx`, which logs full request URLs at DEBUG -- and an
  OpenAI-compatible endpoint carries its key in the URL for some providers, and
  in headers `urllib3` will happily render for others. This file is one a user
  is asked to attach to a bug report. It may not contain their API key, so the
  handler goes on the `grimoire` logger and third-party output stays where it
  was.

Retention is deliberately none, the same call `store.usage` makes: the files
are month-scoped, plain text and trivially deletable by hand, which beats a
pruner that destroys history somebody turned this on to have.
"""

from __future__ import annotations

import heapq
import json
import logging
import re
import threading
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

from . import atomic, config, paths

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

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The threshold, as a `_RANK` index. Module state rather than a config read
#: per row: `record` is on the path of everything the app does, and a file read
#: per log line is a cost the feature does not get to impose. `apply_level`
#: is what moves it -- called at install and again whenever the config is
#: written, so a change in the UI takes effect on the next row without anything
#: here having to poll or cache with a TTL.
#:
#: A one-key dict rather than a bare name so `apply_level` mutates rather than
#: rebinds: a `global` here would also mean a test that imported the name
#: by value held a copy that never moved.
_floor = {"rank": _RANK["info"]}

#: Re-entrancy latch; see the module docstring. Thread-local because the guard
#: is per call stack -- a second thread logging concurrently is not a recursion
#: and must not be silenced.
_busy = threading.local()

#: Bytes appended by this process since the last size check, per month path.
_written: dict[str, int] = {}
_capped: set[str] = set()
_size_guard = threading.Lock()


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


def apply_level(name: str = "") -> str:
    """Set the threshold, from ``name`` or from the stored config.

    Returns the level in force, which can be quieter than ``name`` asked for:
    the floor is clamped at `error` so the error store cannot be switched off
    (see `FLOORS`).
    """
    # Clamped at `error`, never above it -- see `FLOORS`. `level()` then
    # reports the floor actually in force rather than the one that was asked
    # for, so nothing downstream has to know the clamp happened.
    level = level_name(name or _stored_level())
    _floor["rank"] = min(_RANK[level], _RANK["error"])
    level = LEVELS[_floor["rank"]]
    logger = logging.getLogger(ROOT_LOGGER)
    # Two different levels, deliberately.
    #
    # The LOGGER's level decides what is emitted at all, and lowering it is the
    # only way DEBUG and INFO ever reach a handler -- `logging`'s root defaults
    # to WARNING. But raising it past WARNING would make a *storage* setting
    # silently take grimoire's warnings off a developer's terminal, which
    # `logging.lastResort` has always printed there and which has nothing to do
    # with what this file keeps. So the logger never goes quieter than WARNING.
    #
    # The HANDLER's level is the floor for the file, and it is the one that
    # actually enforces the setting. `record` checks `_floor` for the same
    # reason, since a direct call never passes through a handler at all.
    logger.setLevel(min(getattr(logging, level.upper()), logging.WARNING))
    for handler in logger.handlers:
        if isinstance(handler, Handler):
            handler.setLevel(getattr(logging, level.upper()))
    return level


def _stored_level() -> str:
    """The configured level, or "info" if there is nothing to read it from.

    **Must not create the store**, which is why this checks for the file rather
    than just calling `config.log_level()`: that goes through `read_config`,
    which calls `ensure_home` and materializes a default `config.md`. `install`
    runs from `create_app`, and grimoire's rule is that nothing exists on disk
    until the first API call that needs it -- the installers end by *printing*
    where the store will land (`python -m grimoire.where`), which is a promise
    that building the app has not already put it there.

    Guarded as well, because a store whose config is unreadable must still
    boot: logging comes up at its default, which is the state in which the
    reason is most likely to get written down.
    """
    try:
        if not (paths.home() / "config.md").exists():
            return "info"
        return config.log_level()
    except (OSError, ValueError):
        return "info"


def level() -> str:
    """The threshold currently in force."""
    return LEVELS[_floor["rank"]]


def record(level_: str, module: str, message: str, *, kind: str = "",
           campaign: str = "", scene: str = "", task: str = "",
           trace: str = "", ts: str | None = None, **fields: object) -> dict | None:
    """Append one row. Returns it, or None if nothing was written.

    **Never raises**, and never re-enters itself -- both are load-bearing and
    both are argued in the module docstring. Every return of None is one of the
    reasons named there: below the threshold, already inside a record, the month
    is capped, or the write itself failed.

    Extra ``fields`` ride along as row keys. They are filtered to what JSON can
    hold rather than coerced, because a value this module cannot serialize is
    a caller's mistake and must cost that key, not the row.
    """
    if getattr(_busy, "on", False):
        return None                      # nested: see `_busy`
    name = level_name(level_)
    if _RANK[name] < _floor["rank"]:
        return None
    _busy.on = True
    try:
        # The whole body is inside the guard, the row's construction included:
        # `str()` on an object with a raising `__str__` fails exactly as the
        # write can, and this function's contract is that it cannot fail its
        # caller. Building the row above the `try` would leave that hole -- the
        # one `store.usage.record` documents having had.
        row: dict = {"ts": ts or _now(), "level": name,
                     "module": _module_name(module), "message": _clip(message, MAX_MESSAGE)}
        for key, value in (("kind", kind), ("campaign", campaign),
                           ("scene", scene), ("task", task)):
            if value:
                row[key] = str(value)
        # `extra` rather than reusing `value` above: the loop before this one
        # binds it to a `str`, and a second loop rebinding the same name to an
        # `object` is an assignment mypy is right to refuse.
        for name_, extra in fields.items():
            if isinstance(extra, str):
                row[name_] = _clip(extra, MAX_MESSAGE)
            elif extra is None or isinstance(extra, (bool, int, float)):
                row[name_] = extra
        if trace:
            row["trace"] = _clip(trace, MAX_TRACE, head=False)
        return _append(row)
    except (OSError, TypeError, ValueError):
        # OSError is the write. TypeError is a field `json.dumps` refuses --
        # the sort of thing only a test finds, and the sort that would take
        # down a turn. ValueError covers `allow_nan` and the coercions above.
        return None
    finally:
        _busy.on = False


def _append(row: dict) -> dict | None:
    """Write one row to its month file, honouring `MAX_MONTH_BYTES`.

    Raises -- the caller is inside the guard `record` documents, and sharing
    this is what keeps a second writer from acquiring different failure
    semantics.
    """
    path = month_path(row["ts"])
    line = json.dumps(row, allow_nan=False)
    if not _room_for(path, len(line) + 1, row["level"]):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.append_line(path, line)
    return row


def _room_for(path: Path, size: int, level_: str) -> bool:
    """May a row of ``size`` bytes be appended to ``path``?

    Errors are always allowed through: they are what `store.errors` aggregates,
    they are rare, and a cap that silences the failure it was installed to
    survive would be the wrong trade in every direction. Everything else stops
    at the ceiling, once, with a `log_capped` row to say so -- a truncated log
    that does not admit it reads as a quiet one.
    """
    key = str(path)
    with _size_guard:
        if key in _capped:
            return _RANK[level_] >= _RANK["error"]
        counted = _written.get(key)
        if counted is None or counted >= _STAT_EVERY:
            try:
                actual = path.stat().st_size
            except OSError:
                actual = 0              # not there yet, or unreadable: assume room
            _written[key] = 0
        else:
            actual = None
        _written[key] = _written.get(key, 0) + size
        if actual is None or actual + _written[key] < MAX_MONTH_BYTES:
            return True
        _capped.add(key)
    # Outside the lock: this is itself an append, and `_capped` already holds
    # so it cannot recurse into another cap check for the same file.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.append_line(path, json.dumps(
            {"ts": _now(), "level": "warning", "module": "store.logs",
             "message": f"log file reached {MAX_MONTH_BYTES} bytes; "
                        "only errors will be recorded for the rest of the month",
             "kind": "log_capped"}))
    except OSError:
        pass
    return _RANK[level_] >= _RANK["error"]


def forget_file_sizes() -> None:
    """Forget every cached size, and every month currently marked capped.

    Public because `PUT /config/data-dir` needs it: `_room_for` caches by
    absolute path, so a store repointed at a new root leaves byte counts (and
    possibly a cap) charged against files the new tree does not have -- which
    would silence a fresh log at the old one's size. `install` calls it for the
    same reason, one app at a time.
    """
    with _size_guard:
        _written.clear()
        _capped.clear()


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


class Handler(logging.Handler):
    """The bridge from stdlib `logging` into this store.

    Everything the package already logs -- `failsoft`'s corrupt-file warnings,
    `migrations`, `runner`, `llm`'s retries -- arrives here without a single
    call site changing, which is the entire reason to bridge rather than to
    rewrite fifteen modules into `logs.record`.

    `emit` cannot raise: `logging` would route the failure to
    `handleError`/stderr, and a handler that reports its own failures on the
    path of a request that was merely trying to log is the noise this feature
    is supposed to remove. `record` already swallows everything it can; this
    catches what happens *before* it -- `getMessage()` runs the caller's own
    `%` formatting and `__str__`, either of which can raise on a bad log call
    somewhere else entirely.
    """

    def emit(self, log_record: logging.LogRecord) -> None:
        try:
            kind, trace = "", ""
            if log_record.exc_info and log_record.exc_info[0] is not None:
                kind = log_record.exc_info[0].__name__
                trace = "".join(traceback.format_exception(*log_record.exc_info))
            record(_level_of(log_record.levelno), log_record.name,
                   log_record.getMessage(), kind=kind, trace=trace)
        except Exception:  # noqa: BLE001 - a handler that raises is worse than a lost row
            pass


def _level_of(number: int) -> str:
    """A `logging` level number as one of `LEVELS`. See `_FROM_LOGGING`."""
    for floor, name in _FROM_LOGGING:
        if number >= floor:
            return name
    return "debug"


def install() -> str:
    """Attach the handler to the `grimoire` logger. Returns the level in force.

    Idempotent, and that matters more than it looks: the test suite builds an
    app per test against a fresh `GRIMOIRE_HOME`, so a handler left behind by
    the previous app would still be attached and every row would be written
    twice. Removing our own kind first makes install-per-app the same as
    install-once.

    `propagate` is left alone. Nothing configures the root logger, so
    propagation reaches `logging.lastResort` -- WARNING and above on stderr --
    which is what a developer running `uvicorn` in a terminal expects to keep
    seeing.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in [h for h in logger.handlers if isinstance(h, Handler)]:
        logger.removeHandler(handler)
    logger.addHandler(Handler())
    forget_file_sizes()
    return apply_level()


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


def window(days: int) -> tuple[str, str]:
    """The ``[since, until]`` day pair for a window of ``days`` ending today."""
    span = max(1, min(int(days or 1), MAX_DAYS))
    today = date.fromisoformat(time.strftime("%Y-%m-%d", time.gmtime()))
    return (today - timedelta(days=span - 1)).isoformat(), today.isoformat()


def _span(since: object, until: object, days: int) -> tuple[str, str]:
    """The ``[since, until]`` pair a read covers, always bounded.

    An unparseable or absent ``until`` is today; an unparseable or absent
    ``since`` is ``days`` back from it. A reversed pair is read rather than
    refused -- this backs a date control, and swapping two fields should draw
    the window, not a 422.
    """
    end = _valid_day(until) or time.strftime("%Y-%m-%d", time.gmtime())
    start = _valid_day(since)
    if not start:
        span = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
        start = (date.fromisoformat(end) - timedelta(days=span - 1)).isoformat()
    if start > end:
        start, end = end, start
    return start, end


def _window_files(since: str, until: str) -> list[Path]:
    """The month files overlapping ``[since, until]``, oldest first.

    Listed by globbing rather than by generating month names, so a file whose
    month is outside the window but whose *rows* are not -- a clock that moved,
    a store copied between machines -- is still considered; the row filter is
    what decides. The name is used only to skip files that cannot possibly
    overlap.
    """
    out = []
    for path in sorted(log_dir().glob("*.jsonl")):
        month = path.stem
        if not _MONTH.match(month) or month < since[:7] or month > until[:7]:
            continue
        out.append(path)
    return out


def _matches(row: dict, level_: str, module: str, since: str, until: str,
             contains: str, campaign: str) -> bool:
    """Does ``row`` pass every filter? Absent filters do not filter."""
    if _RANK.get(level_name(row.get("level")), 0) < _RANK.get(level_, 0):
        return False
    if module and str(row.get("module", "")) != module:
        return False
    if campaign and str(row.get("campaign", "")) != campaign:
        return False
    ts = str(row.get("ts", ""))
    if since and ts < since:
        return False
    # `until` is a DAY, and a row stamped 14:02 that day sorts after the bare
    # date -- so the comparison is against the day's end, not the day.
    if until and ts > until + "T99":
        return False
    return not (contains and contains.lower() not in _haystack(row))


def _haystack(row: dict) -> str:
    """The text `?q=` searches: the message, plus the two labels a reader is
    most likely to be hunting by name (a module they can also pick from the
    dropdown, and an error kind that has no dropdown of its own)."""
    return " ".join(str(row.get(k, "")) for k in ("message", "module", "kind")).lower()


def read(*, level: str = "debug", module: str = "", since: str = "", until: str = "",
         contains: str = "", campaign: str = "", days: int = DEFAULT_DAYS,
         limit: int = DEFAULT_LIMIT) -> dict:
    """Filtered rows, **newest first**, with the filter vocabulary beside them.

    Newest first because that is the only order a log view is ever read in, and
    doing it here rather than in the client is what lets ``limit`` mean "the
    most recent N" instead of "the first N, then throw most of them away".

    Ordered by the rows' own ``ts``, and NOT by their position in the file.
    Those are usually the same -- rows are appended as they happen -- and the
    version that assumed it was visibly wrong the moment anything wrote a row
    out of order: a page rendered 08-21, 08-18, 08-19, 08-20, 08-21 while
    calling itself newest-first. Two writers on a synced store with skewed
    clocks do that, so does a hand-edited file, and an ordering claim that
    holds only while nothing unusual has happened is not one a reader can use.
    The heap keeps it to ``limit`` rows of memory rather than sorting the
    window.

    Tolerant in both directions, exactly as the usage ledger's reader is: a file
    that cannot be opened is skipped rather than failing the view, and a line
    that will not parse is skipped rather than failing the file --
    `atomic.append_line` documents the one way a torn line happens.

    ``modules`` and ``counts`` describe the *window*, not the page: a dropdown
    that only offered the modules present in the newest 200 rows would lose an
    option every time something else got chatty.

    The window is always bounded -- ``days`` back from ``until`` when no
    ``since`` is given, never "everything there has ever been"; see
    `DEFAULT_DAYS`.
    """
    floor = level_name(level)
    since, until = _span(since, until, days)
    cap = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    modules: set[str] = set()
    counts = dict.fromkeys(LEVELS, 0)
    newest = Newest(cap)
    for path in _window_files(since, until):
        for row in _lines(path):
            if not _matches(row, floor, module, since, until, contains, campaign):
                continue
            modules.add(str(row.get("module", "")))
            counts[level_name(row.get("level"))] += 1
            newest.offer(row)
    return {"rows": newest.rows(), "modules": sorted(m for m in modules if m),
            "counts": counts, "total": sum(counts.values()),
            "truncated": newest.dropped, "level": floor,
            "since": since, "until": until, "levels": list(LEVELS)}


def scan(*, level: str = "debug", module: str = "", since: str = "", until: str = "",
         contains: str = "", campaign: str = "", days: int = DEFAULT_DAYS):
    """Every matching row in the window, **oldest first**, unbounded.

    `read` is the paged view a human looks at; this is the one an aggregate is
    computed over -- `store.errors` grouping by module, `store.metrics`
    counting per day. They cannot share `read`'s return, because a rollup that
    only saw the newest `limit` rows would under-count exactly when there was
    most to count.

    A generator rather than a list: the caller decides what to hold, and every
    caller today holds a counter rather than the rows.

    Bounded exactly as `read` is, and through the same helper -- two readers
    resolving "which window is this" separately is how one of them ends up
    scanning an install's whole history.
    """
    floor = level_name(level)
    since, until = _span(since, until, days)
    for path in _window_files(since, until):
        for row in _lines(path):
            if _matches(row, floor, module, since, until, contains, campaign):
                yield row


class Newest:
    """The ``cap`` rows with the largest ``ts``, newest first.

    Public because `store.errors` pages over the same rows and has to order
    them the same way; two readers with their own idea of "newest" is how one
    of them ends up disagreeing with the other about the same file.

    A bounded min-heap rather than "collect and sort": a month of DEBUG rows is
    the case this has to survive, and holding all of them to hand back two
    hundred is the read that turns a dashboard request into a memory event.
    Only the smallest kept timestamp is ever compared against, so the cost is
    one comparison per row and `cap` rows of memory.

    Ties are broken by arrival, and they are common -- rows are stamped to the
    millisecond and a burst produces several inside one. Without the counter
    `heapq` would fall through to comparing the row DICTS, which raises.
    """

    def __init__(self, cap: int):
        self.cap = cap
        self.dropped = False
        self._seq = 0
        self._heap: list[tuple[str, int, dict]] = []

    def offer(self, row: dict) -> None:
        self._seq += 1
        item = (str(row.get("ts", "")), self._seq, row)
        if len(self._heap) < self.cap:
            heapq.heappush(self._heap, item)
        elif item > self._heap[0]:
            heapq.heapreplace(self._heap, item)
            self.dropped = True
        else:
            self.dropped = True

    def rows(self) -> list[dict]:
        return [row for _, _, row in sorted(self._heap, reverse=True)]


def _lines(path: Path) -> list[dict]:
    """Every well-formed row in ``path``, oldest first. See `read` on why an
    unreadable file is empty rather than an exception."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


# ---- live tailing ----
def cursor() -> str:
    """A cursor at the current end of the log: "tail from here, nothing before".

    Spelled ``<month>.jsonl:<offset>``. A byte offset into an append-only file
    is the one cursor that cannot miss a row or repeat one -- a timestamp
    cursor does both, since rows share a millisecond and a row can be appended
    with a ts already passed.
    """
    paths_ = _window_files("0000-00", "9999-99")
    if not paths_:
        return f"{_now()[:7]}.jsonl:0"
    newest = paths_[-1]
    try:
        return f"{newest.name}:{newest.stat().st_size}"
    except OSError:
        return f"{newest.name}:0"


def tail(from_cursor: str = "", budget: int = MAX_TAIL_BYTES, **filters) -> dict:
    """Rows appended since ``from_cursor``, **oldest first**, plus the next one.

    Oldest first, unlike `read`: these are appended to a list the reader is
    already holding, so they arrive in the order they happened.

    **Bounded by bytes read, not by rows returned**, and that distinction is
    the whole correctness argument. A row cap cannot work here: the cursor is a
    byte offset, so returning "the first N rows" of a chunk means either
    advancing the offset past the rows that did not fit -- losing them
    permanently and silently -- or leaving the offset behind and re-sending
    everything that did fit. The first is what this originally did, and it lost
    seven of ten rows the moment more arrived between two polls than the cap
    allowed, which is precisely when somebody is watching. Reading a bounded
    number of BYTES has no such split: the offset advances by exactly what was
    parsed, every row inside it comes back, and whatever is past the budget is
    still there for the next poll a second later.

    Filtering happens after that boundary, so a filtered tail consumes the log
    at the same rate an unfiltered one does and cannot fall behind.

    Three more things this has to survive, all of which happen in practice:

    - **The month rolling over.** At midnight on the 1st the cursor names a
      file that has stopped growing. When it is exhausted and a newer file
      exists, the cursor moves to the start of the newer one -- so a tail open
      across midnight keeps working instead of going permanently silent.
    - **A short read at the end.** A row is published by one `O_APPEND` write,
      but a reader can still arrive mid-write on a long line, so the offset
      only ever advances to the last complete newline. The partial line is
      re-read whole on the next poll.
    - **The file shrinking.** A store repointed at a different root, or a month
      file deleted by hand, leaves an offset past the end. Rather than seeking
      into nothing, the cursor resets to the start of that file -- which
      re-sends rows at worst, where trusting the offset would send garbage.
    """
    name, _, raw = from_cursor.partition(":")
    try:
        offset = max(0, int(raw))
    except ValueError:
        offset = 0
    files = _window_files("0000-00", "9999-99")
    if not files:
        return {"rows": [], "cursor": from_cursor or cursor(), "more": False}
    names = [p.name for p in files]
    if name not in names:
        # An unknown (or absent) cursor starts at the newest file's end rather
        # than replaying the month: a client opening a tail asked for what
        # happens NEXT, and `read` is how it gets the backlog.
        return {"rows": [], "cursor": cursor(), "more": False}
    left = max(1, min(int(budget or MAX_TAIL_BYTES), MAX_TAIL_BYTES))
    rows: list[dict] = []
    index = names.index(name)
    while True:
        chunk, offset, used = _read_from(files[index], offset, left)
        rows.extend(row for row in chunk if _tail_matches(row, filters))
        left -= used
        if left <= 0:
            break                          # budget spent; the rest waits a second
        # Only move on once the current file is EXHAUSTED. Advancing while
        # bytes remain in it is the same row-loss the docstring describes,
        # wearing a different shape.
        if index + 1 < len(files) and _at_end(files[index], offset):
            index, offset = index + 1, 0   # month rolled over
            continue
        break
    return {"rows": rows, "cursor": f"{files[index].name}:{offset}",
            "more": not _at_end(files[index], offset) or index + 1 < len(files)}


def _at_end(path: Path, offset: int) -> bool:
    """Is ``offset`` at (or past) the end of ``path``? An unreadable file is
    treated as exhausted -- there is nothing more to take from it."""
    try:
        return offset >= path.stat().st_size
    except OSError:
        return True


def _tail_matches(row: dict, filters: dict) -> bool:
    """`_matches` with the tail's defaults: no window, since a tail is by
    definition about now."""
    return _matches(row, level_name(filters.get("level") or "debug"),
                    str(filters.get("module") or ""), "", "",
                    str(filters.get("contains") or ""),
                    str(filters.get("campaign") or ""))


def _read_from(path: Path, offset: int, budget: int) -> tuple[list[dict], int, int]:
    """Complete rows in ``path`` after ``offset``, the new offset, and the bytes
    consumed.

    At most ``budget`` bytes are read, cut back to the last complete newline
    inside it -- so the offset returned always names a row boundary and never
    lands inside a line. A single line longer than the budget would otherwise
    wedge the tail forever, so the budget is stretched to cover one whole line
    when that happens; `MAX_MESSAGE` bounds how long a line can be, and the
    alternative is a cursor that can never advance.
    """
    try:
        size = path.stat().st_size
        if offset > size:
            offset = 0                   # truncated or replaced; see `tail`
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(budget)
            end = data.rfind(b"\n")
            if end < 0 and len(data) >= budget:
                # A line longer than the budget: take the rest of it rather
                # than returning nothing forever.
                data += handle.readline()
                end = data.rfind(b"\n")
    except OSError:
        return [], offset, 0
    if end < 0:
        return [], offset, 0             # nothing complete yet
    whole = data[:end + 1]
    out = []
    for chunk in whole.decode("utf-8", errors="replace").splitlines():
        line = chunk.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out, offset + len(whole), len(whole)
