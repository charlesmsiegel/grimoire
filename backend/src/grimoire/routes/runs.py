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

import contextlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Literal, Protocol

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .. import runner, store
from ..store import attempts as scenes_attempts
from ..store import scenes
from ..store.campaigns import read as campaigns_read
from ..store.campaigns.paths import CampaignNotFound

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

RunClass = Literal["turn", "review", "background", "draft"]

RunState = Literal["running", "landed", "failed", "cancelled"]

_EXCLUSIVE: frozenset[str] = frozenset({"turn", "review"})
"""Classes that hold their subject against other work.

A turn appends to the transcript and a review reads it whole and marks it
absorbed; either running while the other mutates loses work that cannot be
regenerated.
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
                 labels: dict,
                 events: tuple[HandshakeEvent, HandshakeEvent],
                 review_generation: str | None = None) -> None:
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
        # Expiry is measured on a MONOTONIC clock, separately from the
        # wall-clock stamp above. A device that corrects a stale clock on
        # reconnect -- routine on a phone -- moves wall time by minutes or
        # more: forward, and a run that just finished is immediately past its
        # window, destroying the reconnect this feature exists to provide;
        # backward, and terminal runs are held with their frame buffers far
        # longer than intended. `ended_at` stays for display and for anything
        # a human reads.
        self.ended_monotonic: float | None = None
        self.frames: list[dict] = []
        # Both events exist before the run is ever published, because the
        # pre-start window is real: a cancel or a discovery can arrive while the
        # producing route is still doing synchronous setup, before any runner
        # exists. A run observable without its events leaves that caller waiting
        # on something nothing will make.
        self.ready, self.terminal = events
        # Whether a producer was ever handed to the runner. A route reserves
        # BEFORE its first mutator and can still return early -- a validation
        # refusal, a setup exception -- and a run left `running` with nothing
        # driving it is never reaped and makes every later turn on that scene
        # answer `run_in_flight` forever. `reservation` reads this to tell
        # "returned without starting" from "started and detached".
        self.started = False
        # Set by `runner.cancel` before it waits for anything, and read by
        # `_guarded` the moment it installs its scope. A Stop can arrive while
        # the producing route is still doing synchronous setup -- there is no
        # scope to cancel then, and without this the request was simply lost and
        # the provider started after the user had stopped it.
        self.cancel_requested = False
        # Which pending review this run belongs to -- minted when an absorb
        # starts, carried on the retries that fold into it, and stored on the
        # record. `DELETE .../pending-review` names it, and that is the whole
        # reason it exists: the pending payload is the absorb result verbatim
        # and names no producer, and the delete route carries no run id, so
        # "flag the scene's most recent run" would stop an unrelated live CHAT
        # run that happens to be newer, and "flag the run the record names"
        # finds nothing at all before the absorb has published one.
        self.review_generation = review_generation
        # Set by the reviewer's Cancel, under the campaign lock, BEFORE the
        # record is deleted -- and read by this run's terminal persist under
        # that same lock, which is what stops the runner from publishing and
        # recreating the review the player has just dismissed. A cancelled
        # review that reappears minutes later is worse than one that was never
        # saved. Distinct from `cancel_requested`, which is a Stop on the run
        # itself: this one is an intent about the RECORD, and it is what the
        # phases' `abandoned` predicate reads to stop generating for it.
        self.review_cancelled = False
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

    def finish(self, state: RunState, at: float | None = None,
               monotonic_at: float | None = None) -> None:
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
            self.ended_monotonic = (time.monotonic() if monotonic_at is None
                                    else monotonic_at)
            self.state = state


class RunRegistry:
    """Every run this process knows about, and the indexes to find one.

    Addressed by id, never by ``(cid, sid)``. A subscriber that resolved a frame
    stream by scene could, between one run ending and the next starting, attach
    a view showing scene B to frames produced for scene A.
    """

    def __init__(self, event_factory: Callable[[], HandshakeEvent] | None = None) -> None:
        self._event_factory = event_factory or _PlainEvent
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._by_subject: dict[Subject, list[str]] = {}
        self._by_attempt: dict[tuple[Subject, str], str] = {}
        # Stops that arrived before their attempt had a run. Insertion-ordered
        # so the oldest can be dropped when the cap is reached -- see
        # `cancel_or_precancel`. The values are unused; only membership matters.
        self._precancelled: dict[tuple[Subject, str], None] = {}
        # Which runs are live, and who to tell when that set becomes empty or
        # stops being empty. The Android shell promotes its server to a
        # foreground service on the first and demotes on the second, which is
        # what stops the OS reclaiming the process mid-turn.
        self._live: set[str] = set()
        self._on_live_change: Callable[[int], None] | None = None
        # The count is computed under `_lock` and DELIVERED outside it, so two
        # threads can arrive at the sink in the opposite order to the
        # transitions they describe. `_live_seq` stamps each one where it is
        # decided; `_sink_lock` serializes delivery and drops any stamp older
        # than the last one delivered. See `_fire_live`.
        self._live_seq = 0
        self._delivered_seq = -1
        self._sink_lock = threading.Lock()
        # How many store-root moves are in progress. A COUNT, not a flag: two
        # overlapping moves both entered under a boolean and the first to finish
        # cleared it while the second was still changing the pointer. See
        # `hold_still`.
        self._store_moves = 0
        self._by_key: dict[str, str] = {}

    def set_live_sink(self, sink: Callable[[int], None] | None) -> None:
        """Who to tell when the live-run count crosses zero.

        Set by the app at startup. Absent -- every desktop install -- the
        transitions are computed and dropped, which costs a set operation.
        """
        self._on_live_change = sink

    def _fire_live(self, count: int | None, seq: int) -> None:
        """Announce a crossing, OUTSIDE the registry lock and fail-soft.

        Outside, because the sink calls into the Android runtime and a
        cross-language call under this lock is the same deadlock shape the
        portal events already taught us. Fail-soft, because a foreground
        promotion the OS refuses must not take down a run that is generating
        perfectly well -- a notification is the least important thing here.

        **In order, though, and review caught that it was not.** Computing
        under the lock and delivering outside it leaves the delivery unordered:
        retiring the last run computes `0`, a new send reserves and delivers
        `1`, and the delayed `0` lands after it. The service then demotes while
        a run is live -- so the phone locks, the OS reclaims the process, and
        the turn this whole feature exists to save is lost. The mirror image
        pins the service and its notification after everything has finished.

        `seq` is stamped where the transition is DECIDED, under `_lock`, so it
        orders them by that rather than by which thread got here first. A stamp
        older than the last delivered describes a world that has already been
        superseded, and saying it would be a lie about the present.
        """
        if count is None or self._on_live_change is None:
            return
        # Held across the call, not just the compare: two sinks running
        # concurrently could otherwise interleave inside the Android runtime,
        # and `_delivered_seq` would say the newer one landed while the older
        # was still executing.
        with self._sink_lock:
            if seq <= self._delivered_seq:
                return
            self._delivered_seq = seq
            try:
                self._on_live_change(count)
            except Exception:                                # noqa: BLE001
                _log.exception("live-run callback failed")

    def retire(self, run_id: str) -> None:
        """Note that a run is no longer live, and announce it if it was the last.

        Called by the runner on every terminal path, including the pre-start
        release: a run reserved by a route that then refuses is never entered by
        the runner at all, and a demotion hung off the runner would leave the
        service pinned by a run that no longer exists.
        """
        with self._lock:
            self._live.discard(run_id)
            count = len(self._live) if not self._live else None
            self._live_seq += 1
            seq = self._live_seq
        self._fire_live(count, seq)

    def set_event_factory(self, factory: Callable[[], HandshakeEvent]) -> None:
        """Swap the factory used for runs published from here on.

        The lifespan calls this to install one that builds events on the event
        loop. Runs already published keep the events they were made with, which
        is correct: they were made before there was a loop to build on.
        """
        self._event_factory = factory

    def start_or_existing(self, subject: Subject, cls: RunClass, kind: str,
                          attempt_id: str | None, scene_identity: str | None,
                          labels: dict, adopt_terminal: bool = True,
                          also_precancelled: Subject | None = None,
                          review_generation: str | None = None,
                          ) -> tuple[Run, bool]:
        """Reserve a run, or hand back the one this attempt already made.

        ``labels`` is required, not optional: left defaultable, an
        implementation can omit it at every call site, pass every test, and ship
        notifications with no campaign or scene text -- a feature silently
        absent behind a green suite.

        All of it under one lock, get-or-create style. A check-then-act would
        hand two concurrent first callers different answers, which is precisely
        the double-send this exists to prevent.
        """
        # BEFORE the lock. The loop-backed factory blocks on a portal round
        # trip, and loop-side code (the reaper) takes this same lock -- so
        # constructing under it can deadlock the server: the handler holds the
        # lock waiting on the loop while the loop waits for the lock. Two spare
        # events on the adopt path is a cheap price for that not being possible.
        events = (self._event_factory(), self._event_factory())
        with self._lock:
            # FIRST, and inside the lock the move also holds: a check made in
            # `put_data_dir` and acted on afterwards leaves room for exactly
            # this reservation, and then the run's setup writes the player's
            # post into the old tree while its terminal write resolves the
            # campaign against the new one.
            if self._store_moves:
                raise StoreMovingError
            if attempt_id is not None:
                # Attempt ids come from clients, so they are only unique within
                # a subject -- two scenes may pick the same one.
                known = self._by_attempt.get((subject, attempt_id))
                existing = self._runs.get(known) if known else None
                # The identity has to match here too, not only in `get`. A stale
                # client retrying an old attempt id after the scene was deleted
                # and its id recycled would otherwise adopt the dead scene's run
                # and receive its frames.
                if existing is not None and self._owns(existing, subject, scene_identity) \
                        and (adopt_terminal or existing.state == "running"):
                    # Returned even when terminal, for a CLIENT's id: a client
                    # whose response was lost re-sends the same one and must
                    # adopt the original outcome rather than do the work twice.
                    #
                    # `adopt_terminal=False` is for an id the SERVER derived --
                    # from a proposal, say. That is only a dedupe key for
                    # concurrent duplicates, not a promise from anyone that this
                    # is the same logical request, so a later retry of a turn
                    # that FAILED has to be allowed to actually retry. Adopting
                    # there would make one crashed adjudication permanent.
                    return existing, False

            key = exclusion_key(subject, cls)
            if key is not None:
                holder_id = self._by_key.get(key)
                holder = self._runs.get(holder_id) if holder_id else None
                if holder is not None and holder.state == "running":
                    raise RunInFlightError(holder.id)

            run = Run(subject, cls, kind, attempt_id, scene_identity, labels,
                      events, review_generation=review_generation)
            self._runs[run.id] = run
            self._by_subject.setdefault(subject, []).append(run.id)
            if attempt_id is not None:
                self._by_attempt[(subject, attempt_id)] = run.id
                # Consumed HERE, under the acquisition that publishes the run,
                # and not by the caller afterwards. Two acquisitions left the
                # window this record exists to close: the cancel route looks up
                # and finds nothing, we publish and see no record, and only then
                # does the route write one -- which nothing will ever read. One
                # lock for each side makes the two total: either the record is
                # already here, or the cancel finds this run.
                # BOTH keys, and review caught why. A Stop that arrives before
                # the reservation is filed under the subject the CANCEL route
                # could build -- and when startup backfill skipped a contended
                # campaign, that scene has no identity yet, so the cancel filed
                # it under `UNRESOLVED` while `reserve_turn` goes on to MINT one
                # and looks under that. The recorded Stop was never consumed and
                # the provider ran for a turn the player had already stopped.
                #
                # Reconciled here rather than by minting on the cancel path: a
                # Stop should not have to write to the scene file to be heard.
                keys = [(subject, attempt_id)]
                if also_precancelled is not None:
                    keys.append((also_precancelled, attempt_id))
                if any(self._precancelled.pop(k, "miss") is None for k in keys):
                    run.cancel_requested = True
            if key is not None:
                self._by_key[key] = run.id
            self._live.add(run.id)
            # Announced at RESERVATION, not when the runner starts. The registry
            # goes live before the handler has built its prompt, and that setup
            # is not always fast -- context construction can reach semantic
            # recall. A phone locking during it would find the service
            # unpromoted and the process reclaimable before the runner ever
            # began, losing the turn in exactly the window this protects.
            crossed = len(self._live) if len(self._live) == 1 else None
            self._live_seq += 1
            seq = self._live_seq
        self._fire_live(crossed, seq)
        return run, True

    @staticmethod
    def _now() -> float:
        """The clock retention is measured on. Monotonic, never wall."""
        return time.monotonic()

    def _owns(self, run: Run | None, subject: Subject,
              identity: str | None) -> bool:
        """Whether ``subject`` (and, when given, ``identity``) owns this run.

        The subject alone is not enough for a scene. A scene deleted and
        replaced inside the retention window lands on the same ``sid`` -- ids
        are recycled by design -- so the replacement would otherwise be handed
        the dead scene's run and could read its frames or cancel it. The
        identity is exactly the thing that distinguishes them; callers that
        have it pass it.
        """
        if run is None or run.subject != subject:
            return False
        return identity is None or run.scene_identity == identity

    def get(self, run_id: str, subject: Subject,
            identity: str | None = None) -> Run | None:
        """The run, or ``None`` if this subject does not own it.

        The subject check is the isolation: a run id from another scene answers
        'gone', never another scene's state.
        """
        with self._lock:
            run = self._runs.get(run_id)
            return run if self._owns(run, subject, identity) else None

    def for_subject(self, subject: Subject,
                    identity: str | None = None) -> list[Run]:
        """Every run on this subject, live and terminal, oldest first.

        A collection rather than a pointer: drafts and background work declare
        no exclusion key, so they overlap legitimately, and a most-recent
        pointer would hide the first one with no discovery path left.
        """
        with self._lock:
            return [self._runs[rid] for rid in self._by_subject.get(subject, [])
                    if self._owns(self._runs.get(rid), subject, identity)]

    def cancel_or_precancel(self, subject: Subject, attempt_id: str,
                            identity: str | None = None) -> Run | None:
        """The run this attempt reserved, or -- if it has not yet -- a record
        that it was stopped, kept for the reservation to find.

        ONE acquisition, deliberately, and this is the whole point of the
        method. Asking and then recording is two, and a reservation fits
        between them: the lookup finds nothing, the run is published seeing no
        record, and the record is written afterwards for nobody. Doing both
        under one lock makes the orderings total -- either this runs first and
        the reservation consumes the record, or the reservation runs first and
        this finds the run.
        """
        with self._lock:
            known = self._by_attempt.get((subject, attempt_id))
            run = self._runs.get(known) if known else None
            if self._owns(run, subject, identity):
                return run
            self._precancelled[(subject, attempt_id)] = None
            while len(self._precancelled) > _MAX_PRECANCEL:
                self._precancelled.pop(next(iter(self._precancelled)))
            return None

    def for_attempt(self, subject: Subject, attempt_id: str | None,
                    identity: str | None = None) -> Run | None:
        """The run this attempt id already produced, if any. Read-only.

        `start_or_existing` does the same lookup, but a route cannot get that
        far without first passing its own preflight checks -- and a REPLAY must
        not be subject to them. A client that lost the response to a completed
        turn re-sends the same attempt id; if the LLM connection has been
        removed or re-keyed since, the preflight refuses with `missing_key` and
        the client is told a turn that actually landed did not, which is the
        exact ambiguity the attempt id exists to remove. No provider is needed
        to hand back an outcome that already exists.
        """
        if attempt_id is None:
            return None
        with self._lock:
            known = self._by_attempt.get((subject, attempt_id))
            run = self._runs.get(known) if known else None
            return run if self._owns(run, subject, identity) else None

    def reviews_for_generation(self, subject: Subject, generation: str) -> list[Run]:
        """Every run on this subject that belongs to one pending review.

        The list, not the newest: an absorb and a retry of one of its phases
        both carry the generation, and a Cancel means "stop preparing this
        review", not "stop the most recent thing". Terminal runs are included
        and harmless -- flagging one changes nothing, and filtering them here
        would need a second read of state the caller is about to act on anyway.
        """
        with self._lock:
            return [r for r in self._runs.values()
                    if r.subject == subject and r.cls == "review"
                    and r.review_generation == generation]

    def live_running_in(self, cid: str) -> Run | None:
        """Any live run anywhere in this campaign, for the operations that
        reshape a campaign rather than one scene.

        `repad` is the one: crossing 999 -> 1000 scenes renames EVERY scene in
        the campaign and repoints their sidecars, so every live turn in it
        loses the path it captured. Asking scene by scene cannot express that
        question, and refusing the explicit rename route never covered it.
        """
        with self._lock:
            for run in self._runs.values():
                if (run.state == "running" and run.subject[0] == "scene"
                        and run.subject[1] == cid):
                    return run
            return None

    def any_live(self) -> Run | None:
        """Any live run at all, for the operations that reshape the store the
        runs are writing INTO rather than one campaign inside it.

        `PUT /config/data-dir` is the one: it moves the global root, and a
        detached run resolves its campaign and scene against `store.home()`
        when it persists -- minutes after the request that started it. Moved
        underneath, that terminal write either fails the identity fence and
        discards a finished reply, or lands in a copied library while the
        player's post stays in the old one.
        """
        with self._lock:
            for run in self._runs.values():
                if run.state == "running":
                    return run
            return None

    @contextlib.contextmanager
    def hold_still(self):
        """Hold the store root still: no run may be reserved for this block.

        `any_live()` followed by the move is a check-then-act, and review
        caught it: the registry lock is released between the two, a send
        reserves in the gap, and the move proceeds with a run now live. That
        run's setup has already written into the old tree, so its terminal
        write either fails the identity fence -- discarding a finished reply --
        or lands in a copied library while the player's post stays behind in
        the original.

        So the refusal and the flag are set in ONE acquisition, and
        `start_or_existing` reads the flag inside that same lock. The block is
        short and takes no other lock: `set_data_dir` rewrites a pointer file.

        The count is decremented in a `finally`, because a move that raises must
        not leave the app unable to start a turn until it is restarted.

        A COUNT rather than a flag, and review caught the difference: two
        overlapping moves both got in -- the check only excludes running RUNS,
        not other movers -- and the first to finish cleared the flag while the
        second was still changing the pointer. A send reserving in that
        remainder is the very thing this exists to prevent, and it was reachable
        exactly when two moves were in flight. Nested holds are still exclusive
        against reservations for as long as any of them is open.
        """
        with self._lock:
            for run in self._runs.values():
                if run.state == "running":
                    raise RunInFlightError(run.id)
            self._store_moves += 1
        try:
            yield
        finally:
            with self._lock:
                self._store_moves -= 1

    def live_for_key(self, key: str | None) -> Run | None:
        """The running holder of an exclusion key, if there is one."""
        if key is None:
            return None
        with self._lock:
            holder_id = self._by_key.get(key)
            holder = self._runs.get(holder_id) if holder_id else None
            return holder if holder is not None and holder.state == "running" else None

    def reap(self, now: float | None = None) -> int:
        """Drop terminal runs older than the window. Returns how many went.

        Every index is cleared, not just ``_runs``: an entry left in
        ``_by_attempt`` would make a later send with the same attempt id adopt a
        record that no longer exists, and one left in ``_by_key`` would wedge
        the scene permanently -- neither visible through ``get``.
        """
        cutoff = (self._now() if now is None else now) - REAP_SECONDS
        with self._lock:
            dead = [r for r in self._runs.values()
                    if r.ended_monotonic is not None and r.ended_monotonic < cutoff]
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
                    # Only when it still points HERE. A recycled scene id can
                    # start a replacement under the same attempt id, and
                    # deleting its mapping would make a retry start the work
                    # again rather than adopt it -- the duplicate send this
                    # index exists to prevent. Same guard `_by_key` already had.
                    key_a = (run.subject, run.attempt_id)
                    if self._by_attempt.get(key_a) == run.id:
                        del self._by_attempt[key_a]
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


# --- routes -----------------------------------------------------------------
#
# Deliberately thin. Everything above this line is pure data with no FastAPI in
# it, and these handlers only resolve a subject, look a run up, and shape a
# response. Nothing here imports `streaming` or `scenes` -- the registry is
# reachable from the whole route layer, and a dependency in that direction
# would make the module graph cyclic (`test_import_guard`).

_log = logging.getLogger(__name__)

router = APIRouter()


def _scene_subject(cid: str, sid: str) -> tuple[Subject, str | None]:
    """The subject for a scene, plus the scene's current identity.

    The subject IS the identity (see `Subject`): a scene's `sid` moves on
    rename and is handed to the next scene on delete, so it cannot name a run
    that outlives the request that started it. Both are returned because
    callers pass the identity to `_owns` as well, where it stays a belt-and-
    braces check on a run that was indexed before this was true.
    """
    try:
        identity = scenes.scene_identity_strict(cid, sid)
    except CampaignNotFound as exc:
        # `scene_identity` reaches `campaign_root`, which refuses an id the
        # store cannot address. Unhandled that is a 500, and every id-carrying
        # route in this app is swept for exactly that.
        raise _gone("campaign_gone", "no such campaign") from exc
    except OSError as exc:
        # Could not READ the header. Retryable, and it must not fall through as
        # "no identity" -- see `UNRESOLVED` below for what that would allow.
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": f"the scene could not be read: {exc}"}) from exc
    # STRICT, and never `None` past this point. `_owns` reads `identity=None` as
    # "caller did not ask", i.e. a wildcard -- correct for a subject-wide sweep,
    # catastrophic here: a replacement scene that recycled a retained run's
    # `sid` and has no identity of its own would match that run and be allowed
    # to read its frames or cancel it. A scene with no identity has no runs
    # either (`reserve_turn` mints one before it publishes anything), so a
    # sentinel that matches nothing is the honest answer rather than a refusal
    # -- discovery on such a scene still answers "no run" instead of an error.
    resolved = identity or UNRESOLVED
    return ("scene", cid, resolved), resolved


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


def _gone(kind: str, detail: str) -> HTTPException:
    """A 404 whose body is FLAT.

    `main` installs an exception handler that emits a dict `detail` as the
    response body directly, so `kind` lands at the top level -- which is how
    every other structured error in this tree is asserted.
    """
    return HTTPException(status_code=404, detail={"kind": kind, "detail": detail})


def _start_cursor(from_: int, run: Run) -> int:
    """Where a replay should actually begin.

    A cursor past the buffer is clamped to the tail. Held literally on a LIVE
    run it would exclude every frame that arrives next -- their indexes are
    LOWER than it -- so the client would tail to the end of the run and receive
    nothing at all, which is worse than the reconnect it was attempting.

    Its own function because that is the only way to test it: through the route
    the difference is invisible on a terminal run (both answer empty) and needs
    a race to observe on a live one.
    """
    return min(from_, len(run.frames))


def run_payload(run: Run) -> dict:
    """What a client needs to decide what to do, and nothing it does not.

    `next_index` rather than the frames themselves: a poll is for state, and a
    terminal run's buffer can be the whole reply. The client asks for frames
    over the stream route, from the cursor it kept.
    """
    return {
        "id": run.id,
        "state": run.state,
        "kind": run.kind,
        "cls": run.cls,
        "attempt_id": run.attempt_id,
        # Which pending review a `review` run is preparing, so a client that
        # discovered a live absorb through `GET .../run` can address Cancel to
        # it -- `DELETE .../pending-review` names the generation, not the run.
        # `None` for every other class.
        "review_generation": run.review_generation,
        "labels": run.labels,
        "next_index": len(run.frames),
        "error": run.error,
        "result": run.result,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
    }


def _resolve(app, cid: str, sid: str, run_id: str) -> Run:
    subject, identity = _scene_subject(cid, sid)
    run = app.state.runs.get(run_id, subject, identity=identity)
    if run is None:
        raise _gone("run_gone", "no such run for this scene")
    return run


@router.get("/campaigns/{cid}/scene-by-identity")
def get_scene_by_identity(cid: str, identity: str) -> dict:
    """The scene an identity names right now, or 404.

    What a notification tap resolves through: the intent carries the identity
    precisely because a `sid` goes stale on rename, so without this the tap
    could only open a stale route or fall back to the campaign.

    The identity is a QUERY parameter, and the path deliberately does not sit
    under `/scenes/`. `/scenes/by-identity/{identity}` puts a literal where a
    `sid` goes and a parameter where a literal goes, so it crosses EVERY
    `/scenes/{sid}/<name>` route -- a dozen ambiguous pairs, each needing its
    own entry in `test_route_order.CROSSING_PAIRS`. Twelve exemptions to add
    one lookup is the guard reporting that the URL is shaped wrong, not that
    the guard is in the way.
    """
    try:
        sid = scenes.find_by_identity(cid, identity)
    except CampaignNotFound as exc:
        raise _gone("campaign_gone", "no such campaign") from exc
    except OSError as exc:
        # The scan could not read every candidate, so it cannot rule the
        # identity out. A 404 here tells a notification tap the scene is gone
        # and sends it back to the campaign, which is a wrong answer the user
        # sees; contention is retryable and is what this actually is.
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": str(exc)}) from exc
    if sid is None:
        raise _gone("scene_gone", "no scene carries that identity")
    return {"id": sid}


@router.get("/campaigns/{cid}/scenes/{sid}/run")
def get_scene_run(cid: str, sid: str, request: Request,
                  attempt: str | None = None) -> dict:
    """Discovery. With `attempt`, an exact match; without, the newest run.

    "Newest" is a decision, not an implementation detail. The subject index
    routinely holds several -- a terminal run stays readable for the whole
    retention window while a new one is already live -- and answering with the
    older, terminal one makes the client settle and miss the live reply
    entirely, which is the failure this endpoint exists to prevent.

    A scene with no runs answers `{"run": None}`, not 404: that is the ordinary
    case on every mount, and an error there would make every quiet scene look
    broken.
    """
    subject, identity = _scene_subject(cid, sid)
    found = request.app.state.runs.for_subject(subject, identity=identity)
    if attempt is not None:
        found = [r for r in found if r.attempt_id == attempt]
    if not found:
        return {"run": None}
    # Reservation order, NOT `max(started_at)`. `for_subject` preserves the
    # order runs were indexed in, and that order is real; `started_at` is a wall
    # clock, so a backward correction between two reservations can give the
    # newer live run a LOWER stamp -- and answering with the older terminal one
    # makes the client settle and miss the active reply, which is the single
    # failure this endpoint exists to prevent.
    return {"run": run_payload(found[-1])}


@router.get("/campaigns/{cid}/scenes/{sid}/runs/{run_id}")
def get_run(cid: str, sid: str, run_id: str, request: Request) -> dict:
    return {"run": run_payload(_resolve(request.app, cid, sid, run_id))}


@router.post("/campaigns/{cid}/scenes/{sid}/runs/{run_id}/cancel")
def post_run_cancel(cid: str, sid: str, run_id: str, request: Request) -> dict:
    """Ask a run to stop, and answer once it really has.

    A terminal run is NOT an error here: Stop races the reply landing, and
    reporting a failure for a turn that succeeded a moment earlier is worse
    than doing nothing.
    """
    run = _resolve(request.app, cid, sid, run_id)
    if run.state == "running":
        runner.cancel(request.app, run)
        # Wait for the abort hook, not just for the cancel to be delivered:
        # answering earlier lets a fast re-send race the partial-persist, which
        # is the ordering the handshake exists to guarantee.
        run.terminal.wait(timeout=CANCEL_TIMEOUT_SECONDS)
    return {"run": run_payload(run)}


@router.post("/campaigns/{cid}/scenes/{sid}/attempt-cancel")
def post_attempt_cancel(cid: str, sid: str, request: Request,
                        attempt: str = Query(...)) -> dict:
    """Stop the turn this attempt id names, whether or not it has a run yet.

    What Stop calls when discovery came back empty. The POST it is stopping may
    have been accepted and then blocked in synchronous setup -- the campaign
    lock is held by something else -- so "no run for this attempt" does not mean
    "nothing is going to happen": the route can still reserve and detach a turn
    after the lookup. Recording the cancel against the ATTEMPT closes that,
    because the reservation consumes it (`reserve_turn`).

    Idempotent, and safe to call for an attempt that never reserves: the record
    is capped and forgotten oldest-first.

    The attempt is a QUERY parameter, not a path segment, because the header
    contract accepts a client's id VERBATIM -- `X-Grimoire-Attempt: client/42`
    is legal, and percent-encoding does not save it: the ASGI router matches on
    the decoded path, so the slash splits the segment and the request reaches no
    route at all. The very clients most likely to use a structured id would be
    the ones unable to stop their own turns.
    """
    attempt_id = attempt
    subject, identity = _scene_subject(cid, sid)
    # One call, because looking up and then recording is two acquisitions with a
    # reservation-shaped gap between them -- see `cancel_or_precancel`.
    run = request.app.state.runs.cancel_or_precancel(subject, attempt_id, identity)
    if run is None:
        return {"run": None}
    if run.state == "running":
        runner.cancel(request.app, run)
        run.terminal.wait(timeout=CANCEL_TIMEOUT_SECONDS)
    return {"run": run_payload(run)}


@router.get("/campaigns/{cid}/scenes/{sid}/attempt-state")
def get_attempt_state(cid: str, sid: str, request: Request,
                      attempt: str = Query(...)) -> dict:
    """Whether this attempt's post is still in the scene, and its run if one
    is still known.

    The question a client asks when it comes back after the run record expired.
    Every other route here reads the in-memory registry, which by definition no
    longer has a reaped run -- so without this the client cannot ask the only
    decisive question at all, and #95's ambiguity returns the moment
    `REAP_SECONDS` passes.

    `retained` is the durable half and comes from `store.attempts`. It answers
    False for everything unresolved -- no identity, no record, an unreadable
    file -- because the caller's rule is that ambiguity keeps the player's
    text: wrong this way costs one duplicate they can see and delete, wrong the
    other way costs them their words with no trace.

    Scene-scoped like every route here, so an attempt belonging to another
    scene answers about nothing rather than about that scene.
    """
    subject, identity = _scene_subject(cid, sid)
    run = request.app.state.runs.for_attempt(subject, attempt, identity)
    return {
        "attempt": attempt,
        "retained": scenes_attempts.retained(
            cid, None if identity == UNRESOLVED else identity, attempt),
        "run": run_payload(run) if run is not None else None,
    }


@router.get("/campaigns/{cid}/scenes/{sid}/runs/{run_id}/stream")
def get_run_stream(cid: str, sid: str, run_id: str, request: Request,
                   from_: int = Query(0, alias="from")):
    """Replay this run's frames from `from_`, INCLUSIVE.

    A client that consumed through N asks for N+1. Exclusive would drop one
    frame per reconnect -- invisible until someone reads the text and finds a
    word missing mid-sentence.

    Every frame goes out with its absolute index on an SSE `id:` line. The
    client cannot derive that by counting what it decoded, because a comment
    frame (the heartbeat) carries no event at all -- so a per-event cursor lags
    the server's position and `consumed + 1` replays text already rendered.
    """
    # 400 rather than FastAPI's 422: `scenes.py:300` already answers a bad
    # `limit`/`before` this way, and one shape of bad-parameter error is easier
    # for a client to handle than two.
    if from_ < 0:
        raise HTTPException(status_code=400, detail="from must not be negative")
    run = _resolve(request.app, cid, sid, run_id)
    return tail_response(run, from_)


def tail_response(run: Run, from_: int = 0, lead: str | None = None):
    """A response that replays `run` from `from_` and then follows it.

    Shared by the reconnect route and by every producing route, so a client
    reading a turn as it happens and a client picking one up after a
    disconnection are served by exactly the same code -- there is no "live"
    path that could drift from the "replay" path.

    `lead` is emitted before any buffered frame and is NOT stored in the
    buffer: it carries the run's identity to the caller that just created it,
    and a client reconnecting later already knows the id it is asking about.
    """

    async def event_stream():
        """Replay what is buffered, then TAIL until the run is terminal.

        A negative `from` is rejected by the route signature rather than read as
    zero: silently widening a malformed cursor replays the whole buffer and
    duplicates a reply the client has already rendered.

    A one-shot replay would answer the buffer and reach EOF -- so a client
        attaching to a live run (the ordinary case on reconnect, and always the
        case when `from` is already at the tail) would disconnect before the
        run finished and never see the rest of the reply. That is the whole
        foreground half of this feature.

        Polling rather than a push signal because the frames arrive from a
        different thread and the handshake events are set-once; the interval is
        well under a human's perception of "live", and it costs one list slice
        per tick on an idle run.
        """
        if lead is not None:
            yield lead
        cursor = _start_cursor(from_, run)
        while True:
            # Terminal is read BEFORE draining, and that order is the whole
            # correctness argument. Any frame appended before the run went
            # terminal is necessarily visible to the drain that follows this
            # read -- so the loop cannot exit holding undelivered frames, and
            # the last words of a reply cannot be truncated. Draining first and
            # then checking would leave a window between the two, which a
            # second pass only narrows and no test can pin down.
            done = run.terminal.is_set() or run.state != "running"
            for frame in run.frames_since(cursor):
                cursor = frame["index"] + 1
                yield f"id: {frame['index']}\n{frame['raw']}"
            if done:
                return
            await anyio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def sse(payload: dict) -> str:
    """One SSE data frame. Local rather than imported from `streaming`, which
    this module is forbidden to depend on (`test_import_guard`)."""
    return f"data: {json.dumps(payload)}\n\n"


def start_detached(app, run: Run, producer, outcome=None,
                   on_unstarted=None) -> None:
    """Run `producer` for `run`, decoupled from the request that asked for it.

    `producer` is a zero-arg callable returning an async iterator of raw SSE
    frames -- the existing stream generators, unchanged. Every frame it yields
    is buffered on the run, which is what makes the work survive the socket:
    the response tails that buffer, and so does a reconnect ten minutes later.

    `outcome` is a zero-arg callable read AFTER the producer is exhausted; it
    returns what the producer decided about itself, or `None` to let the runner
    infer success. It exists because "did not raise" is not "succeeded" here:
    the stream generators handle an upstream `LLMError`, a contended finalize,
    and a scene that changed underneath them by emitting an error frame and
    returning NORMALLY. Without this the runner marked every one of those
    `landed`, so polling -- and the notification that will read it -- reported
    a delivered reply for a turn that failed. Passed as a callable rather than
    a value because it is only decided while the producer runs, and as a
    callable rather than the producer's own object because `runs` may not
    import `streaming` (`test_import_guard`).

    `on_unstarted` undoes destructive setup the ROUTE did on the one path where
    the producer is never entered -- a Stop that landed while the route was
    still in synchronous setup. Regenerate is the caller that needs it; see
    `runner._guarded`.
    """

    async def pump():
        async for frame in producer():
            run.append_frame(frame)
        return None if outcome is None else outcome()

    # AFTER the handoff, not before. `runner.start` can raise -- the lifespan
    # portal is gone, or closing as the request hands off -- and `reservation`
    # deliberately skips a started run, so marking it first left the record
    # permanently `running` with no task, no terminal event, and its scene's
    # exclusion key held for the rest of the process. Same thread, and nothing
    # reads the flag until this route returns, so there is no window here.
    runner.start(app, run, pump, on_unstarted)
    run.started = True


def answer_without_running(app, run: Run, frames: list[str],
                           state: RunState = "landed"):
    """End a reserved run that answered on its own, and hand back its answer.

    For the exits that produce a complete response without generating anything
    -- an already-narrated proposal, a check that would not resolve. Returning
    those frames directly would leave the run holding an empty buffer, and a
    duplicate request adopting it by attempt id (which is the whole point of
    the id) would then be handed a stream containing nothing at all. Buffering
    them first makes the run the single record of what this request answered,
    however it answered.

    `state` is what that answer WAS, and defaulting it to `landed` for every
    caller was wrong: a check that would not resolve buffers an `error` frame
    and generated nothing, so a client polling the record read success while
    the stream said failure -- and on Android the completion notification said
    the turn had replied. The frames and the run's state have to agree, because
    they are read by different clients and neither knows about the other.
    """
    for frame in frames:
        run.append_frame(frame)
    release_before_start(app, run, state)
    return tail_response(run, 0, lead=lead_frame(run))


@contextlib.contextmanager
def reservation(app, run: Run):
    """Guarantee that a reserved run reaches a terminal state.

    `reserve_turn` publishes the run before the route does any work, on purpose:
    that is what makes a second send refusable rather than merely discouraged.
    The cost is that every path out of the route between the reservation and
    `start_detached` now owns a live, discoverable run -- and a route that
    returns or raises in that window used to leave it `running` forever. Such a
    run is never eligible for reaping (only terminal runs are), so the scene it
    holds answers `run_in_flight` for the life of the process: one bad request
    and the scene is unusable until restart.

    Wrapping the window instead of auditing each exit is deliberate. The
    branches are the thing that changes as routes are migrated, and a guard that
    has to be remembered at each new `return` is a guard that will be missed.
    """
    try:
        yield
    except HTTPException as exc:
        # A refusal the route chose -- 400 for an empty retry, 404 for a scene
        # that vanished. The client is being told; the run has to agree, or the
        # composer stays locked against a turn that never began.
        _release_unstarted(app, run, "failed", {
            "kind": "refused", "detail": _detail_text(exc)})
        raise
    except BaseException as exc:
        _release_unstarted(app, run, "failed", {
            "kind": "run_failed", "detail": str(exc) or type(exc).__name__})
        raise
    # No exception, no producer: the route answered on its own. Nothing will
    # ever set this run's events, so anyone already polling or cancelling it
    # would wait forever.
    _release_unstarted(app, run, "landed")


def _release_unstarted(app, run: Run, state: RunState, error: dict | None = None) -> None:
    """Terminate `run` -- but ONLY if nothing is driving it.

    The `started` guard is on the failure arms too, not just the clean exit, and
    that is not defensive noise: `start_detached` succeeds and the route then
    builds its response, so a throw between the two would otherwise mark a run
    `failed` and set `terminal` while its producer is still writing frames into
    it. Every subscriber would stop reading mid-reply and be told the turn
    failed, while the turn went on to persist perfectly well -- the worst of the
    outcomes this whole guard exists to prevent, arrived at by the guard itself.
    """
    if run.started or run.state != "running":
        return
    release_before_start(app, run, state, error)


def _detail_text(exc: HTTPException) -> str:
    """An HTTPException's message as a string, however it was built. Routes
    raise both plain strings and the dict shape the 409 handler unwraps."""
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("detail") or detail.get("kind") or detail)
    return str(detail)


def release_before_start(app, run: Run, state: RunState, error: dict | None = None) -> None:
    """`runner.release_before_start`, re-exported so routes need one import."""
    runner.release_before_start(app, run, state, error)


def lead_frame(run: Run) -> str:
    """The frame every producing route sends first.

    Before any delta, and before anything can fail: a client whose connection
    dies immediately still has to be able to find its run, or the send is
    unaddressable and "did my turn land?" has no answer -- which is the whole
    ambiguity this feature exists to remove.
    """
    return sse({"run": {"id": run.id, "attempt_id": run.attempt_id,
                        "state": run.state, "next_index": len(run.frames)}})


def replay_attempt(app, cid: str, sid: str, attempt_id: str | None):
    """The buffered response for an attempt that already ran, or `None`.

    Called at the very top of a producing route, ahead of its preflight checks
    -- see the call site for why that order matters. Resolves the subject the
    same way the run routes do, so a replacement scene that recycled the `sid`
    cannot replay the dead scene's turn.

    Answers `None` for anything it cannot resolve rather than raising: this runs
    before the route's own validation, and a malformed campaign or scene id must
    still get that route's 404, not a different one from here.
    """
    if not attempt_id:
        return None
    try:
        subject, identity = _scene_subject(cid, sid)
    except HTTPException:
        return None
    run = app.state.runs.for_attempt(subject, attempt_id, identity)
    if run is None:
        return None
    return tail_response(run, 0, lead=lead_frame(run))


def require_scene_free(app, cid: str, sid: str) -> None:
    """Refuse a change to the SHAPE of a scene while a run is holding it.

    A `turn` or a `review`, and one check covers both: `exclusion_key` is built
    from the subject and both classes are in `_EXCLUSIVE`, so they name the
    same key and a scene can hold at most one of either. The rule is one
    sentence -- while a turn or a review holds a scene, that scene's shape does
    not change -- and a review is the case that makes it urgent rather than
    theoretical: an absorb is minutes long, and an edit, a retcon or a cut
    landing under one moves the transcript it was built from.

    Detaching a turn is what makes this necessary. A rename mints a new `sid`
    (the slug is part of it), and the run and every one of its persistence hooks
    hold the old one -- so after a rename the identity fence looks for a scene
    that is no longer at that path, decides it is gone, and discards a reply the
    provider may have spent minutes producing. That was survivable when a turn
    died with its socket, because the window was the length of one request;
    it is minutes now, and the rename button is one click away in another tab.

    Deliberately a refusal rather than a repoint. Following the scene means
    updating a live run's captured `sid` from outside it, which is the kind of
    change the fence exists to make impossible; refusing costs the user a few
    seconds and the composer already tells them a turn is in flight.
    """
    identity = scenes.scene_identity_strict(cid, sid)
    if identity is None:
        return                       # no identity, so no run can name this scene
    live = app.state.runs.live_for_key(exclusion_key(("scene", cid, identity), "turn"))
    if live is not None:
        # `scene_busy`, not `run_in_flight`. They are different refusals and the
        # client acts on them differently: `run_in_flight` answers "you cannot
        # start another turn here", which resolves itself when this one lands;
        # this one answers "you cannot change the SHAPE of this scene while a
        # turn is reading it", which is about renaming, editing and cutting.
        raise HTTPException(status_code=409, detail={
            "kind": "scene_busy", "run_id": live.id,
            "detail": "a turn or review is running on this scene; stop it first"})


@contextlib.contextmanager
def scene_held_free(app, cid: str, sid: str):
    """Hold the campaign lock across BOTH the scene-free check and the mutation
    it guards.

    Every route that changes a scene's SHAPE goes through this -- rename,
    delete, message edit, message cut, retcon, replay begin/accept/cancel --
    because detaching a turn made all of them concurrent with generation. The
    doors that already open a campaign-lock hold of their own (alternate
    promotion, the manual roll, the manual check) call `require_scene_free`
    inside it instead: the same guarantee, without a second acquisition.

    A turn composes its prompt from the transcript as it stood and finalizes
    against the transcript as it is, so a post edited or cut underneath it
    produces a reply answering a question nobody asked any more, appended to
    history that has moved.

    Checking and then mutating is two steps, and a reservation fits between
    them: the check sees no run, a send reserves and detaches, and the rename
    lands anyway -- so the turn's fence later finds its scene gone and discards
    a finished reply. `reserve_turn` takes the same campaign lock across its
    identity capture and its reservation, which is what makes holding it here
    actually exclude one rather than merely narrow the gap before one.

    Lock order is campaign-then-registry on both sides (`live_for_key` and
    `start_or_existing` take the registry lock inside a campaign-lock hold, and
    nothing takes them the other way round), so this cannot invert.
    """
    with store.locks.campaign_lock(cid):
        require_scene_free(app, cid, sid)
        yield


@contextlib.contextmanager
def store_held_still(app):
    """Refuse an operation that moves the STORE ROOT while anything is running,
    and keep it refused for the whole of that operation.

    Detaching a turn is what makes this reachable: the run survives navigation,
    so the player can leave the scene, open Configuration and change the storage
    location while their turn is still generating. Every earlier version of this
    hazard was impossible -- a turn died with its request, and its request held
    the page.

    Campaign-agnostic, unlike `require_campaign_free`: the root is global, so a
    run in ANY campaign is a run that would be persisted into the wrong tree.

    A CONTEXT MANAGER and not a check, which is the correction review asked
    for: see `RunRegistry.hold_still` for the window a bare check leaves open.
    """
    try:
        with app.state.runs.hold_still():
            yield
    except RunInFlightError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "runs_in_flight", "run_id": exc.run_id,
            "detail": "a turn is still generating; wait for it or stop it before "
                      "moving the storage location"}) from exc


def require_campaign_free(app, cid: str) -> None:
    """Refuse an operation that reshapes EVERY scene in a campaign while any of
    them is generating -- see `RunRegistry.live_running_in`."""
    live = app.state.runs.live_running_in(cid)
    if live is not None:
        raise HTTPException(status_code=409, detail={
            "kind": "scene_busy", "run_id": live.id,
            "detail": "a turn is running in this campaign; stop it first"})


def reserve_turn(app, cid: str, sid: str, kind: str,
                 attempt_id: str | None,
                 adopt_terminal: bool = True) -> tuple[Run, bool]:
    """Reserve a `turn` run for this scene, or raise 409 if one is in flight.

    `attempt_id` comes from the `X-Grimoire-Attempt` header. Absent or
    malformed, one is generated: older clients and `curl` keep working, they
    simply get no idempotency, which is what they have today. Present, it is
    used verbatim -- the server never rewrites it, because the client has
    already stored it and will ask about it by that name.
    """
    return _reserve(app, cid, sid, "turn", kind,
                    attempt_id or uuid.uuid4().hex,
                    adopt_terminal=adopt_terminal)


def reserve_review(app, cid: str, sid: str, kind: str,
                   generation: str) -> Run:
    """Reserve a `review` run for this scene, or raise 409 if one is in flight.

    `review` shares its exclusion key with `turn` (`exclusion_key` builds it
    from the subject, and `_EXCLUSIVE` holds both classes), so an absorb and a
    chat cannot hold one scene between them -- and every route that changes the
    scene's SHAPE is refused for the whole of a review, not just the whole of a
    turn. That is not decoration: an edit, a retcon or a cut landing under a
    ten-minute absorb would move the transcript the review is being built from,
    and the watermark would then refuse to save it -- after the entire budget
    had been spent, which is the most expensive way to discover a race that
    excluding prevents for free.

    **Reserved BEFORE the snapshot, which is why this returns before one is
    taken.** Snapshot-then-reserve leaves a gap in which a fast chat turn can
    reserve, append, finish and release, after which the absorb is accepted
    against a transcript that has already moved.

    No attempt id: a review carries no player text, so there is nothing for
    idempotency to protect, and the exclusion key already makes a duplicate
    POST a refusal rather than a second run. A client that lost the 202 finds
    its run through `GET .../run`.
    """
    run, _ = _reserve(app, cid, sid, "review", kind, None,
                      review_generation=generation)
    return run


def _reserve(app, cid: str, sid: str, cls: RunClass, kind: str,
             attempt_id: str | None, adopt_terminal: bool = True,
             review_generation: str | None = None) -> tuple[Run, bool]:
    """The shared half of `reserve_turn` and `reserve_review`.

    The scene's identity is captured here, via `ensure_identity` rather than a
    plain read: a campaign whose lock was contended at startup still has
    identity-less scenes, and capturing `None` would make the publish fence
    compare None with None -- which always matches, defeating the fence in
    exactly the case it exists for.
    """
    try:
        identity = scenes.ensure_identity(cid, sid)
    except CampaignNotFound as exc:
        raise _gone("campaign_gone", "no such campaign") from exc
    except scenes.SceneNotFound as exc:
        # `_require_scene` passed and then another request deleted or renamed
        # the scene before this took the campaign lock. An ordinary concurrent
        # mutation, and every other scene route answers it with a 404; leaving
        # it to fall through made a send 500 instead.
        raise _gone("scene_gone", "scene not found") from exc
    except OSError as exc:
        # The scene file could not be read or written just now -- a sync client
        # mid-write, a sharing violation (`identity.UnreadableError`), a device
        # that went away. `_require_scene` read this same file a moment ago, so
        # this is a transient condition and not a bad request; reported as the
        # store contention it is, which is a kind the client already retries,
        # rather than as the 500 an unhandled OSError would give it.
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": f"the scene could not be read: {exc}"}) from exc
    labels = {"campaign": _campaign_label(cid), "scene": _scene_label(cid, sid)}
    subject: Subject = ("scene", cid, identity)
    try:
        # Under the campaign lock, which is what lets `scene_held_free` exclude
        # a reservation rather than merely narrow the window before one. The
        # identity capture above takes the same lock (`ensure_identity` is
        # serialized on it) and it is reentrant, so this costs nothing.
        with store.locks.campaign_lock(cid):
            run, fresh = app.state.runs.start_or_existing(
                subject, cls, kind, attempt_id, identity, labels,
                adopt_terminal=adopt_terminal,
                # The subject a Stop would have built for this scene BEFORE
                # `ensure_identity` above minted one. Only different when the
                # startup backfill skipped this campaign, which is exactly when
                # a scene is still identity-less.
                also_precancelled=("scene", cid, UNRESOLVED),
                review_generation=review_generation)
    except RunInFlightError as exc:
        raise HTTPException(status_code=409, detail={
            "kind": "run_in_flight", "run_id": exc.run_id,
            "detail": "a turn or review is already running on this scene"}) from exc
    except StoreMovingError as exc:
        # Distinct from `run_in_flight`, which resolves when somebody else's
        # turn finishes. This one resolves in milliseconds and the same send
        # will work, so the client is told to retry rather than to wait.
        raise HTTPException(status_code=409, detail={
            "kind": "busy",
            "detail": "the storage location is being changed; try again"}) from exc
    # A Stop that arrived while this route was still in setup is already on the
    # run: `start_or_existing` consumes the record under the same lock that
    # publishes it. `runner` reads the flag when it installs the cancel scope,
    # so the turn ends without ever reaching a provider.
    return run, fresh


def cancel_review(app, cid: str, sid: str, generation: str) -> list[Run]:
    """Flag every run preparing this review as cancelled. Returns them.

    MUST be called inside the campaign-lock hold that then deletes the record,
    and BEFORE the delete. Split apart, the delete lands, the run publishes,
    and the review the player just dismissed comes back minutes later.

    Named by GENERATION rather than by run id or by recency, because neither of
    the obvious readings works: the stored payload names no producer, and
    "the scene's newest run" is as likely to be an unrelated live chat.

    Answers an empty list for a scene whose identity cannot be resolved rather
    than raising: the caller is deleting a record, and a Cancel that cannot
    find a run still has a record to remove.

    The runs come back so the caller can WAIT for them, which it must do
    outside this lock -- see `await_reviews_stopped`.
    """
    try:
        subject, _ = _scene_subject(cid, sid)
    except HTTPException:
        return []
    flagged = app.state.runs.reviews_for_generation(subject, generation)
    for run in flagged:
        run.review_cancelled = True
    return flagged


def await_reviews_stopped(flagged: list[Run]) -> int:
    """Wait for flagged review runs to reach a terminal state. Returns how many
    were still live when asked.

    **Outside the campaign lock, always.** A run being cancelled reaches its
    terminal persist through `campaign_lock(cid)`; waiting for it while holding
    that lock is a deadlock with a thirty-second fuse.

    Waited on rather than left to unwind on its own, because the caller's very
    next act is usually to start a *fresh* absorb -- and `review` holds the
    scene's exclusion key exactly as a turn does, so a retry still unwinding
    would refuse it with `run_in_flight`. The phases notice the flag on their
    own abandonment poll, so this is normally well under a second; the bound is
    for a provider that will not unwind, which must not hold a worker forever.
    """
    live = [r for r in flagged if r.state == "running"]
    for run in live:
        run.terminal.wait(timeout=CANCEL_TIMEOUT_SECONDS)
    return len(live)


def start_computing(app, run: Run, work) -> None:
    """Run `work` detached, for a class whose value is a payload and not frames.

    `work` is a zero-arg callable returning a coroutine that returns the run's
    outcome -- `{"state": ..., "result"?: ..., "error"?: ...}` -- exactly what
    `runner._guarded` already understands from a producer's `outcome`.

    No frame buffer, and that is the whole difference from `start_detached`:
    End Scene is not a streaming view, so there is nothing to tail and a client
    polls `GET .../runs/{id}` instead. Buffering an empty stream for it would
    give a reconnecting client an SSE response that says nothing.

    `run.started` is set AFTER the handoff for `start_detached`'s reason:
    `runner.start` can raise, and `reservation` deliberately skips a started
    run, so marking first would leave the record permanently `running` with no
    task and its scene's exclusion key held for the rest of the process.
    """
    runner.start(app, run, work)
    run.started = True


def _campaign_label(cid: str) -> str:
    """The campaign's display name, captured at start.

    Read now rather than at terminal: the notification has to name the scene a
    turn belonged to even when that scene has since been deleted, which is
    exactly the case the error notification exists to report.
    """
    try:
        return campaigns_read.read_campaign(cid)["meta"].get("title", cid)
    except Exception:                                        # noqa: BLE001
        return cid


def _scene_label(cid: str, sid: str) -> str:
    try:
        return scenes.read_scene_meta(cid, sid).get("title", sid)
    except Exception:                                        # noqa: BLE001
        return sid
