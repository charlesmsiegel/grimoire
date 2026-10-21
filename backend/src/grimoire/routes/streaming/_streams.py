"""streaming: streams responsibilities."""

from __future__ import annotations

import anyio
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from . import _display, _followups, _persistence, _state


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


def _fence_stream(cid: str, sid: str, messages: list[dict], resolved,
                  client: LLMClient, *, finalize, on_error=None, on_abort=None,
                  task: str = "chat", outcome: _state.StreamOutcome | None = None,
                  post: int | None = None, after_turn=None):
    """Stream one persisted turn while watching for a ```roll fence.

    Deltas are routed through a FenceWatcher, so an opener (even split across
    chunks) is never emitted and streaming stops once a fence closes. When the
    stream ends, `finalize(watcher)` (called with the lock/persist strategy of
    the caller — initial turn vs continuation) returns the trailing SSE frames
    (proposal / done). `on_error(watcher)` decides what to persist on an
    upstream LLM failure. Fence watching runs on persisted turns only; the
    opener's stream (`greetings._opener_frames`) is deliberately untouched.

    `task` is the label this turn's ledger row carries (#152) -- the meter is
    opened here rather than at each caller because this is the one place that
    sees every way a stream can end, and all three have to be recorded: a
    completed turn, a provider failure, and a client that walked away. It is
    the task `inference.generate` is asked for, so `resolved` is the caller's
    resolution of that task (`inference.for_task` for a sibling on the scene
    route).

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
    box = outcome if outcome is not None else _state.StreamOutcome()
    asked = _display._asked(resolved)

    async def event_stream():
        watcher = store.fence.FenceWatcher()
        # Opened before the request goes out, so `duration_ms` measures what the
        # user waited rather than what was left after the last delta.
        meter = store.usage.meter(task, campaign=cid, scene=sid, post=post)
        # Display only, and deliberately downstream of the watcher rather than
        # inside it: the state block is stripped from the transcript by
        # `_persist_reply`, but by then the deltas carrying it have already been
        # rendered. `watcher.narration` is untouched, so what gets persisted is
        # decided in exactly one place either way.
        redactor = store.state_fence.StreamRedactor()
        liveness = _state._Liveness()
        try:
            # Display rules ride the same text, so the redaction and the fence
            # hiding above are unchanged: a `display` frame stands where the
            # `delta` would have, and counts from 0 on this path.
            display = _display._Display(cid, asked, meter)
            async for delta in operations.generate(task, messages, client=client,
                                                   resolved=resolved, usage=meter.usage):
                if not delta:
                    if liveness.due():
                        yield _state._HEARTBEAT  # the facade is still waiting on the model
                    continue
                out = redactor.feed(watcher.feed(delta))
                frames = await display.frames(out)
                if frames:
                    liveness.sent()
                    for frame in frames:
                        yield frame
                elif display.active and liveness.due():
                    # Text a display rule is hiding sends no bytes, and it can
                    # arrive as an unbroken run of non-empty deltas with no
                    # empty one to carry a heartbeat. Only with rules in force:
                    # a plain stream sends the bytes it always did.
                    yield _state._HEARTBEAT
                if watcher.complete:
                    break  # stop-after-fence: ignore anything past the close
            tail = redactor.feed(watcher.finish()) + redactor.finish()
            for frame in await display.frames(tail, last=True):
                yield frame
            # Before `finalize`, and deliberately: the accounting is complete the
            # moment the provider stops, and the persist below can raise
            # StoreBusy and return early. A stop-after-fence `break` above skips
            # the provider's trailing usage frame, so those rows carry the
            # timing and the route and no token counts -- which is the honest
            # answer, and why the ledger records an absent price rather than a
            # zero one.
            watcher.connection = _display._served(meter, asked)
            meter.done()
        except LLMError as exc:
            # Flush the redactor too, and emit what it lets go BEFORE the error
            # frame. `on_error` persists `watcher.narration` whole, and
            # `split_block` strips only a TRAILING block -- so a partial that
            # ends in a `state` fence with narration after it is stored in full
            # while the redactor was still withholding all of it. Without this
            # the client would be missing text that a refresh then reveals.
            flushed = redactor.feed(watcher.finish()) + redactor.finish()
            for frame in await display.frames(flushed, last=True):
                yield frame
            watcher.connection = _display._served(meter, asked)
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
            yield _state._sse({"error": {"detail": exc.detail, "kind": exc.kind, **note}})
            # A failed turn that kept a partial reply still grew the transcript,
            # so it still wants asking about (#397). After the frame, like the
            # success path below: the report of the failure is what the client
            # is waiting for.
            await _followups._fire_follow_up(after_turn, box, cid, sid)
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
            watcher.connection = _display._served(meter, asked)
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
                await _followups._fire_follow_up(after_turn, box, cid, sid)
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
            yield _state._sse({"error": {"detail": str(exc), "kind": "busy"}})
            return
        box.land()
        for frame in frames:
            yield frame
        # LAST, after the outcome is decided and after every frame has gone out.
        # A subscriber that has walked away does not lose this: the producer is
        # always driven to exhaustion by `runs.start_detached`'s pump, which
        # buffers frames on the run rather than on the socket (#397).
        await _followups._fire_follow_up(after_turn, box, cid, sid)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _chat_stream(cid: str, sid: str, messages: list[dict], resolved, client: LLMClient,
                 undo_user_post=None, restore_removed=None, task: str = "chat",
                 *, identity: str | None, outcome: _state.StreamOutcome | None,
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
    box = outcome if outcome is not None else _state.StreamOutcome()
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
        turn_token = _state._claim_turn(cid, sid)
        owned = store.scenes.read_scene(cid, sid)["messages"]
        owned_tail = len(owned)
        # Which player post this turn is answering (#153), read from the same
        # transcript snapshot the tail came from. `chat` has just appended it,
        # `retry` and `regenerate` are re-answering the one already there, and
        # all three land on the same index -- which is the point: a post and its
        # rerolls have to bucket together or the per-post figure would show only
        # the attempt still on screen.
        post = _persistence._answering_post(owned)
        # A director turn attributes to its own note and to nothing else.
        #
        # A TYPED note is written to the transcript now, so `_answering_post`
        # finds it and the cost lands on the line that bought it -- which is
        # the whole reason notes are stored, and what also makes a retry or a
        # reroll of that turn bucket with it. The other two director turns
        # store nothing: an offscreen scene has no note, and an empty send
        # means "next NPC round". For those the scan reaches some earlier,
        # visible post, and charging it would inflate a post the player can see
        # with the cost of a turn they did not send -- the same error
        # `_continuation_post` refuses for the same reason.
        if task == _persistence.DIRECTOR and not _persistence._is_director_note(owned, post):
            post = None

    def finalize(watcher) -> list[str]:
        # The WHOLE of it under one acquisition, which the identity fence
        # requires: read outside the lock that guards the writes, the check
        # answers a question that was true a moment ago. The lock is reentrant,
        # so the nested takes below (and `_persist_reply`'s own) cost nothing.
        with store.locks.campaign_lock(cid):
            if _persistence._scene_moved(cid, sid, identity):
                # The id still resolves -- to somebody else's scene. Writing
                # here is the corruption the fence exists for, and it is not a
                # case the client can be expected to sort out afterwards, so it
                # is reported as a failure rather than a quiet no-op.
                box.fail("scene_replaced", _persistence._MOVED)
                frames = [_state._sse({"error": {"kind": "scene_replaced", "detail": _persistence._MOVED}})]
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
                payload = _persistence._make_proposal(cid, sid, watcher)
                # `post` travels onto the record so the continuation that
                # resolves this roll is charged where this turn was -- see
                # `store.proposals.new`. A director turn passes None, and the
                # continuation inherits that rather than inventing one.
                rec = store.proposals.new(cid, sid, payload, post)  # heals before replacing
            _persistence._persist_reply(cid, sid, watcher.narration, watcher.connection)
            # Read under the hold this whole function runs in, so the follow-ups
            # fold exactly the transcript this turn left (#397).
            box.persisted(_persistence._tail_length(cid, sid))
            frames.append(_state._sse({"proposal": {**payload, "id": rec["id"]}}))
        elif _persistence._persist_reply(cid, sid, watcher.narration, watcher.connection):
            box.persisted(_persistence._tail_length(cid, sid))   # it landed; see above (#397)
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
                if _state._owns_turn(cid, sid, turn_token):
                    restore_removed()
        # Once, at the one exit, rather than in each branch above: a proposal
        # record with no narration behind it, a restored reply, and a post that
        # landed are all writes this turn made after its status line went out.
        # The branch that wrote nothing at all bumps too, which costs a caller
        # holding an older token one re-price and is the direction to be wrong
        # in (`_turn_settled`).
        _persistence._turn_settled(cid)
        frames.append(_state._sse({"done": True}))
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
            if _persistence._scene_moved(cid, sid, identity):
                # Neither half applies to a scene that is not this scene: the
                # partial belongs to a transcript that is gone, and the post to
                # roll back went with it. `_fence_stream` has already recorded
                # the provider failure that brought us here.
                return {}
            if _persistence._persist_reply(cid, sid, watcher.narration, watcher.connection):
                # A partial is still a post, and still moves both gates (#397).
                box.persisted(_persistence._tail_length(cid, sid))
                return {}            # a normal turn keeps its partial reply
            if not _state._owns_turn(cid, sid, turn_token):
                return {}
            if restore_removed is not None:
                restore_removed()
            if undo_user_post is not None and undo_user_post():
                # Taking the post back off is a transcript write like appending
                # it was, and the one that a caller holding a token from before
                # the send would otherwise never see.
                _persistence._turn_settled(cid)
                return {"post_returned": True}
            _persistence._turn_settled(cid)
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
            if _persistence._scene_moved(cid, sid, identity):
                # Ahead of the claim check, because it is the wider question:
                # `_turn_tokens` is keyed by `sid`, so a replacement scene that
                # recycled the id has not claimed anything and this turn still
                # reads as the owner of a scene it has never seen.
                return []
            if not _state._owns_turn(cid, sid, turn_token):
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
            if restore_removed is not None and not _persistence._would_land(cid, sid, watcher.narration):
                restore_removed()
                return []
            if len(store.scenes.read_scene(cid, sid)["messages"]) != owned_tail:
                return []
            return finalize(watcher)

    return _fence_stream(cid, sid, messages, resolved, client, finalize=finalize,
                         on_error=on_error, on_abort=on_abort, task=task, outcome=box, post=post, after_turn=after_turn)


def _continuation_stream(cid: str, sid: str, pid: str, messages: list[dict],
                         resolved, client: LLMClient, *,
                         identity: str | None, outcome: _state.StreamOutcome | None,
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
    box = outcome if outcome is not None else _state.StreamOutcome()
    # Inherited from the proposal, never re-derived -- see `_continuation_post`.
    post = _persistence._continuation_post(cid, sid, pid)

    def finalize(watcher) -> list[str]:
        # Under one acquisition with the fence, like `_chat_stream`'s: the
        # commit below is a terminal write and the check that guards it cannot
        # be split from it. Reentrant, so `commit_narration`'s own take is free.
        with store.locks.campaign_lock(cid):
            if _persistence._scene_moved(cid, sid, identity):
                box.fail("scene_replaced", _persistence._MOVED)
                frames = [_state._sse({"error": {"kind": "scene_replaced", "detail": _persistence._MOVED}})]
            else:
                frames = _finalize_continuation(watcher, [])
        # Outside the `with`, so this has one exit mypy can see -- see the same
        # shape in `_chat_stream.finalize`.
        return frames

    def _finalize_continuation(watcher, frames: list[str]) -> list[str]:
        # A continuation whose entire output was a state block persists no
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
        if watcher.narration.strip() and not _persistence._would_land(cid, sid, watcher.narration):
            frames.append(_state._sse({"done": True}))
            return frames
        persist = lambda: _persistence._persist_reply(cid, sid, watcher.narration, watcher.connection)
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
                        box.persisted(_persistence._tail_length(cid, sid))
                    payload = _persistence._make_proposal(cid, sid, watcher)
                    # new() heals the record it is about to erase; the lock is
                    # reentrant, so that projection is safe under ours.
                    # `post` carries forward: a follow-up roll inside the same
                    # turn is still answering whatever the first half was, and
                    # re-deriving it here has the failure `_continuation_post`
                    # exists to prevent, one hop further along.
                    rec = store.proposals.new(cid, sid, payload, post)
                    frames.append(_state._sse({"proposal": {**payload, "id": rec["id"]}}))
        elif store.proposals.commit_narration(cid, sid, pid, persist) and wrote:
            box.persisted(_persistence._tail_length(cid, sid))
        # Both branches wrote, whatever `wrote` says about the transcript:
        # `commit_narration` marks the record narrated even when the
        # continuation persisted no post, and the first minted a replacement
        # proposal on top of that (#409, `_turn_settled`).
        _persistence._turn_settled(cid)
        frames.append(_state._sse({"done": True}))
        return frames

    # No on_error, and no on_abort either: an upstream failure or a disconnect
    # mid-continuation drops the partial (nothing persisted) and leaves the
    # record resolved/declined, so a retry re-streams a fresh continuation
    # cleanly. Persisting here would be worse than losing the text — narration
    # committed outside `commit_narration` is narration a supersede can no
    # longer displace.
    return _fence_stream(cid, sid, messages, resolved, client, finalize=finalize,
                         task="continuation", outcome=box, post=post,
                         after_turn=after_turn)
