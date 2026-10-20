"""Per-application run indexes and exclusion holds share one registry lock."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from ...store import paths, proclock
from . import _contracts, _maintenance_claims, _record
from ._router import router as router


class RunRegistry:
    """Every run this process knows about, and the indexes to find one.

    Addressed by id, never by ``(cid, sid)``. A subscriber that resolved a frame
    stream by scene could, between one run ending and the next starting, attach
    a view showing scene B to frames produced for scene A.
    """

    def __init__(self, event_factory: Callable[[], _contracts.HandshakeEvent] | None = None) -> None:
        self._event_factory = event_factory or _contracts._PlainEvent
        self._lock = threading.Lock()
        self._runs: dict[str, _record.Run] = {}
        self._by_subject: dict[_contracts.Subject, list[str]] = {}
        self._by_attempt: dict[tuple[_contracts.Subject, str], str] = {}
        # Stops that arrived before their attempt had a run. Insertion-ordered
        # so the oldest can be dropped when the cap is reached -- see
        # `cancel_or_precancel`. The values are unused; only membership matters.
        self._precancelled: dict[tuple[_contracts.Subject, str], None] = {}
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
        # How many tree operations (fork, delete, export, the scheduled backup)
        # are holding the store against maintenance. A count for the same reason
        # `_store_moves` is one: they overlap. See `exclude_maintenance`.
        self._maintenance_holds = 0
        # The same process lock maintenance takes, retained until the last tree
        # operation on that root exits (#479). A lock PATH is the key, because
        # proclock identifies root aliases by their directory identity. Counts
        # permit nesting and overlapping operations without reacquiring our own
        # OS lock; separate registries/processes must acquire it for themselves.
        # Like its existing thread locks and live runs, this ownership belongs
        # to the app process; inheriting a registry across os.fork is unsupported.
        self._maintenance_locks: dict[Path, tuple[int, int]] = {}
        # How many cache-sync batches are running (spec 05 section 4.2): a
        # write-through warm-up or an API sync, pinned to the root it started
        # on. A count, for `_store_moves`' reason. Held only for the length of
        # a batch, never while the write-through queue merely holds paths, so
        # an idle queue never refuses a data-dir move. See `sync_hold`.
        self._sync_holds = 0
        #: Runs that ended with a tool thread still reading (`pin_root`): the
        #: root stays still for them, as for a live run, until it returns.
        self._root_pins: dict[str, int] = {}
        self._by_key: dict[str, str] = {}
        # Records a trigger asked a campaign's reconcile to look at while one
        # was already live, keyed by subject. Here, on the per-app registry and
        # under its lock, and never at module scope: a `TestClient` builds an
        # app per test and the suite reuses cids, so module state would hand
        # one test's leftover refs to the next test's first run.
        self._pending_touched: dict[_contracts.Subject, set[str]] = {}

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
                _contracts._log.exception("live-run callback failed")

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

    def set_event_factory(self, factory: Callable[[], _contracts.HandshakeEvent]) -> None:
        """Swap the factory used for runs published from here on.

        The lifespan calls this to install one that builds events on the event
        loop. Runs already published keep the events they were made with, which
        is correct: they were made before there was a loop to build on.
        """
        self._event_factory = factory

    def start_or_existing(self, subject: _contracts.Subject, cls: _contracts.RunClass, kind: str,
                          attempt_id: str | None, scene_identity: str | None,
                          labels: dict, adopt_terminal: bool = True,
                          also_precancelled: _contracts.Subject | None = None,
                          review_generation: str | None = None,
                          single_live: bool = False,
                          refuse_live: bool = False,
                          single_match: Callable[[_record.Run], bool] | None = None,
                          ) -> tuple[_record.Run, bool]:
        """Reserve a run, or hand back the one this attempt already made.

        ``labels`` is required, not optional: left defaultable, an
        implementation can omit it at every call site, pass every test, and ship
        notifications with no campaign or scene text -- a feature silently
        absent behind a green suite.

        All of it under one lock, get-or-create style. A check-then-act would
        hand two concurrent first callers different answers, which is precisely
        the double-send this exists to prevent.

        ``single_live`` makes a keyless class adopt the live run of the same
        class and kind on the subject instead of starting a second one: one
        live reconcile per campaign, whether End Scene or Refresh asked first.
        It is a scan under this lock rather than an exclusion key because the
        loser is not refused -- it is handed the running sweep, under its own
        attempt id (`_adopt`). With ``single_match`` the live run is adopted
        only when it matches -- a run about the same thing -- and otherwise
        refuses this one with `RunInFlightError` naming it: one live
        investigation per campaign, adopted by a start for its own candidate
        and refused to a start for another (roadmap 12 §9.2).

        ``refuse_live`` is the opposite answer to the same question: a live run
        of this class and kind on the subject refuses a new one with
        `RunInFlightError` naming it, and nothing is adopted -- for a run whose
        requests carry options adoption would lose (a cache sync's dry run or
        `embed: false`, spec 05 section 7.8). A repeat of the live run's own
        attempt id is still handed that run back.
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
                raise _contracts.StoreMovingError
            existing = self._by_own_attempt(subject, attempt_id, scene_identity,
                                            adopt_terminal)
            if existing is not None:
                return existing, False

            if single_live and single_match is not None \
                    and (held := self._live_kind(subject, cls, kind)) is not None \
                    and (held.cancel_requested or not single_match(held)):
                # A run about something else, or one still stopping, holds
                # the subject until it ends: never two at once.
                raise _contracts.RunInFlightError(held.id)
            if single_live and (live := self._live_single(subject, cls, kind)) is not None:
                return self._adopt(live, subject, attempt_id), False
            if refuse_live and (live := self._live_kind(subject, cls, kind)) is not None:
                raise _contracts.RunInFlightError(live.id)

            key = self._refuse_if_excluded(subject, cls)

            run = _record.Run(subject, cls, kind, attempt_id, scene_identity, labels,
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

    def _refuse_if_excluded(self, subject: _contracts.Subject, cls: _contracts.RunClass) -> str | None:
        """Raise if a new run of this class may not start now; else its key.

        Under ``self._lock``. The maintenance-hold count is read under the same
        lock `exclude_maintenance` counts under, so a fork that has just been
        let in and a migration arriving now cannot both pass.
        """
        if cls == "maintenance" and self._maintenance_holds:
            raise _contracts.MaintenanceHeldError
        key = _contracts.exclusion_key(subject, cls)
        if key is not None:
            holder_id = self._by_key.get(key)
            holder = self._runs.get(holder_id) if holder_id else None
            if holder is not None and holder.state == "running":
                raise _contracts.RunInFlightError(holder.id)
        return key

    def _by_own_attempt(self, subject: _contracts.Subject, attempt_id: str | None,
                        scene_identity: str | None,
                        adopt_terminal: bool) -> _record.Run | None:
        """The run this attempt id already made, if it may be adopted. Under
        ``self._lock``; `start_or_existing`'s first lookup, kept apart so that
        method stays within the complexity bound."""
        if attempt_id is None:
            return None
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
            return existing
        return None

    def _live_kind(self, subject: _contracts.Subject, cls: _contracts.RunClass, kind: str) -> _record.Run | None:
        """Any running run of this class and kind on the subject, a stopping
        one included -- it is still running until its thread returns. Called
        under ``self._lock``."""
        for rid in self._by_subject.get(subject, []):
            run = self._runs.get(rid)
            if run is not None and run.state == "running" and run.cls == cls and run.kind == kind:
                return run
        return None

    def _live_single(self, subject: _contracts.Subject, cls: _contracts.RunClass, kind: str) -> _record.Run | None:
        """The running run of this class and kind on the subject, if any.

        Called under ``self._lock``. A run that is ``forgotten`` belongs to a
        deleted record, and one that is ``cancel_requested`` has had Stop
        pressed: adopting either would hand the next Refresh a sweep that is
        going away.
        """
        for rid in self._by_subject.get(subject, []):
            run = self._runs.get(rid)
            if (run is not None and run.state == "running" and run.cls == cls
                    and run.kind == kind and not run.forgotten
                    and not run.cancel_requested):
                return run
        return None

    def _adopt(self, live: _record.Run, subject: _contracts.Subject, attempt_id: str | None) -> _record.Run:
        """Record ``attempt_id`` as another name for ``live``. Under ``self._lock``.

        Without it a client that lost the 202 for an adopted start could not
        find the run again by the attempt it sent.
        """
        if attempt_id is not None and attempt_id != live.attempt_id \
                and attempt_id not in live.adopted_attempts:
            live.adopted_attempts.append(attempt_id)
        if attempt_id is not None:
            self._by_attempt[(subject, attempt_id)] = live.id
        return live

    def pend_touched(self, subject: _contracts.Subject, refs: Iterable[str]) -> None:
        """Leave refs for the subject's live reconcile to take after its pass."""
        with self._lock:
            self._pending_touched.setdefault(subject, set()).update(refs)

    def take_touched(self, subject: _contracts.Subject) -> set[str]:
        """Take (and clear) the refs pending for the subject; empty when none."""
        with self._lock:
            return self._pending_touched.pop(subject, set())

    def drop_pending_touched(self) -> None:
        """Forget every pending ref. They name records in a tree that may no
        longer be the store's root."""
        with self._lock:
            self._pending_touched.clear()

    def forget_root(self) -> int:
        """Make every run unreachable, after the store root moved. Returns how
        many went.

        `forget_subject` for a whole library: every subject's ids are slugs of
        the OLD tree, and the new one can hold a campaign of the same slug, so
        inside the retention window a reader there could otherwise rediscover
        a draft by its attempt id -- a history answer or an investigation of
        the other library's campaign -- and show it as this one's. The move is
        refused while anything is live (`hold_still`), so every run this
        reaches is terminal; the flag rather than `_runs` for the same reason
        `forget_subject` gives, and the reaper collects them as ever."""
        with self._lock:
            for run in self._runs.values():
                run.forgotten = True
            count = len(self._runs)
            self._by_subject.clear()
            self._by_attempt.clear()
            self._precancelled.clear()
            self._pending_touched.clear()
            return count

    @staticmethod
    def _now() -> float:
        """The clock retention is measured on. Monotonic, never wall."""
        return time.monotonic()

    def _owns(self, run: _record.Run | None, subject: _contracts.Subject,
              identity: str | None) -> bool:
        """Whether ``subject`` (and, when given, ``identity``) owns this run.

        The subject alone is not enough for a scene. A scene deleted and
        replaced inside the retention window lands on the same ``sid`` -- ids
        are recycled by design -- so the replacement would otherwise be handed
        the dead scene's run and could read its frames or cancel it. The
        identity is exactly the thing that distinguishes them; callers that
        have it pass it.
        """
        if run is None or run.forgotten or run.subject != subject:
            return False
        return identity is None or run.scene_identity == identity

    def get(self, run_id: str, subject: _contracts.Subject,
            identity: str | None = None) -> _record.Run | None:
        """The run, or ``None`` if this subject does not own it.

        The subject check is the isolation: a run id from another scene answers
        'gone', never another scene's state.
        """
        with self._lock:
            run = self._runs.get(run_id)
            return run if self._owns(run, subject, identity) else None

    def for_subject(self, subject: _contracts.Subject,
                    identity: str | None = None) -> list[_record.Run]:
        """Every run on this subject, live and terminal, oldest first.

        A collection rather than a pointer: drafts and background work declare
        no exclusion key, so they overlap legitimately, and a most-recent
        pointer would hide the first one with no discovery path left.
        """
        with self._lock:
            return [self._runs[rid] for rid in self._by_subject.get(subject, [])
                    if self._owns(self._runs.get(rid), subject, identity)]

    def cancel_or_precancel(self, subject: _contracts.Subject, attempt_id: str,
                            identity: str | None = None) -> _record.Run | None:
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
            while len(self._precancelled) > _contracts._MAX_PRECANCEL:
                self._precancelled.pop(next(iter(self._precancelled)))
            return None

    def for_attempt(self, subject: _contracts.Subject, attempt_id: str | None,
                    identity: str | None = None) -> _record.Run | None:
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

    def reviews_for_generation(self, subject: _contracts.Subject, generation: str) -> list[_record.Run]:
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

    def live_running_in(self, cid: str) -> _record.Run | None:
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

    def any_live(self) -> _record.Run | None:
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
        `start_or_existing` reads the flag inside that same lock. The block
        takes no other registry lock: `set_data_dir` rewrites a pointer file,
        but it first waits out `paths.library_guard`, which a world or
        campaign delete holds across its whole removal (spec 03 keeps the
        root still under a delete). So a move that lands beside a delete
        refuses reservations for that delete's length, not just a write's.

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
                    raise _contracts.RunInFlightError(run.id)
            if self._root_pins:
                raise _contracts.RunInFlightError(next(iter(self._root_pins)))
            if self._sync_holds:
                raise _contracts.SyncRunningError()
            self._store_moves += 1
        try:
            yield
        finally:
            with self._lock:
                self._store_moves -= 1

    def pin_root(self, run_id: str) -> None:
        """Hold the root still for a run that ended with a thread still
        reading the store through `store.home()`, until `unpin_root`: a move
        is refused (`hold_still`) as if the run were live."""
        with self._lock:
            self._root_pins[run_id] = self._root_pins.get(run_id, 0) + 1

    def unpin_root(self, run_id: str) -> None:
        with self._lock:
            left = self._root_pins.get(run_id, 0) - 1
            if left > 0:
                self._root_pins[run_id] = left
            else:
                self._root_pins.pop(run_id, None)

    @contextlib.contextmanager
    def sync_hold(self):
        """Hold the store root still for one cache-sync batch.

        `hold_still`'s other direction, in one acquisition for its reason:
        yields False, holding nothing, while a move is in progress, and while
        the batch holds this a move is refused (`SyncRunningError`). A flag
        rather than an exception so the queue (`cache_warm`, which the run
        machinery imports) need not import this module. Only for the length of
        a batch: the write-through queue holding paths between batches is not
        a reason to refuse a move, since those paths are safe to drop and the
        move clears them."""
        with self._lock:
            if self._store_moves:
                held = False
            else:
                held = True
                self._sync_holds += 1
        if not held:
            yield False
            return
        try:
            yield True
        finally:
            with self._lock:
                self._sync_holds -= 1

    @contextlib.contextmanager
    def exclude_maintenance(self):
        """Hold the store against image-store maintenance for this block.

        The other direction of `MAINTENANCE_KEY`, for the operations that walk
        or reshape a whole tree -- a fork, a delete, a bundle export, the
        scheduled backup. A migration unlinking legacy files under a fork that
        is copying them leaves the fork with neither copy; a collection running
        while an export packs placements ships placements whose objects have
        just gone.

        `hold_still`'s shape, for `hold_still`'s reason: the refusal and the
        count are taken in ONE acquisition, and `start_or_existing` reads the
        count inside that same lock, so neither side can slip in between the
        other's check and act. Raises `MaintenanceRunningError` when a
        maintenance run is live -- including one asked to stop whose thread has
        not yet returned, which is still walking the tree.

        The same OS lock a maintenance run holds also fences other processes
        on this machine (#479). Pin its path at entry: the storage pointer may
        change during an operation, and exit must release the original root's
        descriptor. Each root keeps one descriptor until its LAST hold exits;
        overlapping tree operations within this app therefore still coexist.
        """
        with self._lock:
            for run in self._runs.values():
                if run.state == "running" and run.cls == "maintenance":
                    raise _contracts.MaintenanceRunningError(run.id)
            lock_path = _maintenance_claims._maintenance_lock_path(paths.home().resolve())
            held = self._maintenance_locks.get(lock_path)
            if held is None:
                fd, depth = _maintenance_claims._acquire_maintenance_lock(lock_path), 0
            else:
                fd, depth = held
            self._maintenance_locks[lock_path] = fd, depth + 1
            self._maintenance_holds += 1
        try:
            yield
        finally:
            with self._lock:
                self._maintenance_holds -= 1
                fd, depth = self._maintenance_locks[lock_path]
                if depth == 1:
                    del self._maintenance_locks[lock_path]
                    proclock.release(fd)
                else:
                    self._maintenance_locks[lock_path] = fd, depth - 1

    def live_of_class(self, cls: _contracts.RunClass) -> list[_record.Run]:
        """Every running run of this class, on any subject."""
        with self._lock:
            return [r for r in self._runs.values()
                    if r.state == "running" and r.cls == cls]

    def live_for_key(self, key: str | None) -> _record.Run | None:
        """The running holder of an exclusion key, if there is one."""
        if key is None:
            return None
        with self._lock:
            holder_id = self._by_key.get(key)
            holder = self._runs.get(holder_id) if holder_id else None
            return holder if holder is not None and holder.state == "running" else None

    def forget_subject(self, subject: _contracts.Subject) -> int:
        """Make every run on `subject` unreachable. Returns how many went.

        For a record that has been DELETED. Campaign and world ids are slugs
        and a slug is reusable: deleting "Saltmarch" and creating another
        campaign of that name lands on the same id, and inside the retention
        window the replacement's `/runs` would answer with the dead campaign's
        runs -- so a retry with the same attempt id could adopt suggestions
        computed from a campaign that no longer exists, and show them as this
        one's. A scene solves this with an identity in its subject; campaigns
        and worlds have no such token, and minting one is a store change, so
        the deletion clears them instead.

        A FLAG on each run plus the indexes, and the flag is the part that
        matters: `get` resolves by id straight out of `_runs`, and the client
        that started the run is holding that id -- so clearing the indexes
        alone would still let it poll the dead record's result.

        Never `_runs` itself: a live run may still be executing, its record has
        to stay for `_guarded` to finish with and for the reaper to collect,
        and the internal sweeps that refuse a store move while anything is
        generating have to keep seeing it. Unreachable is the whole
        requirement -- these are all `draft` runs, whose result is held on the
        record and written nowhere, so a run nobody can find is a run that
        changes nothing.

        **PARTIAL, and deliberately said out loud.** This covers the ordinary
        case -- the record is deleted, then somebody discovers or retries --
        and not two races either side of it:

        * a draft that read the record BEFORE the delete but reserves after
          this sweep is stamped with the live subject and nothing has it;
        * a replacement that reserves between the store delete and this call
          is hidden by it.

        Both need a fence shared between deletion and reservation, and the only
        one that actually works is the per-record identity a scene already has
        (`scene_identity`), which would make a recycled slug a different
        subject outright. An epoch stamped at reservation does not: the late
        reservation captures the post-sweep value and matches it. A tombstone
        set poisons the slug for a legitimately recreated record. The campaign
        lock serializes nothing here, because `reserve_draft` does not take it
        and world and global subjects have none. Minting identities for
        campaigns and worlds is a store change with a migration; until then
        this is what is covered.
        """
        with self._lock:
            ids = self._by_subject.pop(subject, [])
            for run_id in ids:
                run = self._runs.get(run_id)
                if run is not None:
                    run.forgotten = True
            for key in [k for k in self._by_attempt if k[0] == subject]:
                del self._by_attempt[key]
            for key in [k for k in self._precancelled if k[0] == subject]:
                del self._precancelled[key]
            # A slug is reusable: a recreated campaign of the same name must not
            # inherit refs a trigger left for the dead one's sweep.
            self._pending_touched.pop(subject, None)
            return len(ids)

    def reap(self, now: float | None = None) -> int:
        """Drop terminal runs older than the window. Returns how many went.

        Every index is cleared, not just ``_runs``: an entry left in
        ``_by_attempt`` would make a later send with the same attempt id adopt a
        record that no longer exists, and one left in ``_by_key`` would wedge
        the scene permanently -- neither visible through ``get``.
        """
        cutoff = (self._now() if now is None else now) - _contracts.REAP_SECONDS
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
                for alias in run.adopted_attempts:
                    # The same guard, for the attempt ids that adopted it.
                    if self._by_attempt.get((run.subject, alias)) == run.id:
                        del self._by_attempt[(run.subject, alias)]
                key = _contracts.exclusion_key(run.subject, run.cls)
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
