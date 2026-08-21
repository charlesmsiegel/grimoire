"""The run record and the registry that indexes it. Pure data, no scheduling.

A run is one route invocation that produces LLM output, decoupled from the HTTP
request that asked for it. The request may end -- the phone locks, the tab is
backgrounded, the socket resets -- while the run keeps going; a later request
finds it by id and picks up the frames it missed.

Two axes decide how a run behaves, and they are deliberately separate:

* the **subject** it acts on -- a scene, a campaign, a world, or the app --
  which is what a client asks about and what scopes every lookup;
* the **class** of work -- ``turn``, ``review``, ``background``, ``draft`` --
  which decides whether it excludes other work on that subject.

``turn`` and ``review`` share one exclusion key per scene, because both rewrite
that scene's transcript and two of them at once lose one. ``background`` and
``draft`` declare none: they legitimately overlap, so the subject index is a
collection rather than a most-recent pointer.

Nothing here imports ``streaming`` or ``scenes``. The registry is reachable from
the whole route layer, and a dependency in that direction would make the module
graph cyclic; ``test_import_guard`` holds it to that.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Literal

REAP_SECONDS = 600
"""How long a terminal run stays discoverable.

Long enough that a phone left face-down through a whole turn still finds its
result on unlock, and short enough that a day of play does not accumulate every
run it ever made. A run reaped before its client returns is not a lost reply --
the reply is on disk -- but the client can no longer tell 'it landed' from 'it
never sent', which is what the durable attempt record answers instead.
"""

Subject = tuple
"""``("scene", cid, sid)`` / ``("campaign", cid)`` / ``("world", wid)`` /
``("global",)``.

A plain tuple on purpose: hashable, so it keys the indexes directly, and
trivially serialisable without pydantic -- which this package has to stay
agnostic about (``test_pydantic_guard``).
"""

RunClass = Literal["turn", "review", "background", "draft"]

RunState = Literal["running", "landed", "failed", "cancelled"]

_EXCLUSIVE: frozenset[str] = frozenset({"turn", "review"})
"""Classes that hold their subject against other work.

