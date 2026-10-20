"""Bounded historical queries and byte-cursor live tail."""

from __future__ import annotations

import heapq
from pathlib import Path

from . import _rows


def _window_files(since: str, until: str) -> list[Path]:
    """The month files overlapping ``[since, until]``, oldest first.

    Listed by globbing rather than by generating month names, so a file whose
    month is outside the window but whose *rows* are not -- a clock that moved,
    a store copied between machines -- is still considered; the row filter is
    what decides. The name is used only to skip files that cannot possibly
    overlap.
    """
    out = []
    for path in sorted(_rows.log_dir().glob("*.jsonl")):
        month = path.stem
        if not _rows._MONTH.match(month) or month < since[:7] or month > until[:7]:
            continue
        out.append(path)
    return out


def _tail_files() -> list[Path]:
    """The files the live tail may sit in: everything up to the current month.

    A store synced from a peer whose clock is ahead already holds a
    future-dated month file, and it sorts newest. `cursor()` would start there
    and `tail` only ever moves toward later names, so the rows THIS device goes
    on writing -- into the real current month -- would never appear in Live at
    all. Bounded here rather than in `_window_files`, which backs the paged
    reader: reading a future month is fine, following one is not.
    """
    return _window_files("0000-00", _rows._now()[:7])


def _matches(row: dict, level_: str, module: str, since: str, until: str,
             contains: str, campaign: str) -> bool:
    """Does ``row`` pass every filter? Absent filters do not filter."""
    if _rows._RANK.get(_rows.level_name(row.get("level")), 0) < _rows._RANK.get(level_, 0):
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
         contains: str = "", campaign: str = "", days: int = _rows.DEFAULT_DAYS,
         limit: int = _rows.DEFAULT_LIMIT) -> dict:
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
    floor = _rows.level_name(level)
    since, until = _rows.span(since, until, days)
    cap = max(1, min(int(limit or _rows.DEFAULT_LIMIT), _rows.MAX_LIMIT))
    modules: set[str] = set()
    counts = dict.fromkeys(_rows.LEVELS, 0)
    newest = Newest(cap)
    for path in _window_files(since, until):
        for row in _lines(path):
            # `modules` is the WINDOW's vocabulary, which is what this claims
            # to be: collected with no level, module or text filter applied, so
            # every control built from it offers the same options whatever the
            # others are set to. Filtering it made the selected module vanish
            # from its own dropdown -- raising the level was enough -- leaving a
            # select rendered blank while it was still filtering by it.
            if _matches(row, "debug", "", since, until, "", campaign):
                modules.add(str(row.get("module", "")))
            if not _matches(row, floor, module, since, until, contains, campaign):
                continue
            counts[_rows.level_name(row.get("level"))] += 1
            newest.offer(row)
    return {"rows": newest.rows(), "modules": sorted(m for m in modules if m),
            "counts": counts, "total": sum(counts.values()),
            "truncated": newest.dropped, "level": floor,
            "since": since, "until": until, "levels": list(_rows.LEVELS)}


def scan(*, level: str = "debug", module: str = "", since: str = "", until: str = "",
         contains: str = "", campaign: str = "", days: int = _rows.DEFAULT_DAYS):
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
    floor = _rows.level_name(level)
    since, until = _rows.span(since, until, days)
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


