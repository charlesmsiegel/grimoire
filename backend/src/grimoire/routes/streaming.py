"""SSE framing, the persisted-turn strategies, and the roll-proposal
machinery they share.

Crash-window disclosure (accepted risk, per the phase-4 spec). The fence
handoffs are serialized against concurrent writers by the per-campaign
proposals lock, but are NOT crash-atomic across files (proposals.json and
the scene transcript are separate writes; grimoire is a local single-process
app with no cross-file journal). Two microsecond-wide windows exist, both
bounded and non-corrupting:

  - initial fence: a crash between writing the pending record and persisting
    the pre-fence narration leaves a recoverable chip whose last narration
    beat is missing — the player can still adjudicate or decline;
  - follow-up fence: a crash between the old record's `narrated` write and
    the new pending record leaves the continuation fully persisted and the
    follow-up check simply lost; play continues on the next send.

The guaranteed invariant: no roll is ever duplicated or lost once logged, no
narration is attributed to a superseded decision, and no crash leaves an
unrecoverable or corrupted state. Full journaling was rejected as
disproportionate for a local single-user store.

This module holds no routes; ``scenes``, ``mechanics`` and ``greetings``
import from it.

A note for tests that patch the helpers below: importers bind them by value
(``from .streaming import _persist_reply``), so patch the module where the name
is *looked up*, not necessarily where it is defined. Patching
``routes.streaming._persist_reply`` covers the turn strategies here but not
``greetings.post_first_post``, which holds its own reference.
"""

from __future__ import annotations

import itertools
import json
import logging
import time

import anyio
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError

_log = logging.getLogger(__name__)

# An SSE comment: framing every proxy on the path understands as traffic, and
# that `parseSSEChunk` already skips (it only reads `data:` lines), so the
# client needs no matching change to tolerate one. Emitted whenever the facade
# reports it is still waiting on the model (#95) -- before the first token, a
# generation is otherwise indistinguishable from a dead connection.
_HEARTBEAT = ": heartbeat\n\n"

# Which turn each scene currently belongs to, newest claim wins. A cancelled
# turn's flush runs after its socket has closed, so it has to be able to ask
# whether it is still the turn the scene belongs to before writing anything.
#
# Transcript length cannot answer that, which review caught after it was first
# used for exactly this: three of the four `_chat_stream` callers -- retry,
# regenerate, and the director-note/empty send -- append nothing of their own,
# so two overlapping ones capture an identical length and the older one's abort
# would sail through the check and persist into the newer turn.
#
# In-process only, and that is the right scope: it distinguishes two turns
# racing inside one backend, which is what a player generates. Two *processes*
# on one scene is the case `store/locks.py` already documents as beyond what
# this app can serialize. Entries are overwritten per turn and never removed --
# one small entry per scene ever streamed, which is not worth reaping.
_turn_tokens: dict[tuple[str, str], int] = {}
_turn_seq = itertools.count(1)


def _claim_turn(cid: str, sid: str) -> int:
    """Make this the scene's current turn and return the token proving it."""
    token = next(_turn_seq)   # atomic under the GIL; claims come from request threads
    _turn_tokens[(cid, sid)] = token
    return token


def _owns_turn(cid: str, sid: str, token: int) -> bool:
    return _turn_tokens.get((cid, sid)) == token


async def _flush_on_abort(hook, watcher) -> None:
    """Persist a turn's partial output while the stream is being torn down.

    The disconnect path, not the error path: a client that navigates away or
    presses Cancel makes Starlette cancel the task driving this generator, and
    the resulting ``CancelledError``/``GeneratorExit`` is not an ``LLMError``,
    so the handler that persists partials never used to run and the text was
    dropped on the floor (#95).

    Shielded, because the cancellation is already in flight: inside a cancelled
    scope the next await would re-raise before the write could start. The wait
    is bounded by the store lock's own timeout rather than a second one here --
    ``run_in_threadpool`` is not cancellable, so a bound around it would only
    lie about when this returns.
    """
    if hook is None:
        return
    try:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(hook, watcher)
    except Exception:  # noqa: BLE001 - a failed rescue must not replace the teardown
        # Nothing left to tell anyone: the client is gone and the exception that
        # brought us here is about to be re-raised, so anything raised in here
        # would only mask it. Broad rather than `StoreBusy` alone (the error
        # path's narrower catch, #234) because this one runs during unwinding,
        # where the loop may be shutting down under it -- and losing a partial
        # is the outcome this path already accepts.
        pass


def _persist_reply(cid: str, sid: str, text: str) -> int:
    """Split one model reply into per-speaker posts and append them (#744),
    returning how many actually landed.

    The count is what a caller needs to tell "the model said something" from
    "the transcript grew". They are not the same question and the gap is not
    only the tracker block: a reply that is nothing but a speaker marker splits
    into no non-empty segment either, and `append_reply` writes nothing for it.
    `post_first_post` is the caller that has to know — it reports success to a
    user who is adopting an opener, and reporting it over an empty scene loses
    the text with no error to show for it.

    Macros are expanded before persisting (#137): {{roll}}/{{random}} must be
    resolved once, not re-rolled on every future context build that re-reads
    this now-historical message. Goes through append_reply so the generation
    records its own turn boundary for drift measurement.

    A trailing transient-state tracker block (#120) is split off FIRST, so it
    never reaches `split_reply` and so cannot become a post. Unconditionally,
    not gated on `turnstate_depth`: turning the feature off must not start
    leaking blocks into transcripts while the model is still complying from the
    scene it can see, and a block is unambiguous enough that stripping one
    nobody asked for costs nothing.

    Art handles are resolved next, for the same reason and in the same spirit:
    `[[art:...]]` is machine-readable output that must become markdown -- or
    nothing -- before `split_reply` runs, so a handle can never be split into a
    post of its own and no handle is ever written to a transcript.
    Unconditionally, again: a model still emitting handles from a scene it can
    see must not start leaking them the moment the section is switched off in
    the prompt layout. `resolve_handles` deletes what it cannot resolve, so a
    store with no described art turns every handle into nothing, which is the
    right answer there too.

    It runs BEFORE macro expansion, so a description containing `{{user}}`
    lands as alt text that expands like any other narration, and AFTER the
    tracker split, so a handle stranded inside a tracker block is not resolved
    into markdown that nobody will ever render.
    """
    text, tracked = store.turnstate.split_block(text)
    text = store.context.resolve_art_handles(cid, text, sid)
    players = frozenset(store.appearances.player_names(cid, sid))
    subs = store.context.scene_substitutions(cid, sid)
    # Tracker values get the same one-shot macro resolution the narration below
    # does: the section they feed is macro-expanded on every context build, so a
    # stored {{random}} would re-roll each prompt and a stored {{user}} would
    # drift with the cast.
    tracked = store.turnstate.expand_values(
        tracked, lambda v: store.context.expand_macros(v, subs, cid, sid))
    segments = [{"speaker": seg["speaker"],
                 "content": store.context.expand_macros(seg["content"], subs, cid, sid)}
                for seg in store.scenes.split_reply(text, players)]
    # One lock over both: a reply landing in a slot a reroll emptied is a
    # variant that exists only in the transcript until `reconcile` writes it
    # down, and an edit arriving in between would destroy it. Reentrant, so the
    # acquisitions inside each call cost nothing. `reconcile` writes nothing for
    # a reply that lands anywhere else, which is every ordinary turn.
    with store.locks.campaign_lock(cid):
        # Read before the append, under the same lock, so the index is the one
        # this generation's posts really take. Skipped entirely when there is
        # neither a block to file nor a ledger to clean up, which is every turn
        # on an install that leaves the feature off: the guard is a `stat`, and
        # what it avoids is re-parsing the whole transcript.
        landed = (len(store.scenes.read_scene(cid, sid)["messages"])
                  if tracked or store.turnstate.read(cid).get(sid) else None)
        store.scenes.append_reply(cid, sid, segments)
        if landed is not None:
            _record_turnstate(cid, sid, landed, segments, tracked)
        try:
            store.alternates.reconcile(cid, sid)
        except OSError:
            # The reply is already in the transcript. A sidecar that cannot be
            # written (a full disk, a read-only store) must not turn a landed
            # generation into a failed one: the exception would escape the
            # stream finalizer before its `done` frame, so the client would show
            # a failure over a reply that is on disk and offer a retry that
            # appends a *second* generation. Same judgement `_read_raw` already
            # makes on the way in — the sidecar is a convenience beside the
            # transcript, never a reason to lose or misreport one. The cost is
            # the round-eleven durability window staying open for this turn.
            pass
        # Counted the way `append_reply` filters, because that is what it wrote.
        kept = sum(1 for s in segments if s["content"].strip())
        if kept:
            # Inside the hold, so the token has moved before any other writer
            # can take the lock and read it. The activity middleware stamps
            # every other campaign write, but it deliberately skips streams --
            # a stream's status line is sent before its outcome is known -- so
            # a detached turn is the one mutation that would otherwise land
            # with the campaign's revision reading exactly as it did before
            # (#409). A clock advance priced in another tab while this turn was
            # generating would then confirm against a transcript that has grown
            # under it. Never raises, like everything else after the append.
            store.revision.bump(cid)
    return kept


