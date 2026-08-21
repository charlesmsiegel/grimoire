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

#: Rows one read may return. A filtered log view is a page, not an export.
MAX_LIMIT = 2000
DEFAULT_LIMIT = 200

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The threshold, as a `_RANK` index. Module state rather than a config read
#: per row: `record` is on the path of everything the app does, and a file read
#: per log line is a cost the feature does not get to impose. `apply_level`
#: is what moves it -- called at install and again whenever the config is
#: written, so a change in the UI takes effect on the next row without anything
#: here having to poll or cache with a TTL.
_threshold = _RANK["info"]

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

    Returns the level in force. Called by `install` and by the config route
    after a write, which is what keeps `record` free of a per-row config read
    (see `_threshold`).
    """
    global _threshold
    level = level_name(name or _stored_level())
    _threshold = _RANK[level]
    logging.getLogger(ROOT_LOGGER).setLevel(getattr(logging, level.upper()))
    for handler in logging.getLogger(ROOT_LOGGER).handlers:
        if isinstance(handler, Handler):
            handler.setLevel(getattr(logging, level.upper()))
    return level


def _stored_level() -> str:
    """The configured level, or "info" if the config cannot be read.

    Guarded because this runs during `create_app`, and a store whose config is
    unreadable must still boot -- with logging on at its default, which is the
    state in which the reason is most likely to get written down.
    """
    try:
        return config.log_level()
    except (OSError, ValueError):
        return "info"


def level() -> str:
    """The threshold currently in force."""
    return LEVELS[_threshold]


def enabled(level_: str) -> bool:
    """Would a row at ``level_`` be recorded? The check `record` makes, exposed
    for a caller that would have to *build* something expensive to log it."""
    return _RANK.get(level_name(level_), 0) >= _threshold


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
    if _RANK[name] < _threshold:
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
        for key, value in fields.items():
            if isinstance(value, str):
                row[key] = _clip(value, MAX_MESSAGE)
            elif value is None or isinstance(value, (bool, int, float)):
                row[key] = value
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
    prefix = ROOT_LOGGER + "."
    return text[len(prefix):] if text.startswith(prefix) else text


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
    """``text`` as ``YYYY-MM-DD``, or "" if it is not one. A `date` is accepted
    too, so a caller does not have to format one to ask a question with it."""
    if isinstance(text, date):
        return text.isoformat()
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
    if contains and contains.lower() not in _haystack(row):
        return False
    return True


def _haystack(row: dict) -> str:
    """The text `?q=` searches: the message, plus the two labels a reader is
    most likely to be hunting by name (a module they can also pick from the
    dropdown, and an error kind that has no dropdown of its own)."""
    return " ".join(str(row.get(k, "")) for k in ("message", "module", "kind")).lower()


def read(*, level: str = "debug", module: str = "", since: str = "", until: str = "",
         contains: str = "", campaign: str = "", limit: int = DEFAULT_LIMIT) -> dict:
    """Filtered rows, **newest first**, with the filter vocabulary beside them.

    Newest first because that is the only order a log view is ever read in, and
    doing it here rather than in the client is what lets ``limit`` mean "the
    most recent N" instead of "the first N, then throw most of them away".

    Tolerant in both directions, exactly as the usage ledger's reader is: a file
    that cannot be opened is skipped rather than failing the view, and a line
    that will not parse is skipped rather than failing the file --
    `atomic.append_line` documents the one way a torn line happens.

    ``modules`` and ``counts`` describe the *window*, not the page: a dropdown
    that only offered the modules present in the newest 200 rows would lose an
    option every time something else got chatty.
    """
    floor = level_name(level)
    since = _valid_day(since)
    until = _valid_day(until) or time.strftime("%Y-%m-%d", time.gmtime())
    if since and since > until:
        since, until = until, since
    cap = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    rows: list[dict] = []
    modules: set[str] = set()
    counts = dict.fromkeys(LEVELS, 0)
    truncated = False
    for path in reversed(_window_files(since, until)):
        for row in reversed(_lines(path)):
            if not _matches(row, floor, module, since, until, contains, campaign):
                continue
            modules.add(str(row.get("module", "")))
            counts[level_name(row.get("level"))] += 1
            if len(rows) < cap:
                rows.append(row)
            else:
                truncated = True
    return {"rows": rows, "modules": sorted(m for m in modules if m),
            "counts": counts, "total": sum(counts.values()),
            "truncated": truncated, "level": floor,
            "since": since, "until": until, "levels": list(LEVELS)}


def scan(*, level: str = "debug", module: str = "", since: str = "", until: str = "",
         contains: str = "", campaign: str = ""):
    """Every matching row in the window, **oldest first**, unbounded.

    `read` is the paged view a human looks at; this is the one an aggregate is
    computed over -- `store.errors` grouping by module, `store.metrics`
    counting per day. They cannot share `read`'s return, because a rollup that
    only saw the newest `limit` rows would under-count exactly when there was
    most to count.

    A generator rather than a list: the caller decides what to hold, and every
    caller today holds a counter rather than the rows.
    """
    floor = level_name(level)
    since = _valid_day(since)
    until = _valid_day(until) or time.strftime("%Y-%m-%d", time.gmtime())
    if since and since > until:
        since, until = until, since
    for path in _window_files(since, until):
        for row in _lines(path):
            if _matches(row, floor, module, since, until, contains, campaign):
                yield row


def _lines(path: Path) -> list[dict]:
    """Every well-formed row in ``path``, oldest first. See `read` on why an
    unreadable file is empty rather than an exception."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
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


def tail(from_cursor: str = "", limit: int = DEFAULT_LIMIT, **filters) -> dict:
    """Rows appended since ``from_cursor``, **oldest first**, plus the next one.

    Oldest first, unlike `read`: these are appended to a list the reader is
    already holding, so they arrive in the order they happened.

    Three things this has to survive, all of which happen in practice:

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
        return {"rows": [], "cursor": from_cursor or cursor()}
    names = [p.name for p in files]
    if name not in names:
        # An unknown (or absent) cursor starts at the newest file's end rather
        # than replaying the month: a client opening a tail asked for what
        # happens NEXT, and `read` is how it gets the backlog.
        return {"rows": [], "cursor": cursor()}
    cap = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    rows: list[dict] = []
    index = names.index(name)
    while index < len(files) and len(rows) < cap:
        path = files[index]
        chunk, offset = _read_from(path, offset)
        for row in chunk:
            if len(rows) >= cap:
                break
            if _tail_matches(row, filters):
                rows.append(row)
        if index + 1 < len(files):
            index, offset = index + 1, 0   # month rolled over
            continue
        break
    return {"rows": rows, "cursor": f"{files[index].name}:{offset}"}


def _tail_matches(row: dict, filters: dict) -> bool:
    """`_matches` with the tail's defaults: no window, since a tail is by
    definition about now."""
    return _matches(row, level_name(filters.get("level") or "debug"),
                    str(filters.get("module") or ""), "", "",
                    str(filters.get("contains") or ""),
                    str(filters.get("campaign") or ""))


def _read_from(path: Path, offset: int) -> tuple[list[dict], int]:
    """Complete rows in ``path`` after byte ``offset``, and the new offset."""
    try:
        size = path.stat().st_size
        if offset > size:
            offset = 0                   # truncated or replaced; see `tail`
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset                # nothing complete yet
    whole = data[:end + 1]
    out = []
    for raw in whole.decode("utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out, offset + len(whole)