def _lines(path: Path):
    """Every well-formed row in ``path``, oldest first, one at a time.

    A GENERATOR reading line by line, not `read_text().splitlines()`. A month
    file may approach `MAX_MONTH_BYTES`, and slurping it held the whole file as
    one string plus a list of every parsed row -- for a read whose result is
    bounded to a couple of hundred rows by `Newest`. On the packaged Android
    build that is an out-of-memory kill at exactly the moment somebody is
    investigating a verbose log. The usage ledger's reader already made this
    call; this one now matches it.

    See `read` on why an unreadable file is empty rather than an exception --
    and note that `_read_from`, which backs the live tail, deliberately does
    the opposite.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                row = _rows._shaped(raw)
                if row is not None:
                    yield row
    except (OSError, ValueError):
        # Mid-file as well as on open: a decode error surfaces on the read that
        # reaches the bad bytes, and a report drawn short beats no report.
        return


# ---- live tailing ----
def cursor() -> str:
    """A cursor at the current end of the log: "tail from here, nothing before".

    Spelled ``<month>.jsonl:<offset>``. A byte offset into an append-only file
    is the one cursor that cannot miss a row or repeat one -- a timestamp
    cursor does both, since rows share a millisecond and a row can be appended
    with a ts already passed.
    """
    paths_ = _tail_files()
    if not paths_:
        return f"{_rows._now()[:7]}.jsonl:0"
    newest = paths_[-1]
    try:
        return f"{newest.name}:{newest.stat().st_size}"
    except OSError:
        return f"{newest.name}:0"


def tail(from_cursor: str = "", budget: int = _rows.MAX_TAIL_BYTES, **filters) -> dict:
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
    files = _tail_files()
    if not files:
        return {"rows": [], "cursor": from_cursor or cursor(), "more": False}
    names = [p.name for p in files]
    if name not in names:
        # An unknown (or absent) cursor starts at the newest file's end rather
        # than replaying the month: a client opening a tail asked for what
        # happens NEXT, and `read` is how it gets the backlog.
        return {"rows": [], "cursor": cursor(), "more": False}
    left = max(1, min(int(budget or _rows.MAX_TAIL_BYTES), _rows.MAX_TAIL_BYTES))
    rows: list[dict] = []
    index = names.index(name)
    more = False
    while True:
        chunk, offset, used, filled = _read_from(files[index], offset, left)
        rows.extend(row for row in chunk if _tail_matches(row, filters))
        left -= used
        more = filled or left <= 0
        if left <= 0:
            break                          # budget spent; the rest waits a poll
        if used > 0:
            continue                       # keep draining this file
        # Nothing complete left here. A newer month existing means this one is
        # FINISHED -- nothing is ever appended to a past month, so trailing
        # bytes that are not yet a row there never will be. Waiting for the
        # cursor to reach the last byte instead parked the tail in a dead month
        # forever on a torn final line, and the rollover this exists for never
        # happened. Draining first (above) is what keeps that from costing a
        # row, and re-reading rather than breaking is what keeps it from
        # costing a whole poll interval.
        if index + 1 < len(files):
            index, offset = index + 1, 0   # month rolled over
            continue
        break
    # `more` means "rows are WAITING", not "the file holds more bytes", and the
    # difference is a hot loop: the caller polls again immediately while this
    # is true, so a trailing line with no newline -- what a half-written row
    # and a hand-edited file both look like -- would spin forever on a byte
    # count that never becomes a row. `filled` is the honest signal: the read
    # took everything its budget allowed, so the file had more to give.
    return {"rows": rows, "cursor": f"{files[index].name}:{offset}", "more": more}


def _tail_matches(row: dict, filters: dict) -> bool:
    """`_matches` with the tail's defaults: no window, since a tail is by
    definition about now."""
    return _matches(row, _rows.level_name(filters.get("level") or "debug"),
                    str(filters.get("module") or ""), "", "",
                    str(filters.get("contains") or ""),
                    str(filters.get("campaign") or ""))


def _past_newline(handle, budget: int) -> int | None:
    """Bytes from here to just past the next newline, or None if there is none.

    Reads in `budget`-sized chunks and keeps none of them: the caller is
    stepping over a corrupt line, not collecting it. Gives up after
    `MAX_LINE_BYTES` and reports what it consumed, so a file with no newline
    left in it at all cannot hold the tail still forever.
    """
    consumed = 0
    while consumed < _rows.MAX_LINE_BYTES:
        chunk = handle.read(budget)
        if not chunk:
            return None
        found = chunk.find(b"\n")
        if found >= 0:
            return consumed + found + 1
        consumed += len(chunk)
    return consumed


def _read_from(path: Path, offset: int,
               budget: int) -> tuple[list[dict], int, int, bool]:
    """Complete rows in ``path`` after ``offset``, the new offset, the bytes
    consumed, and whether the read filled its window.

    That last flag is what `tail` turns into ``more``, and it is deliberately
    not "are there bytes left in the file": a trailing line with no newline
    leaves bytes that will never become a row, and a caller that polls again
    while bytes remain would spin on them forever.

    The read window is ``budget`` but never smaller than `MIN_LINE_READ`.
    That floor is what distinguishes "this line is corrupt" from "this poll
    had little budget left": with a window of a few hundred bytes, an ordinary
    row has no newline inside it either, and the oversized-line path below
    would step over a perfectly good row and drop it. Reading slightly past a
    small remaining budget costs one line; getting this wrong costs rows.

    Only whole lines are consumed, so the offset returned always names a row
    boundary and never lands inside one.
    """
    # No `except OSError` here, unlike `_lines`. A log that cannot be READ is
    # the thing a live tail most needs to say out loud: swallowing it into an
    # empty poll made the panel go on claiming the log was merely quiet, and
    # the route's `except OSError` -- which exists to send exactly that frame
    # -- could never fire. The paged reader stays tolerant because a report
    # drawn short beats no report; a tail drawn short is a lie.
    window = max(int(budget), _rows.MIN_LINE_READ)
    size = path.stat().st_size
    if offset > size:
        offset = 0                       # truncated or replaced; see `tail`
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read(window)
        end = data.rfind(b"\n")
        if end < 0:
            if len(data) < window:
                # A partial trailing line. `filled=False`: nothing here can
                # become a row until more bytes land, and saying otherwise is
                # what makes the caller re-poll with no sleep, forever.
                return [], offset, 0, False
            # No newline in a full window, so longer than anything this module
            # writes: corrupt. Stepped over in bounded chunks rather than read
            # -- a bare `readline` here would pull the rest of the file into
            # memory, which is exactly what the budget exists to prevent.
            past = _past_newline(handle, window)
            if past is None:
                return [], offset, 0, False   # no newline yet; wait for more
            return [], offset + len(data) + past, len(data) + past, True
    filled = len(data) >= window
    whole = data[:end + 1]
    out = []
    for chunk in whole.decode("utf-8", errors="replace").splitlines():
        row = _rows._shaped(chunk)
        if row is not None:
            out.append(row)
    return out, offset + len(whole), len(whole), filled
