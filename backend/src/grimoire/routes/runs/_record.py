"""One detached run owns its frames, terminal timestamps and captured labels."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from . import _contracts
from ._router import router as router


class Run:
    """One detached unit of work, and the frames it has produced so far."""

    def __init__(self, subject: _contracts.Subject, cls: _contracts.RunClass, kind: str,
                 attempt_id: str | None, scene_identity: str | None,
                 labels: dict,
                 events: tuple[_contracts.HandshakeEvent, _contracts.HandshakeEvent],
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
        self.state: _contracts.RunState = "running"
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
        # Set when the record this run belongs to is DELETED. Read by `_owns`,
        # so every discovery path -- by id, by subject, by attempt -- answers
        # as it would for a run that never existed, while the internal sweeps
        # (`any_live`, `live_running_in`) still see it, because it really is
        # still generating and the store must not move under it. Campaign and
        # world ids are slugs and a slug is reusable, so without this a
        # replacement of the same name inherits the dead record's runs.
        self.forgotten = False
        # Set by the reviewer's Cancel, under the campaign lock, BEFORE the
        # record is deleted -- and read by this run's terminal persist under
        # that same lock, which is what stops the runner from publishing and
        # recreating the review the player has just dismissed. A cancelled
        # review that reappears minutes later is worse than one that was never
        # saved. Distinct from `cancel_requested`, which is a Stop on the run
        # itself: this one is an intent about the RECORD, and it is what the
        # phases' `abandoned` predicate reads to stop generating for it.
        self.review_cancelled = False
        # Attempt ids of later starts that ADOPTED this run rather than making
        # their own -- a Refresh pressed while the automatic reconcile after End
        # Scene is still live (`single_live`). Each is indexed in `_by_attempt`
        # beside the run's own, so `GET .../runs?attempt=` re-finds the run for
        # a client that lost its 202, and `reap` drops them with the run.
        # Appended only under the registry lock.
        self.adopted_attempts: list[str] = []
        # The store root a `maintenance` run pinned when it was reserved. Every
        # path the run builds starts here rather than at the live `home()`, so a
        # root that changes underneath is detected rather than followed. `None`
        # for every other class.
        self.root: Path | None = None
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

    def finish(self, state: _contracts.RunState, at: float | None = None,
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
