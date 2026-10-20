"""Subject identities, exclusion vocabulary and handshake contracts."""

from __future__ import annotations

import logging
import threading
from typing import Literal, Protocol

from ._router import router as router

STREAM_POLL_SECONDS = 0.05
"""How often a live replay looks for newly appended frames.

Frames arrive from the runner's thread and the handshake events are set-once,
so there is nothing to await; this is well under the threshold where a reader
would notice, and an idle run costs one list slice per tick.
"""

CANCEL_TIMEOUT_SECONDS = 30.0
"""How long the cancel route waits for the abort hook before answering anyway.

Bounded because a provider that never unwinds must not hold an HTTP worker
forever; long enough that a normal unwind always fits inside it.
"""

REAP_SECONDS = 600
"""How long a terminal run stays discoverable.

Long enough that a phone left face-down through a whole turn still finds its
result on unlock, and short enough that a day of play does not accumulate every
run it ever made.

What it does NOT survive, stated plainly because an earlier version of this
docstring pointed at a "durable attempt record" that has never existed: the
registry is in memory, so both the window expiring and the process restarting
lose the attempt mapping. A client that retries the same attempt id after
either will not adopt the original outcome -- `start_or_existing` sees an
attempt it has never met and runs the turn again, duplicating the player's post
and the reply. A run reaped before its client returns is not a lost reply (the
reply is on disk), but the client can no longer tell "it landed" from "it never
sent", and this is the boundary of what idempotency by attempt id covers.
Closing it means persisting each attempt's outcome beside the scene, which is
a store change and not a registry one.
"""

Subject = tuple
"""``("scene", cid, identity)`` / ``("campaign", cid)`` / ``("world", wid)`` /

A scene is named by its IDENTITY, not by its `sid`. The id is neither stable nor
unique over time -- a rename mints a new one and a deletion frees the old one
for the next scene -- so keying runs by it broke twice over: a rename left a
terminal run reachable from neither the old URL nor the new one (so a client
could not tell "it landed" from "it never sent", and re-sent), and a
replacement that recycled the id could reach the dead scene's run. The identity
is the one name that survives the first and is not shared by the second.

``("global",)``.

A plain tuple on purpose: hashable, so it keys the indexes directly, and
trivially serialisable without pydantic -- which this package has to stay
agnostic about (``test_pydantic_guard``).
"""

RunClass = Literal["turn", "review", "background", "draft", "maintenance"]

RunState = Literal["running", "landed", "failed", "cancelled"]

_EXCLUSIVE: frozenset[str] = frozenset({"turn", "review"})
"""Classes that hold their subject against other work.

A turn appends to the transcript and a review reads it whole and marks it
absorbed; either running while the other mutates loses work that cannot be
regenerated.
"""

MAINTENANCE_KEY = "global\x00image-maintenance"
"""The one exclusion key every `maintenance` run holds, whatever its subject.

Keyed on the CLASS rather than built from the subject, unlike a turn's: a
migration and a collection both walk the whole image store and may delete from
it, so two of them must never overlap however they were started -- and a key
built from `("global",)` would be shared with nothing else anyway, so no other
global run (a model refresh) is refused by it.
"""

ATTACHABLE: frozenset[str] = frozenset({"turn", "review"})
"""Classes an attemptless discovery may answer with.

`GET .../run` without an attempt asks "what is the newest thing on this scene
I might still be waiting for?", and only these two are ever that: a `turn` has
a frame buffer to read and a `review` has a payload to poll. `background` and
`draft` have neither, and a client handed one attaches to a buffer that never
fills -- the composer stays locked over a scene where nothing is wrong. Once a
landed turn schedules its own follow-ups (#397), a `background` run is
routinely the newest run on a scene, so this stopped being hypothetical.

The same members as `_EXCLUSIVE` today and deliberately not spelled as that
name: they answer different questions, and a background run that one day
needed a key must not thereby become attachable.
"""


class HandshakeEvent(Protocol):
    """What a run's `ready`/`terminal` events must do.

    Two implementations satisfy it: `_PlainEvent` (no event loop, for a
    registry built in `create_app` and used by tests that never start a
    lifespan) and `runner._PortalEvent` (created and mutated only on the
    lifespan loop, so an async waiter is woken correctly). Typing the factory
    as returning `object` compiled but left every caller of `.wait()`
    unchecked, which is how a protocol earns its place.
    """

    def set(self) -> None: ...
    def is_set(self) -> bool: ...
    def wait(self, timeout: float | None = None) -> bool: ...