def _record_turnstate(cid: str, sid: str, landed: int, segments: list[dict],
                      tracked: dict) -> None:
    """Retire what this generation displaces, then file its tracker block
    against the index of its LAST post.

    `supersede` runs whether or not there is a block, because the case it exists
    for is a reroll whose replacement has none -- see its docstring.

    `append_reply` drops blank segments, so the count is recomputed the same
    way here rather than assumed: an entry filed past the transcript's end is
    one `entries()` then discards, silently losing the turn it describes.

    Never fatal. A ledger that cannot be written must not turn a landed
    generation into a failed one: the exception would escape the stream
    finalizer before its `done` frame, so the client would report a failure
    over a reply that is on disk and offer a retry that appends a second one.
    Exactly the judgement `reconcile` below already makes, and the cost is
    smaller -- a lost mood, not a lost variant.
    """
    try:
        store.turnstate.supersede(cid, sid, landed)
        kept = sum(1 for s in segments if s["content"].strip())
        if not tracked or not kept:
            return
        # The transcript's own label rule, both halves of it: drop a sub-speaker
        # parenthetical first (`**Mara (aside):**` is Mara — `speaker_base` is
        # the same helper `absorb.routing` uses so the two cannot disagree),
        # then match exactly or by unique prefix. Passing the raw label matched
        # nothing for a sub-speaker, so the dialogue persisted and every field
        # it carried was dropped.
        states = store.turnstate.resolve(
            tracked, store.appearances.scene_cast(cid, sid),
            lambda label, names: store.scenes.match_name(
                store.scenes.speaker_base(label), names))
        store.turnstate.record(cid, sid, landed + kept - 1, states)
    except OSError:
        pass


def _narration(watcher) -> str:
    """What this turn actually SAID -- the reply with its tracker block already
    split off (#120).

    Every "did this turn produce anything?" test goes through here, because
    `watcher.narration` answers a different question once a tracker block can
    exist. A reply consisting only of a block is non-empty raw and empty in the
    transcript, and the callers below use that test to decide whether to put
    back a reply that reroll deleted, whether to take a stranded user post off,
    and whether the turn is worth persisting at all. Testing the raw text there
    made a tracker-only regenerate look like a successful reply, skip the
    restore, and delete a reply nothing else held a copy of.

    Cheap and pure, so calling it beside `_persist_reply`'s own split costs a
    scan of one reply and keeps the grammar in one place.
    """
    return store.turnstate.split_block(watcher.narration)[0]


def _would_land(cid: str, sid: str, text: str) -> int:
    """How many posts `_persist_reply` would keep from `text`, without writing.

    The authoritative answer is `_persist_reply`'s own return, and the callers
    that can afford to persist first use that. Two cannot: `on_abort` chooses
    between restoring and finalizing, and finalizing is what persists;
    `_continuation_stream` has to decide before `commit_narration`, which runs
    the write inside its own lock and marks the record `narrated` on the way
    out. Both need the count in advance, so this predicts it the same way —
    tracker block off, then the marker grammar, then non-empty content.

    Deliberately skips macro expansion, which `_persist_reply` does and which
    can only ever shrink a segment. That makes this conservative in the safe
    direction: it can say "something lands" when nothing does, never the
    reverse, and the caller's fallback for that is the path it would have taken
    anyway.
    """
    narration, _ = store.turnstate.split_block(text)
    players = frozenset(store.appearances.player_names(cid, sid))
    return sum(1 for seg in store.scenes.split_reply(narration, players)
               if seg["content"].strip())


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _sse_response(frames: list[str]):
    """A StreamingResponse that just replays already-computed SSE frames (used
    for the immediate-done / error-frame branches of the proposal route)."""
    async def event_stream():
        for f in frames:
            yield f
    return StreamingResponse(event_stream(), media_type="text/event-stream")


