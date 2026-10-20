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
  is asked to attach to a bug report. Request credentials must stay out, so the
  handler goes on the `grimoire` logger and third-party output stays where it
  was. Debug-level incoming LLM capture records response bodies, including
  reasoning and arbitrary provider fields. A body can echo private data; the
  Configuration page explains that these captures need review before sharing.

Retention is deliberately none, the same call `store.usage` makes: the files
are month-scoped, plain text and trivially deletable by hand, which beats a
pruner that destroys history somebody turned this on to have.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import heapq
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from .. import atomic, config, paths
from ._reading import Newest as Newest
from ._reading import _haystack as _haystack
from ._reading import _lines as _lines
from ._reading import _matches as _matches
from ._reading import _past_newline as _past_newline
from ._reading import _read_from as _read_from
from ._reading import _tail_files as _tail_files
from ._reading import _tail_matches as _tail_matches
from ._reading import _window_files as _window_files
from ._reading import cursor as cursor
from ._reading import read as read
from ._reading import scan as scan
from ._reading import tail as tail
from ._recording import Handler as Handler
from ._recording import _append as _append
from ._recording import _capped as _capped
from ._recording import _ensure_process as _ensure_process
from ._recording import _floor as _floor
from ._recording import _incoming_event as _incoming_event
from ._recording import _reset_after_fork as _reset_after_fork
from ._recording import _room_for as _room_for
from ._recording import _stored_level as _stored_level
from ._recording import _written as _written
from ._recording import apply_level as apply_level
from ._recording import forget_file_sizes as forget_file_sizes
from ._recording import incoming_capture as incoming_capture
from ._recording import install as install
from ._recording import level as level
from ._recording import record as record
from ._rows import _DAY as _DAY
from ._rows import _FROM_LOGGING as _FROM_LOGGING
from ._rows import _MONTH as _MONTH
from ._rows import _RANK as _RANK
from ._rows import _STAT_EVERY as _STAT_EVERY
from ._rows import DEFAULT_DAYS as DEFAULT_DAYS
from ._rows import DEFAULT_LIMIT as DEFAULT_LIMIT
from ._rows import FLOORS as FLOORS
from ._rows import LEVELS as LEVELS
from ._rows import MAX_DAYS as MAX_DAYS
from ._rows import MAX_LIMIT as MAX_LIMIT
from ._rows import MAX_LINE_BYTES as MAX_LINE_BYTES
from ._rows import MAX_MESSAGE as MAX_MESSAGE
from ._rows import MAX_MONTH_BYTES as MAX_MONTH_BYTES
from ._rows import MAX_TAIL_BYTES as MAX_TAIL_BYTES
from ._rows import MAX_TRACE as MAX_TRACE
from ._rows import MIN_LINE_READ as MIN_LINE_READ
from ._rows import ROOT_LOGGER as ROOT_LOGGER
from ._rows import _back as _back
from ._rows import _clip as _clip
from ._rows import _level_of as _level_of
from ._rows import _module_name as _module_name
from ._rows import _now as _now
from ._rows import _shaped as _shaped
from ._rows import _text as _text
from ._rows import _valid_day as _valid_day
from ._rows import level_name as level_name
from ._rows import log_dir as log_dir
from ._rows import month_path as month_path
from ._rows import span as span

# Preserve deferred module annotations without copying live values.
__annotations__ = {'_written': 'dict[str, int]', '_capped': 'set[str]'}

from . import _recording as _recording


def __getattr__(name: str):
    """Read process-replaced state from its single runtime owner."""
    if name == '_busy':
        return _recording._busy
    if name == '_size_guard':
        return _recording._size_guard
    if name == '_append_guard':
        return _recording._append_guard
    if name == '_pid':
        return _recording._pid
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted([*globals(), *['_busy', '_size_guard', '_append_guard', '_pid']])

# Original star-import surface, captured before extraction.
__all__ = [
    'DEFAULT_DAYS',
    'DEFAULT_LIMIT',
    'FLOORS',
    'LEVELS',
    'MAX_DAYS',
    'MAX_LIMIT',
    'MAX_LINE_BYTES',
    'MAX_MESSAGE',
    'MAX_MONTH_BYTES',
    'MAX_TAIL_BYTES',
    'MAX_TRACE',
    'MIN_LINE_READ',
    'ROOT_LOGGER',
    'Callable',
    'Handler',
    'Newest',
    'Path',
    'annotations',
    'apply_level',
    'atomic',
    'config',
    'cursor',
    'date',
    'forget_file_sizes',
    'heapq',
    'incoming_capture',
    'install',
    'json',
    'level',
    'level_name',
    'log_dir',
    'logging',
    'month_path',
    'os',
    'paths',
    're',
    'read',
    'record',
    'scan',
    'span',
    'sys',
    'tail',
    'threading',
    'time',
    'timedelta',
    'traceback',
]