class StoreMovingError(Exception):
    """Raised when a run is reserved while the store root is being moved.

    The move takes the registry's own lock for its whole duration, so a
    reservation can only see this flag by arriving in a window where the answer
    really is "not right now". Transient by construction -- a moment later the
    root is settled and the same send succeeds -- which is why it is a distinct
    kind from `run_in_flight` rather than a variation of it.
    """


class RunInFlightError(Exception):
    """Raised when a class that excludes finds its subject already held.

    Carries the winning ``run_id`` so the caller can attach to it rather than
    only learning that it lost -- which is the difference between a second tab
    showing the reply in progress and a second tab showing an error.
    """

    def __init__(self, run_id: str) -> None:
        super().__init__(f"a run is already in flight: {run_id}")
        self.run_id = run_id


class MaintenanceHeldError(Exception):
    """Raised when a `maintenance` run is reserved while a tree operation holds
    the store against one (`RunRegistry.exclude_maintenance`).

    Transient, like `StoreMovingError`: the fork or export finishes and the
    same start succeeds, so it reaches the client as `busy`.
    """


class SyncRunningError(Exception):
    """Raised when the store root is asked to move while a cache-sync batch is
    running in this process (spec 05 section 4.2). Transient: the batch ends
    and the same move succeeds, so it reaches the client as `busy`."""


class MaintenanceRunningError(Exception):
    """Raised when a tree operation asks to hold the store while a `maintenance`
    run is live. Carries the run's id so the refusal can name it."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"image-store maintenance is running: {run_id}")
        self.run_id = run_id


class _PlainEvent:
    """The default handshake event: set-once, thread-safe, no event loop.

    The registry is constructed in ``create_app`` and used by tests that never
    start a lifespan, so it cannot depend on a running async backend. Task 3
    injects a portal-backed factory in its place for runs that will actually
    execute, where an async waiter needs to be woken on the loop.
    """

    __slots__ = ("_flag",)

    def __init__(self) -> None:
        self._flag = threading.Event()

    def set(self) -> None:
        self._flag.set()

    def is_set(self) -> bool:
        return self._flag.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._flag.wait(timeout)


def exclusion_key(subject: Subject, cls: RunClass) -> str | None:
    """The key this class holds on this subject, or ``None`` if it holds none.

    Built from the WHOLE subject, never from the scene id alone. Scene ids are
    campaign-local -- ``store.scenes.serialize._numbering`` derives the next
    number from the files in that campaign's own directory -- so ``0001--mara``
    exists in every campaign that has a first scene. A key made from ``sid``
    would make a turn in one campaign refuse a turn in another, or worse, let
    one campaign's reply attach to the other's scene.

    ``maintenance`` is the exception, and `MAINTENANCE_KEY` says why.
    """
    if cls == "maintenance":
        return MAINTENANCE_KEY
    if cls not in _EXCLUSIVE:
        return None
    return "\x00".join(str(part) for part in subject)


# --- routes -----------------------------------------------------------------
#
# Deliberately thin. Everything above this line is pure data with no FastAPI in
# it, and these handlers only resolve a subject, look a run up, and shape a
# response. Nothing here imports `streaming` or `scenes` -- the registry is
# reachable from the whole route layer, and a dependency in that direction
# would make the module graph cyclic (`test_import_guard`).

_log = logging.getLogger('grimoire.routes.runs')


_MAX_PRECANCEL = 256
"""How many un-reserved Stops to remember.

Generous next to the one or two that can plausibly be in flight, and small
enough that a client inventing ids cannot grow the registry without bound.
"""

UNRESOLVED = "\x00unresolved"
"""Stands in for a scene identity that could not be resolved.

Deliberately not a valid token -- `_TOKEN` is 32 hex characters -- so `_owns`
compares it against every stored identity and matches none. `None` cannot be
used because that is already the wildcard.
"""


#: The kind of the one `background` run on the global subject that syncs the
#: compiled cache (spec 05 section 7.8).
CACHE_SYNC_KIND = "cache-sync"