class StreamOutcome:
    """What a producer decided about its own run, for the runner to apply.

    A detached producer is driven by `runner._guarded`, which cannot read
    success off the wire: these generators handle an upstream `LLMError`, a
    contended finalize, and a scene that changed underneath them by emitting an
    error frame and returning NORMALLY. "Did not raise" therefore covered both
    a delivered reply and three ways of failing, and every one of them was
    recorded `landed` -- so a poll, and the notification that reads it, told the
    user their turn had landed when nothing was persisted.

    Deliberately a plain box the generator fills rather than a return value:
    the producer is an async *iterator*, so there is nowhere for it to return
    to, and its consumer only knows it is finished when iteration stops.

    A producer that never sets anything leaves `result()` at `None`, which the
    runner reads as "infer from how the task ended". That keeps every
    unmigrated caller working exactly as before.
    """

    __slots__ = ("at", "error", "state")

    def __init__(self) -> None:
        self.state: str | None = None
        self.error: dict | None = None
        #: How long the transcript was when this turn's terminal write finished,
        #: or None when it wrote nothing. See `persisted`.
        self.at: int | None = None

    def persisted(self, at: int | None) -> None:
        """The transcript length a terminal write of this turn left behind.

        This is the follow-up boundary (#397): the length `askAfterPost` used
        to read on the client and pass as `upto`, so a fold cannot swallow a
        post whose reply has not been written yet. Recorded here rather than
        read afterwards, because "afterwards" is a second read of a file
        anything may have moved on -- and the server, unlike the client, can
        take it under the very lock the write held.

        The LAST write wins, and there is at most one that matters: a turn
        either persists its reply or persists a partial and stops. A turn that
        wrote nothing leaves this None, which is what tells the runner there is
        no follow-up worth scheduling -- neither the summary's coverage nor the
        scene-break count has moved.

        `None` is accepted and ignored, so a boundary that could not be read
        (`_tail_length`) is a skipped follow-up rather than an exception out of
        a `finalize` that has already written the reply.
        """
        if at is not None:
            self.at = at

    def land(self) -> None:
        """The turn finished and its writes were attempted -- the ordinary end."""
        if self.state is None:            # a failure already recorded wins
            self.state = "landed"

    def fail(self, kind: str, detail: str, **note) -> None:
        """`note` is what the handler DID, carried onto the run as well as the
        wire. Today that is `post_returned`: a failed turn whose rollback took
        the player's words back off the transcript. The live frame said so and
        the run did not, so a client that lost the frame and discovered the
        outcome by polling was told the turn failed but not that its prompt was
        gone -- and the composer could not give the words back. Recovery reads
        the run, so the run has to know."""
        self.state = "failed"
        self.error = {"kind": kind, "detail": detail, **note}

    def result(self) -> dict | None:
        if self.state is None:
            return None
        return {"state": self.state, "error": self.error}


def _tail_length(cid: str, sid: str) -> int | None:
    """How many posts the transcript holds right now, or None if it cannot be read.

    Called under the campaign-lock hold a terminal write took, immediately
    after it, so what comes back is the boundary that write produced rather
    than a length something else has since moved on. What it is FOR is
    `StreamOutcome.persisted`, whose docstring carries that reasoning.

    **It may not raise**, and that is why it is a function rather than a
    `len(read_scene(...))` at each call site. It runs inside `finalize`, after
    the reply is already on disk, and `_fence_stream` converts only `StoreBusy`
    there -- so a scene momentarily unreadable (a sync client mid-replace, a
    Windows sharing violation) would escape to the runner and mark a landed
    turn `failed`, over a reply that is perfectly persisted, with a retry then
    free to append a second one. Losing the follow-up is the right cost of
    that: the next turn asks again with a boundary of its own, where a false
    failure is a duplicated reply nobody asked for.
    """
    try:
        return len(store.scenes.read_scene(cid, sid)["messages"])
    except Exception:                                        # noqa: BLE001
        _log.warning("could not read the follow-up boundary for %s/%s",
                     cid, sid, exc_info=True)
        return None


async def _fire_follow_up(after_turn, box: StreamOutcome) -> None:
    """Ask for this turn's background work, once the turn is over.

    The rolling summary and the scene-break check are scheduled here rather
    than by the client that sent the turn (#397). `CampaignView.askAfterPost`
    fired them when the streaming promise settled, which is precisely what does
    not happen in the case detached runs exist for: the phone locks, the
    JavaScript is suspended, the turn lands server-side, and nobody is left to
    ask. The summary then falls behind by however many turns were taken with
    the app backgrounded and the scene-break prompt never appears.

    Three things keep this from costing the turn anything, and all three are
    deliberate:

    * it runs AFTER the outcome is decided and outside every write path, so
      nothing here can change what the turn reports;
    * its failures are swallowed -- a summary that could not even be
      *scheduled* is not a failed turn, and there is nobody to tell;
    * it is gated on a boundary having been recorded, which is the same
      question as "did this turn write anything". A turn that persisted
      nothing has moved neither the summary's coverage nor the scene-break
      count, so asking would spend two reservations to be told nothing is due.

    What it schedules is `background` class work, which by `runs.exclusion_key`
    holds no key on the scene -- so neither of these can ever refuse a later
    turn with `run_in_flight` or hold the scene against an edit or an End Scene.

    IN A WORKER THREAD, and both halves of that matter. Reserving a run takes
    the campaign lock and reads the scene's identity off disk, and this is the
    lifespan's event loop -- inline, a contended campaign would freeze every
    unrelated request and open stream on the backend for as long as it took to
    resolve. And the runner's own handoff is built for a thread that is not the
    loop: `runner._PortalEvent` calls through the portal to build a run's
    events, which raises outright when called from the loop thread.

    Awaited AFTER the frames have gone out, never before. It is the last thing
    a turn does, so nothing a subscriber is waiting for can queue behind it --
    the only thing it delays is the run's own terminal state, by the few
    milliseconds two reservations cost.
    """
    if after_turn is None or box.at is None:
        return
    try:
        await run_in_threadpool(after_turn, box.at)
    except Exception:                                        # noqa: BLE001
        _log.exception("could not schedule the follow-ups for a finished turn")


def _scene_moved(cid: str, sid: str, identity: str | None) -> bool:
    """Whether `sid` no longer names the scene this turn started on.

    Scene ids are recycled -- `serialize._numbering` derives the next number
    from the files on disk with no stored counter -- so a turn held open while
    its scene is deleted and a same-titled replacement is created finds a
    perfectly valid path at exactly the id it captured, and appends its reply to
    somebody else's scene. `_owns_turn` cannot catch it: the claim is keyed by
    `sid` too, and the replacement has not claimed anything.

    `None` means the caller asked for no fence and this is always False --
    deliberately explicit, because comparing two `None`s always matches and
    that is the failure this exists to prevent. `reserve_turn` mints an identity
    rather than reading one for the same reason.

    MUST be called under the campaign lock, next to the write it guards: read
    outside one, it answers a question that was true a moment ago.

    STRICT, and this is the difference between a fence and a data-loss bug. The
    tolerant reader answers `None` for a header it could not open, which is
    indistinguishable here from a scene that was replaced -- so a sync client
    holding the file for the moment generation finalizes would make a complete,
    correct reply look like it belonged to a scene that no longer exists, and
    the fence would throw it away and mark the run failed with the original
    identity still sitting on disk. An unreadable fence is a store failure and
    is raised as one; the caller reports it as retryable contention, which is
    what it is.
    """
    if identity is None:
        return False
    for attempt in range(_FENCE_READS):
        try:
            return store.scenes.scene_identity_strict(cid, sid) != identity
        except OSError as exc:
            if attempt == _FENCE_READS - 1:
                # Raised as contention, not invented as an answer. `StoreBusy`
                # is the kind every caller here already handles -- the error
                # path swallows it, the abort path swallows everything, and
                # finalize turns it into the `busy` frame -- so an unverifiable
                # fence ends the turn the same way a contended store does, and
                # nothing is written to a scene we could not identify.
                raise store.locks.StoreBusy(
                    f"could not read the scene's identity: {exc}") from exc
            time.sleep(_FENCE_RETRY_SECONDS)
    raise AssertionError("unreachable")           # pragma: no cover