A turn appends to the transcript and a review reads it whole and marks it
absorbed; either running while the other mutates loses work that cannot be
regenerated.
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
    """
    if cls not in _EXCLUSIVE:
        return None
    return "\x00".join(str(part) for part in subject)


class Run:
    """One detached unit of work, and the frames it has produced so far."""

    def __init__(self, subject: Subject, cls: RunClass, kind: str,
                 attempt_id: str | None, scene_identity: str | None,
                 labels: dict, event_factory: Callable[[], object]) -> None:
        self.id = uuid.uuid4().hex
        self.subject = subject
        self.cls = cls
        self.kind = kind
        self.attempt_id = attempt_id
        # Captured at START, not read at terminal. A scene deleted mid-run is
        # supported, and resolving its title afterwards would find nothing --
        # in exactly the case the error notification exists to report.
        self.scene_identity = scene_identity
        self.labels = labels
        self.state: RunState = "running"
        self.result: dict | None = None
        self.error: dict | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.frames: list[dict] = []
        # Both events exist before the run is ever published, because the
        # pre-start window is real: a cancel or a discovery can arrive while the
        # producing route is still doing synchronous setup, before any runner
        # exists. A run observable without its events leaves that caller waiting
        # on something nothing will make.
        self.ready = event_factory()
        self.terminal = event_factory()
        self._lock = threading.Lock()

    def append_frame(self, frame: str) -> int:
        """Buffer one raw SSE frame and return the absolute index it got.

        ``frame`` is the wire text verbatim -- what ``event_stream`` yields --
        and that includes the comment frame ``": heartbeat\\n\\n"``, which has no
        JSON payload at all. Storing decoded payloads instead would force
        heartbeats to be dropped, and a dropped frame shifts every later index,
        so a client resuming at ``consumed + 1`` replays text it has already
        rendered. Store it as-is, index it, and let replay be concatenation.
        """
        with self._lock:
            index = len(self.frames)
            self.frames.append({"index": index, "raw": frame})
            return index

    def frames_since(self, index: int) -> list[dict]:
        """Frames from ``index`` onward, INCLUSIVE.

        A client that consumed through N asks for N+1. Making this exclusive
        would drop one frame per reconnect -- invisible until someone reads the
        text and finds a word missing mid-sentence.
        """
        with self._lock:
            return self.frames[max(index, 0):]

    def finish(self, state: RunState, at: float | None = None) -> None:
        """Mark the run terminal. ``at`` defaults to now; tests pass it so
        reaping is deterministic rather than a race against the clock.

        ``ended_at`` is set BEFORE ``state``, and both under the record's lock.
        The registry reads these two attributes under a *different* lock, so
        anything that observes the run as terminal must already be able to see
        when it ended -- otherwise a reap that runs in between finds a terminal
        run with no timestamp and silently keeps it for another cycle.
        """
        with self._lock:
            self.ended_at = time.time() if at is None else at
            self.state = state


class RunRegistry:
    """Every run this process knows about, and the indexes to find one.

    Addressed by id, never by ``(cid, sid)``. A subscriber that resolved a frame
    stream by scene could, between one run ending and the next starting, attach
    a view showing scene B to frames produced for scene A.
    """

    def __init__(self, event_factory: Callable[[], object] | None = None) -> None:
        self._event_factory = event_factory or _PlainEvent
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._by_subject: dict[Subject, list[str]] = {}
        self._by_attempt: dict[tuple[Subject, str], str] = {}
        self._by_key: dict[str, str] = {}

    def start_or_existing(self, subject: Subject, cls: RunClass, kind: str,
                          attempt_id: str | None, scene_identity: str | None,
                          labels: dict) -> tuple[Run, bool]:
        """Reserve a run, or hand back the one this attempt already made.

        ``labels`` is required, not optional: left defaultable, an
        implementation can omit it at every call site, pass every test, and ship
        notifications with no campaign or scene text -- a feature silently
        absent behind a green suite.

        All of it under one lock, get-or-create style. A check-then-act would
        hand two concurrent first callers different answers, which is precisely
        the double-send this exists to prevent.
        """
        with self._lock:
            if attempt_id is not None:
                # Attempt ids come from clients, so they are only unique within
                # a subject -- two scenes may pick the same one.
                known = self._by_attempt.get((subject, attempt_id))
                if known is not None:
                    # Returned even when terminal: a client whose response was
                    # lost re-sends with the same id and must adopt the original
                    # outcome rather than start the work a second time.
                    return self._runs[known], False

            key = exclusion_key(subject, cls)
            if key is not None:
                holder_id = self._by_key.get(key)
                holder = self._runs.get(holder_id) if holder_id else None
                if holder is not None and holder.state == "running":
                    raise RunInFlightError(holder.id)

            run = Run(subject, cls, kind, attempt_id, scene_identity, labels,
                      self._event_factory)
            self._runs[run.id] = run
            self._by_subject.setdefault(subject, []).append(run.id)
            if attempt_id is not None:
                self._by_attempt[(subject, attempt_id)] = run.id
            if key is not None:
                self._by_key[key] = run.id
            return run, True

    def get(self, run_id: str, subject: Subject) -> Run | None:
        """The run, or ``None`` if this subject does not own it.

        The subject check is the isolation: a run id from another scene answers
        'gone', never another scene's state.
        """
        with self._lock:
            run = self._runs.get(run_id)
            return run if run is not None and run.subject == subject else None

    def for_subject(self, subject: Subject) -> list[Run]:
        """Every run on this subject, live and terminal, oldest first.

        A collection rather than a pointer: drafts and background work declare
        no exclusion key, so they overlap legitimately, and a most-recent
        pointer would hide the first one with no discovery path left.
        """
        with self._lock:
            return [self._runs[rid] for rid in self._by_subject.get(subject, [])
                    if rid in self._runs]

    def live_for_key(self, key: str | None) -> Run | None:
        """The running holder of an exclusion key, if there is one."""
        if key is None:
            return None
        with self._lock:
            holder_id = self._by_key.get(key)
            holder = self._runs.get(holder_id) if holder_id else None
            return holder if holder is not None and holder.state == "running" else None

    def reap(self, now: float) -> int:
        """Drop terminal runs older than the window. Returns how many went.

        Every index is cleared, not just ``_runs``: an entry left in
        ``_by_attempt`` would make a later send with the same attempt id adopt a
        record that no longer exists, and one left in ``_by_key`` would wedge
        the scene permanently -- neither visible through ``get``.
        """
        cutoff = now - REAP_SECONDS
        with self._lock:
            dead = [r for r in self._runs.values()
                    if r.ended_at is not None and r.ended_at < cutoff]
            for run in dead:
                del self._runs[run.id]
                ids = self._by_subject.get(run.subject)
                if ids is not None:
                    # Filter rather than `.remove()`: a desynced index would
                    # raise ValueError here and take down the reaper thread for
                    # the life of the process, turning a bookkeeping slip into
                    # an unbounded memory leak.
                    ids[:] = [rid for rid in ids if rid != run.id]
                    if not ids:
                        del self._by_subject[run.subject]
                if run.attempt_id is not None:
                    self._by_attempt.pop((run.subject, run.attempt_id), None)
                key = exclusion_key(run.subject, run.cls)
                if key is not None and self._by_key.get(key) == run.id:
                    del self._by_key[key]
            return len(dead)


def install_registry(app) -> None:
    """Put a registry on the app.

    Called from ``create_app``, NOT from the lifespan. ``conftest.client``
    returns a bare ``TestClient(app)``, which never emits startup -- the same
    reason the gateway clients are built in ``create_app`` (``main.py``). A
    registry created only in the lifespan would be absent for every route test
    and every migrated handler, failing on ``app.state`` before reaching an
    assertion. It is pure data and needs no running loop, so this costs nothing.
    """
    app.state.runs = RunRegistry()
