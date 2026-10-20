"""A run owns one fenced scene claim and one mutable progress box."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ... import store
from .. import streaming
from .. import tracker as tracker_routes
from ._router import router as router


def enabled():
    return True


def roster(cid, sid):
    return store.response_protocol.npc_roster(store.appearances.scene_cast(cid, sid))


def validate_actor(cid, sid, actor_ref):
    if actor_ref and actor_ref not in [r["ref"] for r in roster(cid, sid)] + ["grimoire"]:
        raise HTTPException(400, detail="speaker must be a present NPC or grimoire")


@dataclass(frozen=True)
class _Claim:
    """A run's claim on its scene's turn, which every write the run makes is
    fenced on (`fence`): the scene by the identity the run was reserved on
    (`run.scene_identity` -- a `sid` moves on rename and is reissued after a
    delete) and the turn by the token it took (`streaming._claim_turn`),
    which the scene's next turn replaces."""

    cid: str
    sid: str
    run: Any
    token: int

    @classmethod
    def take(cls, cid, sid, run) -> _Claim:
        """Make `run` the scene's current turn. Under the campaign lock, ahead
        of anything the turn writes."""
        return cls(cid, sid, run, streaming._claim_turn(cid, sid))

    def fence(self) -> None:
        """Refuse a write once the scene moved or another turn claimed it.
        Under the campaign lock that covers the write, beside it: read outside
        one, it answers a question that was true a moment ago."""
        if streaming._scene_moved(self.cid, self.sid, self.run.scene_identity) \
                or not streaming._owns_turn(self.cid, self.sid, self.token):
            raise store.responses.ResponseConflict(
                "scene_replaced", "The scene changed during generation."
            )


def _round_state(cid, sid, round_record, **fields):
    messages = store.scenes.read_scene(cid, sid)["messages"]
    return store.responses.update_round(
        cid,
        sid,
        round_record["id"],
        watermark=len(messages),
        transcript_hash=store.responses.transcript_hash(messages),
        **fields,
    )


class _Progress:
    """What a run's frames have in hand, which `_frames`' failure paths read
    back: the round, and the contribution in flight (`record`, `watcher`,
    `meter`, the roll `continuation` it finishes and the `appended` blocks its
    prompt still owes). `served` is the connection that answered that
    contribution, kept here so a rescue after `meter` is dropped (a failing
    `_pause`/`_save`) still files the partial under the fallback that served
    it, not the primary. `_save` and `_pause` file the contribution in flight
    from here, whether the round or its rescue lands it. `ending` is the
    successor choice of the contribution that closed the round, None until one
    does (`_chain_continues`), and `terminal` that the round already wrote the
    run's last frame (a roll pause, an empty response)."""

    def __init__(self, round_record, appended, continuation, served=""):
        self.round_record = round_record
        self.appended = appended
        self.continuation = continuation
        self.record = None
        self.watcher = None
        self.meter = None
        self.served = served
        self.ending = None
        self.terminal = False
        # Which snapshot the contribution in flight runs on, and what made it
        # once its call is done: built before the meter is dropped, so a Stop
        # landing while `_save`/`_pause` runs still has it for `_rescue`.
        self.composed = "primary"
        self.composed_settings = None
        self.made_by = None
        #: The decide tool's record for the contribution in flight (02-C4),
        #: stored on its variant; None when it was not offered.
        self.tool_record = None
        self.history_by_round = {}
        #: round id -> [history retrievals, query embeddings] so far.
        self.history_cost: dict[str, list[int]] = {}
        #: (round id, speaker) -> the Decision stage's record for that step
        #: (spec 11 §5.5); a key here was refined, and is never asked again.
        self.refined: dict[tuple, dict | None] = {}
        #: Tracker keys this turn marked `pending` (`_save`), started in
        #: `_frames`' `finally` once the terminal frames are out -- in
        #: transcript order, which is the order the scene's tracker lock runs
        #: them in.
        self.tracked: list[tracker_routes.Mark] = []


def _abort_meter(meter):
    if meter:
        meter.done("aborted")


async def _start_tracking(app, cid, sid, client, tracked, identity):
    """Start the tracker runs for the keys this turn marked `pending`.

    AFTER the terminal frames, for `streaming._fire_follow_up`'s reason: the
    player's `done` (or proposal) frame must not wait on bookkeeping. Starting
    a run takes no campaign lock (`tracker.start`); only its failure path does,
    to mark the record `failed` rather than leave it `pending` behind a run
    that does not exist. In a worker thread, because reserving a run goes
    through the lifespan's portal, which raises from the loop thread.

    `app` is None only for a test driving `_frames` with no app behind it --
    there is no lifespan to run an update on. The keys stay `pending`, which is
    the truth: nothing was started."""
    if app is None:
        return
    for marked in tracked:
        await run_in_threadpool(tracker_routes.start, app, cid, sid, marked, client, identity)


def _public_error(exc):
    if isinstance(exc, store.responses.ResponseNotFound):
        return HTTPException(404, detail="response not found")
    return HTTPException(409, detail={"kind": exc.kind, "detail": exc.detail})