_MOVED = ("the scene this turn started on is gone -- its id now names a "
          "different scene, so the reply was not saved")

_FENCE_READS = 3
_FENCE_RETRY_SECONDS = 0.05
"""How hard to try to READ the fence before giving up on the turn.

The conditions that make a header momentarily unopenable -- a sync client
mid-write, a Windows sharing violation -- last milliseconds, and this runs in a
worker thread where a short sleep costs nothing. Retrying is what keeps a
transient blip from ending a turn the provider spent minutes on; giving up
after a few is what keeps a genuinely stuck file from holding the campaign
lock indefinitely.
"""


#: The one `_chat_stream` task whose turn answers no player post -- an
#: ephemeral director note, which is never written to the transcript.
DIRECTOR = "director"


def _continuation_post(cid: str, sid: str, pid: str) -> int | None:
    """Which player post a continuation of proposal `pid` is answering.

    Read off the proposal record rather than re-derived from the transcript,
    and the difference is not academic: a continuation is the second half of a
    turn that already decided this question, and asking it again from here gets
    a DIFFERENT answer for the case the first half got right. A director turn
    is recorded against no post -- its note is ephemeral and never reaches the
    transcript -- but by the time its roll is accepted the last user message is
    some earlier, visible post, which a fresh derivation would charge for
    narration the player never asked for.

    A record that names no post therefore answers None, and so does a record
    that is gone or belongs to a different proposal. That is the honest
    fallback: the cost stays in the scene's totals either way, and a missing
    attribution is a smaller error than a wrong one.
    """
    try:
        rec = store.proposals.get(cid, sid)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("id") != pid:
        return None
    post = rec.get("post")
    if isinstance(post, bool) or not isinstance(post, int) or post < 0:
        return None
    return post


def _answering_post(messages: list[dict]) -> int | None:
    """The index of the player post a turn generated against this transcript is
    answering, or None when there is not one.

    The last user-role message, which is what every producing route is replying
    to: a send appends it and streams, a retry and a regenerate re-answer the
    one already sitting there, and a replay re-runs the turn that followed it.
    A scene whose transcript is all narration -- an opener, a director-driven
    scene before the player has said anything -- has no such post, and gets no
    attribution rather than a wrong one.

    Reads the message list defensively because it is the one thing here that
    came off disk: a `messages` that is not a list of dicts must cost the
    attribution, never the turn it is being attributed to.
    """
    if not isinstance(messages, list):
        return None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and message.get("role") == "user":
            return index
    return None


