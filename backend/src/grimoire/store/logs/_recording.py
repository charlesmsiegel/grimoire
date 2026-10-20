"""One process-owned log writer, bridge, threshold and fork recovery."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from collections.abc import Callable
from pathlib import Path

from .. import atomic, config, paths
from . import _rows

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
_floor = {"rank": _rows._RANK["info"]}

#: Re-entrancy latch; see the module docstring. Thread-local because the guard
#: is per call stack -- a second thread logging concurrently is not a recursion
#: and must not be silenced.
_busy = threading.local()

#: Bytes appended by this process since the last size check, per month path.
_written: dict[str, int] = {}
_capped: set[str] = set()
_size_guard = threading.Lock()
# Serialize this process's appenders, including the size-cap marker. On
# Windows, concurrent O_APPEND writes can overwrite a row without raising;
# the four-thread log test reproduces it with every write reporting success.
# The thread-local re-entry latch above prevents recursive acquisition.
_append_guard = threading.Lock()
_pid = os.getpid()


def _reset_after_fork() -> None:
    global _append_guard, _size_guard, _busy, _pid  # noqa: PLW0603 -- replace parent-owned guards
    # A parent's other threads disappear in the child. Neither their locks
    # nor a copied re-entry latch/byte accounting belong to the new process.
    _append_guard = threading.Lock()
    _size_guard = threading.Lock()
    _busy = threading.local()
    _written.clear()
    _capped.clear()
    _pid = os.getpid()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def _ensure_process() -> None:
    if _pid != os.getpid():
        _reset_after_fork()


def apply_level(name: str = "") -> str:
    """Set the threshold, from ``name`` or from the stored config.

    Returns the level in force, which can be quieter than ``name`` asked for:
    the floor is clamped at `error` so the error store cannot be switched off
    (see `FLOORS`).
    """
    # Clamped at `error`, never above it -- see `FLOORS`. `level()` then
    # reports the floor actually in force rather than the one that was asked
    # for, so nothing downstream has to know the clamp happened.
    level = _rows.level_name(name or _stored_level())
    _floor["rank"] = min(_rows._RANK[level], _rows._RANK["error"])
    level = _rows.LEVELS[_floor["rank"]]
    logger = logging.getLogger(_rows.ROOT_LOGGER)
    # The logger's level, and the handler's, are set to the same floor.
    #
    # An earlier version clamped the LOGGER at WARNING, meaning to keep
    # grimoire's warnings on a developer's terminal (`logging.lastResort`
    # prints WARNING and above to stderr) while a quieter floor governed the
    # file. That does not work and never did: `lastResort` fires only when a
    # record finds NO handler at all, and `install` gives every grimoire record
    # one. Attaching the file handler is itself what takes those lines off
    # stderr, and no arrangement of levels here changes that.
    #
    # Three levels, and they are not the same question.
    #
    # The LOGGER decides what is emitted at all. It goes to the floor, but
    # never quieter than WARNING: `Handler.emit` writes WARNING and above to
    # stderr (giving back the lines `logging.lastResort` printed before this
    # module attached a handler at all), and a record the logger discards never
    # reaches `emit` to be written. Setting it to ERROR for an `error` floor
    # therefore silenced the terminal again through the back door -- for a
    # setting Configuration describes as being about a FILE.
    #
    # The HANDLER stays open, because it is the one that also feeds stderr.
    #
    # `record` is where the file's floor is actually enforced -- and it has to
    # be there regardless, since `store.errors` and every direct caller reach
    # it without passing through a handler at all.
    logger.setLevel(min(getattr(logging, level.upper()), logging.WARNING))
    for handler in logger.handlers:
        if isinstance(handler, Handler):
            handler.setLevel(logging.NOTSET)
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
    return _rows.LEVELS[_floor["rank"]]


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
    _ensure_process()
    if getattr(_busy, "on", False):
        return None                      # nested: see `_busy`
    name = _rows.level_name(level_)
    if _rows._RANK[name] < _floor["rank"]:
        return None
    _busy.on = True
    try:
        # The whole body is inside the guard, the row's construction included:
        # `str()` on an object with a raising `__str__` fails exactly as the
        # write can, and this function's contract is that it cannot fail its
        # caller. Building the row above the `try` would leave that hole -- the
        # one `store.usage.record` documents having had.
        row: dict = {"ts": ts or _rows._now(), "level": name,
                     "module": _rows._module_name(module), "message": _rows._clip(message, _rows.MAX_MESSAGE)}
        for key, value in (("kind", kind), ("campaign", campaign),
                           ("scene", scene), ("task", task)):
            if value:
                row[key] = str(value)
        # `extra` rather than reusing `value` above: the loop before this one
        # binds it to a `str`, and a second loop rebinding the same name to an
        # `object` is an assignment mypy is right to refuse.
        for name_, extra in fields.items():
            if isinstance(extra, str):
                row[name_] = _rows._clip(extra, _rows.MAX_MESSAGE)
            elif extra is None or isinstance(extra, (bool, int, float)):
                row[name_] = extra
        if trace:
            row["trace"] = _rows._clip(trace, _rows.MAX_TRACE, head=False)
        with _append_guard:
            return _append(row)
    except (OSError, TypeError, ValueError):
        # OSError is the write. TypeError is a field `json.dumps` refuses --
        # the sort of thing only a test finds, and the sort that would take
        # down a turn. ValueError covers `allow_nan` and the coercions above.
        return None
    finally:
        _busy.on = False


def incoming_capture() -> Callable[[dict], None] | None:
    """Full incoming bodies are opt-in through the existing Debug floor.

    Resolved per call, so changing the floor affects the next generation.
    Requests, URLs and authorization headers are never passed to this sink.
    Response bodies can contain private prose and reasoning; see Configuration.
    """
    return _incoming_event if _floor["rank"] == _rows._RANK["debug"] else None


def _incoming_event(event: dict) -> None:
    # Keep the existing writer, size backstop and readers. Split the serialized
    # payload BEFORE record clips strings, so a large extension/usage field is
    # reconstructible: group by call_id/attempt/sequence, order by part, join
    # payload, then json.loads once. A missing part is detectable via parts.
    # The monthly cap still applies; log_capped explicitly marks that boundary.
    payload = json.dumps(event["payload"], ensure_ascii=False, allow_nan=False)
    parts = max(1, (len(payload) + _rows.MAX_MESSAGE - 1) // _rows.MAX_MESSAGE)
    metadata = {key: value for key, value in event.items() if key != "payload"}
    for index in range(parts):
        if record("debug", "llm.capture", "Incoming LLM response", kind="llm_incoming",
                  **metadata, part=index + 1, parts=parts,
                  payload=payload[index * _rows.MAX_MESSAGE:(index + 1) * _rows.MAX_MESSAGE]) is None:
            break


def _append(row: dict) -> dict | None:
    """Write one row to its month file, honouring `MAX_MONTH_BYTES`.

    Raises -- the caller is inside the guard `record` documents, and sharing
    this is what keeps a second writer from acquiring different failure
    semantics.
    """
    path = _rows.month_path(row["ts"])
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
            # Re-stat before honouring a cap set earlier. Deleting or
            # truncating the month file is exactly what a user does to reclaim
            # the space the cap is complaining about, and a cap remembered for
            # the life of the process left ordinary logging switched off after
            # they had -- until a restart or the month rolled over, with
            # nothing on screen to say why.
            try:
                if path.stat().st_size < _rows.MAX_MONTH_BYTES:
                    _capped.discard(key)
                    _written.pop(key, None)
            except OSError:
                _capped.discard(key)         # gone entirely; start again
                _written.pop(key, None)
        if key in _capped:
            return _rows._RANK[level_] >= _rows._RANK["error"]
        counted = _written.get(key)
        if counted is None or counted >= _rows._STAT_EVERY:
            try:
                actual = path.stat().st_size
            except OSError:
                actual = 0              # not there yet, or unreadable: assume room
            _written[key] = 0
        else:
            actual = None
        _written[key] = _written.get(key, 0) + size
        if actual is None or actual + _written[key] < _rows.MAX_MONTH_BYTES:
            return True
        _capped.add(key)
    # Outside the lock: this is itself an append, and `_capped` already holds
    # so it cannot recurse into another cap check for the same file.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.append_line(path, json.dumps(
            {"ts": _rows._now(), "level": "warning", "module": "store.logs",
             "message": f"log file reached {_rows.MAX_MONTH_BYTES} bytes; "
                        "only errors will be recorded for the rest of the month",
             "kind": "log_capped"}))
    except OSError:
        pass
    return _rows._RANK[level_] >= _rows._RANK["error"]


def forget_file_sizes() -> None:
    """Forget every cached size, and every month currently marked capped.

    Public because `PUT /config/data-dir` needs it: `_room_for` caches by
    absolute path, so a store repointed at a new root leaves byte counts (and
    possibly a cap) charged against files the new tree does not have -- which
    would silence a fresh log at the old one's size. `install` calls it for the
    same reason, one app at a time.
    """
    _ensure_process()
    with _size_guard:
        _written.clear()
        _capped.clear()


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
            level = _rows._level_of(log_record.levelno)
            message = log_record.getMessage()
            record(level, log_record.name, message, kind=kind, trace=trace)
            if log_record.levelno >= logging.WARNING:
                # Installing this handler is what stops `logging.lastResort`
                # firing -- it runs only when a record finds NO handler -- so
                # without this line, adding a log FILE silently took grimoire's
                # warnings off every terminal that had them before. Written
                # here rather than by a second handler, which this module
                # forbids for a reason that has nothing to do with stderr.
                sys.stderr.write(f"{level.upper()}: {log_record.name}: {message}\n")
        except Exception:  # noqa: BLE001 - a handler that raises is worse than a lost row
            pass


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
    logger = logging.getLogger(_rows.ROOT_LOGGER)
    for handler in [h for h in logger.handlers if isinstance(h, Handler)]:
        logger.removeHandler(handler)
    logger.addHandler(Handler())
    forget_file_sizes()
    return apply_level()