def _fence_stream(cid: str, sid: str, messages: list[dict], conn: dict,
                  client: LLMClient, finalize, on_error=None, on_abort=None,
                  task: str = "chat", outcome: StreamOutcome | None = None,
                  post: int | None = None, after_turn=None):
    """Stream one persisted turn while watching for a ```roll fence.

    Deltas are routed through a FenceWatcher, so an opener (even split across
    chunks) is never emitted and streaming stops once a fence closes. When the
    stream ends, `finalize(watcher)` (called with the lock/persist strategy of
    the caller — initial turn vs continuation) returns the trailing SSE frames
    (proposal / done). `on_error(watcher)` decides what to persist on an
    upstream LLM failure. Fence watching runs on persisted turns only;
    `_ephemeral_stream` is deliberately untouched.

    `task` is the label this turn's ledger row carries (#152) -- the meter is
    opened here rather than at each caller because this is the one place that
    sees every way a stream can end, and all three have to be recorded: a
    completed turn, a provider failure, and a client that walked away.

    `post` is the transcript index of the player post this turn is answering
    (#153), stamped on the row so a reader can ask what one post cost across
    every reroll of it. Passed by the caller rather than derived here, because
    the answer has to be read UNDER THE LOCK the caller already holds to claim
    the turn -- read from inside this generator it would be read after the claim
    and could see a transcript another writer had moved on. `None` for a turn
    that answers no post at all.

    `after_turn(at)` is what this turn asks for once it is over -- the rolling
    summary and the scene-break check, scheduled server-side rather than by a
    client that may no longer exist (#397). Fired from here because this is the
    one place that sees every way a stream can end, and only where the producer
    recorded a boundary through `StreamOutcome.persisted`; see
    `_fire_follow_up` for what it is and is not allowed to cost the turn.

    `on_abort(watcher)` is the same decision for a *disconnect* — the client
    cancelled, or the connection died — which arrives as cancellation rather
    than as an `LLMError` and so needs its own handler. It is deliberately a
    third hook and not a reuse of `on_error`: a hard failure and a deliberate
    cancel want different things done with a turn that produced nothing (a
    failure's orphaned user post is rolled back, a cancelled one is kept so the
    player can retry it), and only `on_error` is reached with a frame still to
    send. Callers whose abort case wants the ordinary end-of-turn writes pass
    `finalize` itself here and let its frames fall on the floor.
    """
    box = outcome if outcome is not None else StreamOutcome()

    async def event_stream():
        watcher = store.fence.FenceWatcher()
        # Opened before the request goes out, so `duration_ms` measures what the
        # user waited rather than what was left after the last delta.
        meter = store.usage.meter(task, campaign=cid, scene=sid, post=post)
        # Display only, and deliberately downstream of the watcher rather than
        # inside it: the tracker block is stripped from the transcript by
        # `_persist_reply`, but by then the deltas carrying it have already been
        # rendered. `watcher.narration` is untouched, so what gets persisted is
        # decided in exactly one place either way (#120).
        redactor = store.turnstate.StreamRedactor()
        try:
            async for delta in client.stream(messages, conn, meter.usage):
                if not delta:
                    yield _HEARTBEAT  # the facade is still waiting on the model
                    continue
                out = redactor.feed(watcher.feed(delta))
                if out:
                    yield _sse({"delta": out})
                if watcher.complete:
                    break  # stop-after-fence: ignore anything past the close
            tail = redactor.feed(watcher.finish()) + redactor.finish()
            if tail:
                yield _sse({"delta": tail})
            # Before `finalize`, and deliberately: the accounting is complete the
            # moment the provider stops, and the persist below can raise
            # StoreBusy and return early. A stop-after-fence `break` above skips
            # the provider's trailing usage frame, so those rows carry the
            # timing and the route and no token counts -- which is the honest
            # answer, and why the ledger records an absent price rather than a
            # zero one.
            meter.done()
        except LLMError as exc:
            # Flush the redactor too, and emit what it lets go BEFORE the error
            # frame. `on_error` persists `watcher.narration` whole, and
            # `split_block` strips only a TRAILING block -- so a partial that
            # ends in a `state` fence with narration after it is stored in full
            # while the redactor was still withholding all of it. Without this
            # the client would be missing text that a refresh then reveals.
            flushed = redactor.feed(watcher.finish()) + redactor.finish()
            if flushed:
                yield _sse({"delta": flushed})
            meter.done("error", exc.kind, detail=exc.detail)
            note: dict = {}
            if on_error is not None:
                try:
                    note = await run_in_threadpool(on_error, watcher) or {}
                except (store.locks.StoreBusy, store.scenes.SceneNotFound, OSError):
                    # on_error writes to the scene -- it persists the partial
                    # reply, and now may also take the user post back off (#95)
                    # -- so it can fail three ways. StoreBusy: the cross-process
                    # lock is contended (#234). SceneNotFound: the scene was
                    # renamed mid-turn, which mints a new id and moves the file
                    # out from under both calls (review caught this on the
                    # rollback; the persist has always had it). OSError: the
                    # attempt record would not open, and `attempts.forget`
                    # raises rather than mistaking that for an absent marker --
                    # so the rollback is refused BEFORE it deletes anything.
                    #
                    # That last one lands exactly where it should. `note` stays
                    # empty, so no `post_returned` reaches the client, so the
                    # client keeps its copy of the words -- and the post is
                    # still in the transcript, because the raise happened
                    # first. A duplicate the player can see and delete, which
                    # is the side of this ambiguity worth being wrong on.
                    #
                    # Neither may escape. The response has already started, so
                    # the global 409 handler cannot convert it, and an exception
                    # here would end the generator with no frame at all --
                    # truncating the stream instead of reporting the upstream
                    # failure that brought us here. The write is lost; the frame
                    # below still tells the user why.
                    pass
            # `note` carries what the handler *did*, not what went wrong: today
            # just `post_returned`, so the client can put the player's words
            # back in the composer instead of losing them (#95). Reported rather
            # than recomputed on the client, because only this side knows
            # whether the rollback actually fired -- it declines when the post
            # is no longer the tail, and a client guessing "failed with no text
            # ⇒ rolled back" would restore a prompt that is still in the
            # transcript and have the player send it twice.
            # The run FAILED, whatever the socket saw. The frame below is the
            # foreground half; this is the half a reconnect or a notification
            # reads, and inferring it from "the generator returned" made a
            # provider failure indistinguishable from a delivered reply.
            box.fail(exc.kind, exc.detail, **note)
            yield _sse({"error": {"detail": exc.detail, "kind": exc.kind, **note}})
            # A failed turn that kept a partial reply still grew the transcript,
            # so it still wants asking about (#397). After the frame, like the
            # success path below: the report of the failure is what the client
            # is waiting for.
            await _fire_follow_up(after_turn, box)
            return
        except BaseException:
            # Cancellation, `GeneratorExit`, or anything else that ends this
            # generator without going through the branch above. No frame can be
            # emitted -- the socket is gone and a generator being closed may not
            # yield -- so the only thing left to do is save the text, then let
            # the teardown continue: swallowing it here would tell Starlette the
            # response ended normally.
            watcher.finish()
            # `aborted`, not `error`: the player pressed Cancel or navigated
            # away, which is not a failure of anything and must not inflate an
            # error rate. It is still a row -- the provider generated, and on a
            # metered connection it was billed.
            meter.done("aborted")
            await _flush_on_abort(on_abort, watcher)
            # A Stop can flush a partial, and a flushed partial is a post: the
            # transcript grew, so both gates moved (#397). Nothing else will ask
            # -- the client's own `askAfterPost` went with this change, and this
            # path never reaches either of the calls below.
            #
            # SHIELDED, for `_flush_on_abort`'s reason: we are unwinding a
            # cancellation, so an unshielded await would be cancelled at its
            # first one and reserve nothing. Fail-soft is `_fire_follow_up`'s
            # own doing, which is what makes it safe to run here at all -- on
            # shutdown the portal is closing, and `_start_background` releases
            # a run it could not hand over.
            with anyio.CancelScope(shield=True):
                await _fire_follow_up(after_turn, box)
            raise
        try:
            # In a worker thread, not inline (#234). `finalize` is synchronous
            # and now waits on a cross-process lock, whose retry loop sleeps for
            # up to LOCK_TIMEOUT. This is an async generator driven by the event
            # loop, so an inline call would block that loop for the full 30s --
            # freezing every unrelated request and every other live stream on
            # this backend, not just the contended campaign.
            #
            # list(), not a bare call: finalize() runs OUTSIDE the try above,
            # and if it ever returns a generator, guarding only the call would
            # let StoreBusy escape from the `for` below -- outside this handler,
            # aborting the stream with no frame emitted at all. Materializing in
            # the worker also keeps any lazy body off the event loop.
            frames = await run_in_threadpool(lambda: list(finalize(watcher)))
        except store.locks.StoreBusy as exc:
            # Deliberately NOT routed through on_error: that persists
            # watcher.narration, and narration whose roll fence has no proposal
            # record destroys the proposal-before-narration guarantee this
            # ordering exists for.
            #
            # What this does NOT claim (review caught the overclaim): that
            # nothing was persisted. `finalize` is not transactional in
            # general -- a caller that writes the proposal under one lock,
            # releases it, and lets `_persist_reply` take it again leaves a
            # proposal on disk with no narration when that second acquisition
            # is the contended one. `_continuation_stream` is still shaped that
            # way; `_chat_stream` is not, since the identity fence put its whole
            # `finalize` under a single acquisition and the inner takes are
            # reentrant. Where it can happen it is the *sanctioned* direction:
            # exactly the recoverable state the documented fence crash-window
            # already produces, and the opposite order is the one that loses
            # data. What is guaranteed is
            # that the busy path adds no narration without its proposal, and
            # that the stream ends with a frame saying so instead of dying (#234).
            box.fail("busy", str(exc))
            yield _sse({"error": {"detail": str(exc), "kind": "busy"}})
            return
        box.land()
        for frame in frames:
            yield frame
        # LAST, after the outcome is decided and after every frame has gone out.
        # A subscriber that has walked away does not lose this: the producer is
        # always driven to exhaustion by `runs.start_detached`'s pump, which
        # buffers frames on the run rather than on the socket (#397).
        await _fire_follow_up(after_turn, box)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _chat_stream(cid: str, sid: str, messages: list[dict], conn: dict, client: LLMClient,
                 undo_user_post=None, restore_removed=None, task: str = "chat",
                 *, identity: str | None, outcome: StreamOutcome | None,
                 after_turn=None):
    """A normal persisted turn. A ```roll fence cuts the stream: the pending
    proposal record is written *before* the pre-fence narration persists, so a
    transcript that ends at a mechanical decision point always has a
    recoverable proposal (see the crash-window disclosure above).

    `undo_user_post` makes the turn transactional (#95). The caller appends the
    player's message before the stream starts, because the context builders
    render history out of the transcript and so need it already there; that
    leaves a window where an upstream failure strands a post with no reply and
    no way to tell it apart from one the model simply ignored. When a turn fails
    having produced *nothing*, this callback takes that post back off, so the
    scene ends up where it started. A turn that produced even a partial reply
    keeps both halves — the post is answered, just not fully — and a cancelled
    turn keeps its post too, since the player asked to stop and will likely
    retry from exactly there.

    `identity` is the scene identity the caller captured when it reserved the
    run, and it is the publish fence: every terminal write below re-reads the
    scene's identity under the campaign lock and refuses if it has changed. A
    detached turn can now outlive the scene it started on by minutes, and ids
    are recycled -- so without this, a turn held open while its scene is deleted
    and replaced finds a valid path at the id it captured and appends its reply
    to a different scene entirely. `None` disables the fence, which is what
    every caller that has not been migrated still passes.

    `outcome` is the box `_fence_stream` fills with what actually happened, so
    a detached run is not recorded `landed` on the strength of the generator
    having returned. See `StreamOutcome`.

    Both are KEYWORD-ONLY AND REQUIRED, with no defaults, and that is the point:
    the remaining producing routes are migrated one at a time, and a default
    would let a migration detach a turn while silently keeping the unfenced,
    always-`landed` behaviour -- which is corruption and a false success report
    respectively, neither of which announces itself. Passing `identity=None,
    outcome=None` is the old behaviour and stays available; it just has to be
    said out loud.
    """
    box = outcome if outcome is not None else StreamOutcome()
    # What the abort path checks before writing (see `on_abort`): this turn's
    # claim on the scene, and how long the transcript was when it began. Both,
    # because they catch different intruders — the token catches another turn,
    # including the three kinds that append nothing and so leave the length
    # unchanged; the length catches everything that is not a turn at all, like a
    # manual roll or a transition line. Read here, while the caller is still
    # synchronous and nothing else can be mid-write.
    #
    # Both under the campaign lock, which review caught the claim being outside
    # of: `_claim_turn` is a plain dict write, so a newer turn could take the
    # token in the gap between the abort hook's `_owns_turn` check and its tail
    # read — the hook would pass a check that was true when it ran and then act
    # on a scene that had since changed hands. The lock the hook holds only
    # makes its own steps indivisible if the writer it races takes it too.
    with store.locks.campaign_lock(cid):
        turn_token = _claim_turn(cid, sid)
        owned = store.scenes.read_scene(cid, sid)["messages"]
        owned_tail = len(owned)
        # Which player post this turn is answering (#153), read from the same
        # transcript snapshot the tail came from. `chat` has just appended it,
        # `retry` and `regenerate` are re-answering the one already there, and
        # all three land on the same index -- which is the point: a post and its
        # rerolls have to bucket together or the per-post figure would show only
        # the attempt still on screen.
        # Not a director turn: the player typed a note that is never written to
        # the transcript, so the generation answers no post of theirs. Charged
        # to the last one anyway it would inflate a post the player can see
        # with the cost of a turn they did not send.
        post = None if task == DIRECTOR else _answering_post(owned)

    def finalize(watcher) -> list[str]:
        # The WHOLE of it under one acquisition, which the identity fence
        # requires: read outside the lock that guards the writes, the check
        # answers a question that was true a moment ago. The lock is reentrant,
        # so the nested takes below (and `_persist_reply`'s own) cost nothing.
        with store.locks.campaign_lock(cid):
            if _scene_moved(cid, sid, identity):
                # The id still resolves -- to somebody else's scene. Writing
                # here is the corruption the fence exists for, and it is not a
                # case the client can be expected to sort out afterwards, so it
                # is reported as a failure rather than a quiet no-op.
                box.fail("scene_replaced", _MOVED)
                frames = [_sse({"error": {"kind": "scene_replaced", "detail": _MOVED}})]
            else:
                frames = _finalize_locked(watcher, [])
        # Outside the `with`, so this function has one exit mypy can see: a
        # context manager whose `__exit__` is typed `-> bool` may suppress, so a
        # `return` inside every branch of the block still reads as a possible
        # fall-through.
        return frames

    def _finalize_locked(watcher, frames: list[str]) -> list[str]:
        if watcher.complete or watcher.truncated:
            with store.locks.campaign_lock(cid):
                payload = _make_proposal(cid, sid, watcher)
                # `post` travels onto the record so the continuation that
                # resolves this roll is charged where this turn was -- see
                # `store.proposals.new`. A director turn passes None, and the
                # continuation inherits that rather than inventing one.
                rec = store.proposals.new(cid, sid, payload, post)  # heals before replacing
            _persist_reply(cid, sid, watcher.narration)
            # Read under the hold this whole function runs in, so the follow-ups
            # fold exactly the transcript this turn left (#397).
            box.persisted(_tail_length(cid, sid))
            frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        elif _persist_reply(cid, sid, watcher.narration):
            box.persisted(_tail_length(cid, sid))   # it landed; see above (#397)
        elif restore_removed is not None:
            # A turn that *succeeded* and produced nothing — a clean EOF with no
            # text and no fence, which a provider does return (an empty safety
            # response, a model that just stops). Review caught this as the one
            # terminal path the reroll's way back did not cover: `on_error` and
            # `on_abort` both restore, but this is neither, so `finalize` sent
            # `done` over a scene whose reply had been deleted and not replaced.
            # The success path is the one where losing it is least excusable,
            # because nothing looked wrong.
            #
            # Gated on still owning the turn, like the other two: restoring
            # appends, so a newer turn's tail must not have the old reply pushed
            # onto it. The user post is deliberately NOT taken back here — a
            # turn that ran and chose to say nothing is not a failed turn, and
            # the player can still see and reuse what they wrote (#95).
            with store.locks.campaign_lock(cid):
                if _owns_turn(cid, sid, turn_token):
                    restore_removed()
        frames.append(_sse({"done": True}))
        return frames

    def on_error(watcher) -> dict:
        """What to do with a turn the provider failed, and what to tell the
        client about it.

        The rollback is gated on this turn still holding the scene's claim, for
        the reason `on_abort` is: an overlapping retry or director note appends
        nothing, so index-and-tail still match A's post while B is generating
        from it, and the undo would delete the very post B is answering. Failing
        the check leaves an orphan, which is the direction this is allowed to be
        wrong in.

        Gating only the *rollback*, not the partial-reply persist beside it. The
        persist is additive and predates this branch; dropping it under
        contention would be a new way to lose text, decided as a rider on a fix
        for a different problem. Deleting is what needs the discipline.
        """
        # Under the lock, for the reason `on_abort` is: the check and the
        # rollback have to be one step. Review caught this path checking
        # ownership and then acting on it outside any lock, so a turn claiming
        # the scene in between would have its post deleted by the failed turn's
        # undo — the exact interleaving the check exists to prevent, just moved
        # a few lines later. Both writers take the lock now (`_claim_turn` since
        # the round before this one), so the window is closed rather than
        # narrowed.
        # The partial persist moved inside this same acquisition when the
        # identity fence was added: it is a terminal write like the others and
        # must not straddle the check that guards it.
        with store.locks.campaign_lock(cid):
            if _scene_moved(cid, sid, identity):
                # Neither half applies to a scene that is not this scene: the
                # partial belongs to a transcript that is gone, and the post to
                # roll back went with it. `_fence_stream` has already recorded
                # the provider failure that brought us here.
                return {}
            if _persist_reply(cid, sid, watcher.narration):
                # A partial is still a post, and still moves both gates (#397).
                box.persisted(_tail_length(cid, sid))
                return {}            # a normal turn keeps its partial reply
            if not _owns_turn(cid, sid, turn_token):
                return {}
            if restore_removed is not None:
                restore_removed()
            if undo_user_post is not None and undo_user_post():
                return {"post_returned": True}
        return {}

    def on_abort(watcher) -> list[str]:
        """Finish a cancelled turn exactly as a completed one — but only while
        it still owns the scene's tail.

        `finalize` itself, frames and all: they are discarded, since the socket
        that would have carried them is gone. Review caught the version that
        only persisted narration — a fence can close in the same chunk that
        carries the pre-fence text, so `watcher.complete` is already true at the
        yield a disconnect lands on, and persisting only the narration would end
        the transcript at a mechanical decision whose proposal record was never
        written: the check silently lost, and proposal-before-narration broken
        from the one direction the StoreBusy path takes such care to avoid.

        The ownership checks are the second half of the same review, in two
        rounds. Stop-then-send is a natural sequence, and this teardown runs
        after the socket closes — so the new turn can already be streaming, and
        may have appended its user post. Persisting then would file the
        cancelled turn's narration under a question it never answered, and a
        closed fence would mint a proposal displacing the live one.

        So: this turn must still hold the scene's claim (`_turn_tokens`), and
        the transcript must still be the length it was when this turn began. The
        token is what catches an overlapping retry or director note, neither of
        which moves the length; the length is what catches a writer that is not
        a turn at all, like a manual roll or a transition line from the
        inspector. Failing either, the partial is dropped — the same trade
        `_continuation_stream` makes, for the same reason.

        Under the campaign lock so the checks and the writes cannot be split.
        The lock is reentrant, so `finalize` re-taking it is free.
        """
        with store.locks.campaign_lock(cid):
            if _scene_moved(cid, sid, identity):
                # Ahead of the claim check, because it is the wider question:
                # `_turn_tokens` is keyed by `sid`, so a replacement scene that
                # recycled the id has not claimed anything and this turn still
                # reads as the owner of a scene it has never seen.
                return []
            if not _owns_turn(cid, sid, turn_token):
                return []
            # The restore is attempted BEFORE the length check, and review caught
            # why that ordering matters: the raw-length refusal exists to stop
            # this turn *adding* text to a transcript that moved on, but putting
            # back a reply this turn deleted is not adding anything — and the
            # things that move the length without claiming a turn are exactly
            # the ones the restore is built to tolerate. A location move or a
            # cast change appends a transition line, and
            # `restore_trailing_assistant_run` steps over trailing transitions
            # by design. Behind the coarse check, a reroll stopped after any of
            # those lost its reply for good.
            #
            # Safe to run first because it is not the unguarded version of the
            # same test: the helper compares the transcript *below* the trailing
            # transitions against what the removal recorded (`keep !=
            # token["kept"]` -> refuse), which is the narrow question the coarse
            # length check was standing in for.
            #
            # A cancel keeps the player's own post, because they still have it
            # and will likely retry from there. A reply this turn *deleted* to
            # make room for itself is the opposite: nothing else holds it, so a
            # reroll stopped before its first token has to put it back or the
            # player loses a reply they never asked to lose (#95).
            #
            # Restoring means rolling the whole turn back, `finalize` included:
            # a reply can be produced with no narration at all — an opener the
            # model leads with makes `narration` empty while the fence closes
            # (or, cut short here, truncates) — and finalizing that would mint a
            # proposal from context the restored reply was absent from. Review
            # caught the version that did both: accepting the roll would append
            # its continuation after an answer it never saw. So the two are
            # exclusive, and this is the side to take, because the reply is the
            # half nothing else holds; the proposal is one more reroll away.
            # The boolean is discarded deliberately, and review asked why. A
            # refusal here means something appended behind the deletion that
            # the restore will not step over — in practice a manual dice roll,
            # whose transcript line has to stay in lockstep with rolls.json and
            # so blocks the insert outright. There is no better second move
            # from inside this hook: `narration` is empty by the branch
            # condition, so there is nothing to persist instead, and forcing
            # the reply back above the roll would reorder the transcript
            # against the one invariant the roll line exists to hold.
            #
            # So the answer is not to lose the race: the roll button and the
            # check submit are locked for the whole of the flush window
            # (`sceneLocked`, the same signal rename and End scene use), which
            # is the only way a roll could land here from this client. Another
            # client — a second tab, a direct API call — can still do it, and
            # that is the wider concurrency class this PR documents rather than
            # closes (#95).
            if restore_removed is not None and not _would_land(cid, sid, watcher.narration):
                restore_removed()
                return []
            if len(store.scenes.read_scene(cid, sid)["messages"]) != owned_tail:
                return []
            return finalize(watcher)

    return _fence_stream(cid, sid, messages, conn, client, finalize, on_error, on_abort,
                         task=task, outcome=box, post=post, after_turn=after_turn)


def _continuation_stream(cid: str, sid: str, pid: str, messages: list[dict],
                         conn: dict, client: LLMClient, *,
                         identity: str | None, outcome: StreamOutcome | None,
                         after_turn=None):
    """Stream a proposal's continuation and commit it atomically. A supersede
    that lands mid-stream makes ``commit_narration`` return False and the
    streamed text is dropped. A follow-up fence in the continuation hands off
    under one lock: commit the old record's narration, then mint the new
    pending record, then emit its proposal event.

    `identity` and `outcome` mean what they do on `_chat_stream`, and are
    keyword-only and required for the same reason: this producer is detached
    too, so an adjudicated roll survives a locked phone -- which is exactly
    when a player looks away."""
    box = outcome if outcome is not None else StreamOutcome()
    # Inherited from the proposal, never re-derived -- see `_continuation_post`.
    post = _continuation_post(cid, sid, pid)

    def finalize(watcher) -> list[str]:
        # Under one acquisition with the fence, like `_chat_stream`'s: the
        # commit below is a terminal write and the check that guards it cannot
        # be split from it. Reentrant, so `commit_narration`'s own take is free.
        with store.locks.campaign_lock(cid):
            if _scene_moved(cid, sid, identity):
                box.fail("scene_replaced", _MOVED)
                frames = [_sse({"error": {"kind": "scene_replaced", "detail": _MOVED}})]
            else:
                frames = _finalize_continuation(watcher, [])
        # Outside the `with`, so this has one exit mypy can see -- see the same
        # shape in `_chat_stream.finalize`.
        return frames

    def _finalize_continuation(watcher, frames: list[str]) -> list[str]:
        # A continuation whose entire output was a tracker block persists no
        # post, and `commit_narration` marks the record `narrated` on the
        # strength of having CALLED persist, not on what it wrote. The proposal
        # would leave `resolved`/`declined` for good, every retry short-circuit
        # to `done`, and an adjudicated roll keep no narration at all — the one
        # loss this whole path is built to prevent. Left uncommitted, the record
        # stays committable and the next send re-streams it.
        #
        # Gated on the RAW narration being non-empty, so this changes nothing
        # about a continuation that genuinely produced no text: that has always
        # counted as narrated, and re-deciding it is not this branch's business.
        if watcher.narration.strip() and not _would_land(cid, sid, watcher.narration):
            frames.append(_sse({"done": True}))
            return frames
        persist = lambda: _persist_reply(cid, sid, watcher.narration)
        # Whether this continuation put anything in the transcript, which is
        # what decides if the follow-ups are worth scheduling (#397). Both
        # halves: the guard above has already returned for narration that would
        # not land, so non-empty narration plus a committed record is exactly
        # "a post was written" -- and an empty one commits too, marking the
        # record narrated without appending anything.
        wrote = bool(watcher.narration.strip())
        if watcher.complete or watcher.truncated:
            with store.locks.campaign_lock(cid):
                if store.proposals.commit_narration(cid, sid, pid, persist):
                    if wrote:
                        box.persisted(_tail_length(cid, sid))
                    payload = _make_proposal(cid, sid, watcher)
                    # new() heals the record it is about to erase; the lock is
                    # reentrant, so that projection is safe under ours.
                    # `post` carries forward: a follow-up roll inside the same
                    # turn is still answering whatever the first half was, and
                    # re-deriving it here has the failure `_continuation_post`
                    # exists to prevent, one hop further along.
                    rec = store.proposals.new(cid, sid, payload, post)
                    frames.append(_sse({"proposal": {**payload, "id": rec["id"]}}))
        elif store.proposals.commit_narration(cid, sid, pid, persist) and wrote:
            box.persisted(_tail_length(cid, sid))
        frames.append(_sse({"done": True}))
        return frames

    # No on_error, and no on_abort either: an upstream failure or a disconnect
    # mid-continuation drops the partial (nothing persisted) and leaves the
    # record resolved/declined, so a retry re-streams a fresh continuation
    # cleanly. Persisting here would be worse than losing the text — narration
    # committed outside `commit_narration` is narration a supersede can no
    # longer displace.
    return _fence_stream(cid, sid, messages, conn, client, finalize,
                         task="continuation", outcome=box, post=post,
                         after_turn=after_turn)


def ephemeral_frames(messages: list[dict], conn: dict, client: LLMClient,
                     task: str = "opener", cid: str = "", sid: str = "",
                     outcome: StreamOutcome | None = None):
    """The raw SSE frames of a generation that is persisted to no scene.

    The opener is the only member. Nothing about the turn is stored, but the
    call still cost tokens and money, so it is still metered (#152) --
    `cid`/`sid` only label the row.

    A FRAME PRODUCER rather than a response, which is what `runs.start_detached`
    takes: every frame is buffered on the run before it reaches anybody, so a
    client that goes away mid-opener reads the rest of it on the way back
    instead of losing the whole generation. Wrapped straight into a
    `StreamingResponse` this was un-detachable by construction, because the
    only copy of each frame was the one already on the wire.

    The `BaseException` arm still files the row and re-raises. It is no longer
    the disconnect path -- a subscriber leaving does not close this generator
    now -- but it is still the CANCEL path, which is `runner.cancel` unwinding
    the provider call, and an aborted row is the honest record of that.

    `outcome` is what the RUN records, and it is not optional in practice: this
    generator handles an upstream `LLMError` by emitting an error frame and
    then finishing normally, so "did not raise" covers both a delivered opener
    and a failed one. Without the box the runner infers `landed` from a clean
    exhaustion, and a client polling the run is told an opener arrived whose
    only terminal frame is an error -- the same defect `StreamOutcome` was
    built for on the persisted-turn side.
    """
    async def frames():
        meter = store.usage.meter(task, campaign=cid, scene=sid)
        try:
            async for delta in client.stream(messages, conn, meter.usage):
                if not delta:
                    yield _HEARTBEAT  # still waiting on the model (#95)
                    continue
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            meter.done()
            if outcome is not None:
                outcome.land()
            yield f"data: {json.dumps({'done': True})}\n\n"
        except LLMError as exc:
            meter.done("error", exc.kind, detail=exc.detail)
            if outcome is not None:
                # The provider's own kind and detail, exactly as the persisted
                # turns record theirs -- so a client reading a failed opener
                # off the run acts on the same `rate_limit` or `auth` it would
                # have read off the frame.
                outcome.fail(exc.kind, exc.detail)
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"
        except BaseException:
            # No frame can be emitted into a generator that is being closed, so
            # filing the row is the only thing left to do.
            meter.done("aborted")
            raise

    return frames


# ---- roll-proposal derivation and projection ----
# Used by the turn strategies above and by the roll-proposal routes in
# `mechanics`, which is why they live here rather than beside those routes.
def _make_proposal(cid: str, sid: str, watcher) -> dict:
    """Build the proposal payload from a closed/truncated fence: parse the
    body, resolve the actor against the scene's available checks (exact
    kind:id, then case-insensitive label), and collect `problems`. A proposal
    is never silently dropped — a bad one just opens the chip in Modify."""
    fields, problems = store.fence.parse_roll_body(watcher.body or "")
    if watcher.truncated:
        problems = [*problems, "roll fence truncated"]

    actors = store.checks.available_checks(cid, sid)
    available = {a["ref"]: a["checks"] for a in actors}

    actor_raw = fields.get("actor")
    actor, actor_label = None, actor_raw
    if actor_raw:
        for a in actors:
            if a["ref"] == actor_raw:
                actor, actor_label = a["ref"], a["label"]
                break
        if actor is None:
            for a in actors:
                if a["label"].lower() == str(actor_raw).strip().lower():
                    actor, actor_label = a["ref"], a["label"]
                    break
    if actor is None:
        problems = [*problems, "actor could not be resolved"]

    mid = store.modules.resolve(cid)
    check_labels: dict[str, str] = {}
    if mid is not None:
        pack = store.modules.load_pack(mid)
        cd = pack["checks"] if isinstance(pack["checks"], dict) else {}
        check_labels = {k: (v.get("label", k) if isinstance(v, dict) else k)
                        for k, v in cd.items() if k != "_defaults"}

    check = fields.get("check")
    if check is not None:
        if check not in check_labels:
            problems = [*problems, "unknown check id"]
        elif actor is not None and check not in dict(available.get(actor, [])):
            problems = [*problems, "check unavailable to this actor"]
    elif fields:
        # fields is non-empty (e.g. valid JSON with an actor but no `check`
        # key) yet carries no check id — parse_roll_body's own tolerant path
        # already flags this same case (see fence.py); a wholly unparseable
        # body (fields == {}) is left alone here since "roll request was
        # unparseable" already covers it and a second problem would be noise.
        problems = [*problems, "roll request had no check id"]

    return {"check": check, "check_label": check_labels.get(check, check),
            "actor": actor, "actor_label": actor_label,
            "difficulty": fields.get("difficulty"), "modifier": fields.get("modifier", 0),
            "reason": fields.get("reason", ""), "available": available,
            "problems": problems}


