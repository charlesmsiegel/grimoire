"""A reserved send owns one fenced setup and its transactional reply handoff."""

from __future__ import annotations

import contextlib
import uuid

from fastapi import Depends, Header, HTTPException, Request

from ... import inference as operations
from ... import prompts, store
from ...llm import LLMClient
from .. import character_turns, history_recall, runs, streaming
from .. import tracker as tracker_routes
from ..common import (
    UsableInference,
    _record_prompt,
    _require_scene,
    _turn_override,
    get_embeddings,
    get_llm,
    override_inference,
    require_inference,
)
from ..models import ChatTurn, RegenerateBody, RetryBody
from ..streaming import StreamOutcome, _chat_stream
from . import _followups
from ._router import router as router


def _disown_dead_pending(cid: str, sid: str) -> None:
    """Re-aim a dead reroll's hint AND route before streaming something that
    sent neither.

    A reroll whose stream died leaves its pending pair parked for "whatever
    lands next" (`store.alternates`). Every other way of streaming into that
    empty slot — Retry, and the empty-send / director turn, which persist no
    player message — sent neither the hint nor the route, so the take they
    produce must not be labelled with either.

    Both halves, and named for both since #77 gave the pair a second field.
    The call below passes them both explicitly rather than leaning on
    `archive`'s defaults, which is the same visibility the `disown_guidance` ->
    `disown_pending` rename was made for: re-aiming only the hint would credit
    the take that lands with a route it was not generated on, and a signature
    default is not where a reader looks to find that out.

    Only over an *empty* slot. Above a live reply these paths append a
    consecutive generation rather than replacing one, which moves the slot and
    retires the set anyway; archiving there would park a reply nobody asked to
    replace. A normal send needs none of this: it appends a player message,
    which moves the anchor and retires the set on its own.
    """
    parked = store.alternates.state(cid, sid)
    if parked["runs"] and parked["active"] is None:
        # Both fields said out loud rather than left to the signature's
        # defaults, so the next reader can see that the route is re-aimed too.
        store.alternates.archive(cid, sid, guidance="", model="")


@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, request: Request,
              client: LLMClient = Depends(get_llm),
              embed_client=Depends(get_embeddings),
              x_grimoire_attempt: str | None = Header(default=None)):
    # BEFORE the preflight checks, and that order is the whole point of an
    # attempt id. This is a REPLAY: the work already ran and its outcome is
    # buffered, so a client that lost the original response must get that
    # outcome back. Behind `require_inference` it would not -- a connection
    # removed or re-keyed in the meantime answers `missing_key`, telling the
    # player a turn that actually landed did not, which is the exact ambiguity
    # the attempt id exists to remove. No provider is needed to replay.
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    # A rejected one-shot target must leave the player's post untouched.
    _turn_override(turn)
    _require_scene(cid, sid)
    if character_turns.enabled() or turn.speaker_ref:
        character_turns.validate_actor(cid, sid, turn.speaker_ref)
    resolved = _chat_resolution(cid, sid, turn)
    if character_turns.enabled():
        # The speaker pick's refusal, ahead of the reservation for the reason
        # below. The kind is `_chat_run`'s: a post unless the send is
        # ephemeral, then a note when one was typed, else a continue.
        character_turns.refuse_an_unanswerable_pick(
            cid, sid, actor_ref=turn.speaker_ref,
            kind=("post" if not _is_ephemeral(cid, sid, turn)
                  else "note" if turn.director and turn.content.strip() else "continue"))
    # RESERVED BEFORE THE FIRST MUTATOR. `heal` can append a line and the
    # sidecar block can retire a proposal, so a 409 raised after them would
    # tell the player nothing happened when something already had. The
    # reservation is also what makes a second send on this scene refusable
    # rather than merely discouraged.
    run, fresh = runs.reserve_turn(request.app, cid, sid, "chat",
                                   x_grimoire_attempt)
    if not fresh:
        # This exact POST already ran -- a duplicate delivery after a lost
        # response. Replay it rather than doing the work twice.
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _chat_run(cid, sid, turn, request, client, resolved, run,
                         embed_client=embed_client)


def _chat_resolution(cid: str, sid: str, turn: ChatTurn) -> UsableInference | None:
    """The send's `chat` resolution -- or None for a send that generates
    nothing (`character_turns.answers_nothing`), which a missing key does not
    refuse."""
    try:
        return require_inference("chat", cid)
    except HTTPException as exc:
        # A Manual-mode post naming no speaker generates nothing, so a missing
        # connection is no reason to refuse it. None tells the engine none was
        # resolved; it requires one after all if its plan turns out to speak.
        if not (isinstance(exc.detail, dict) and exc.detail.get("kind") == "missing_key"
                and character_turns.answers_nothing(
                    cid, sid, director=turn.director, content=turn.content,
                    speaker_ref=turn.speaker_ref)):
            raise
        return None


def _is_ephemeral(cid: str, sid: str, turn: ChatTurn) -> bool:
    """Whether a send posts no player dialogue: a director note steering one
    generation -- asked for outright (`director`, the composer's Direct
    mode), implied by an offscreen scene, which has no other kind of turn, or
    implied — in any scene — by an empty send meaning "next NPC round"."""
    return bool(turn.director or store.scenes.is_pcless(cid, sid)
                or not turn.content.strip())


def _take_the_post_back(cid: str, sid: str, posted_at, content: str, run,
                        post_id: str | None = None) -> bool:
    """Remove the player's post AND retire the record that says it is there.

    One function because the two have to be one step. `on_error` calls this
    inside its campaign-lock hold, so both land together; split apart -- the
    removal here and the record cleared on the way out -- there is a window
    where the transcript has lost the post and the record still claims it, and
    a recovery landing in that window settles and discards the only surviving
    copy of what the player typed.

    **The record is retired FIRST, and the order is the fail-safe.** These are
    two files, so the lock makes them one step for a concurrent reader but not
    for a process that exits between them. Whichever way round, one crash
    window is unavoidable; the choice is which side of it the player lands on:

    - forget, then remove: the marker says "gone" while the post is still
      there. Recovery believes it, hands the words back, and the player sees a
      DUPLICATE they can read and delete.
    - remove, then forget: the post is gone while the marker still says
      "retained". Recovery believes that, settles the attempt, and the only
      copy of what the player typed is discarded with no trace at all.

    The same asymmetry every ambiguity in this feature is resolved by, and the
    reason a `forget` that raises must not be swallowed either -- letting it
    out leaves the post in place, which is the recoverable side.
    """
    store.attempts.forget(cid, run.scene_identity, run.attempt_id)
    removed = store.scenes.remove_trailing_user_post(cid, sid, posted_at, content)
    if removed:
        # The post's stored-rewrite record holds what the player typed, which
        # the failed turn hands back to them; it does not stay on disk too.
        streaming._prune_rewrites(cid, sid)
    if removed and post_id:
        # The post's tracker record goes in the same hold, whether its update
        # already landed or is still on its way (`tracker.after_take_back`).
        tracker_routes.after_take_back(cid, run.scene_identity,
                                       store.tracker.paths.post_key(post_id))
    return removed


def _chat_run(cid: str, sid: str, turn: ChatTurn, request: Request,
              client: LLMClient, resolved: UsableInference | None, run, *, embed_client=None):
    """The body of a send, once the scene is reserved.

    Split out so `runs.reservation` can wrap every exit from it. The run is
    published and discoverable from the moment it is reserved, so a path that
    returns or raises in here without handing a producer to the runner leaves
    it `running` for the life of the process -- never reaped, and refusing
    every later turn on the scene. There are more such paths than there are
    `return` statements (any mutator below can raise), which is why this is a
    wrapper rather than a checklist.
    """
    # A turn that posts no player dialogue (`_is_ephemeral`)...
    ephemeral = _is_ephemeral(cid, sid, turn)
    # ...of which exactly one kind leaves a record. A typed note is a thing the
    # player wrote and paid for a generation against, and it is stored as a
    # marked user message so that generation can be attributed to it --
    # `usage.record`'s `post` is a transcript index, and until now a director
    # turn was the one generation in the app whose cost had nowhere to sit.
    #
    # The other two store nothing, and must not: an empty send has no note, and
    # the default text `prompts.render` supplies for it is this module's
    # wording rather than the player's. Writing that into the transcript would
    # be the app putting words in their mouth once per NPC round.
    note_text = turn.content.strip() if turn.director else ""
    # Heal, then the sidecar, then retire — the same split regenerate makes, and
    # for the same reason. `_disown_dead_pending` writes a file that can refuse
    # the write, and it used to run AFTER the retirement: an empty send over an
    # unwritable sidecar then retired a recoverable decision and returned 500
    # having generated nothing. Heal still leads, because it is what can append
    # a 🎲 line, and the sidecar has to resolve against the transcript that
    # leaves behind. One lock, so a resolution cannot land between the three.
    #
    # The player's post joined them under that SAME acquisition when review
    # caught what the terminal identity fence does not cover. `reserve_turn`
    # captures the scene's identity and then returns; every mutator below used
    # to run outside any fence, so a scene deleted and replaced in that window
    # got this turn's `heal`, its retired proposal and its player post -- and
    # the fence, which only guards the terminal WRITE, would then refuse the
    # reply and leave the replacement holding somebody else's post with no
    # answer coming. Setup is now one locked, fenced unit: either all of it
    # lands on the scene the run captured, or none of it does.
    with store.locks.campaign_lock(cid):
        if streaming._scene_moved(cid, sid, run.scene_identity):
            raise HTTPException(status_code=404, detail="scene not found")
        store.proposals.heal(cid, sid)
        if ephemeral:
            _disown_dead_pending(cid, sid)
        store.proposals.supersede(cid, sid)  # a new send retires any pending decision
        posted_at, content, speaker, post_id, tracked = None, "", None, None, None
        note_at = None
        if note_text:
            # In the same locked, fenced hold the player's post is written in,
            # and for its reasons. Macros are expanded at persist time exactly
            # as they are for a post, so a `{{roll:1d20}}` in a note cannot
            # re-roll on every later read of the scene.
            #
            # Assistant-role, because that is what the format means: the role
            # is derived from the label on read and `You` is the only label
            # that means user. `DIRECTOR_SPEAKER` is what tells it apart, and
            # it is in `SYNTHETIC_SPEAKERS`, so nothing counts it as a reply.
            #
            # **There is no undo for it, unlike the player's post.** A post
            # with no reply is a half-turn nobody asked for and comes back off;
            # a note is a thing the player wrote, and a failed generation does
            # not unwrite it. Removing it silently would discard the only copy
            # of what they typed, which is the side of that ambiguity every
            # other decision here refuses (see `_take_the_post_back`). Left in
            # place it is visible and deletable, and re-sending is a fresh
            # note rather than a resurrection.
            content = store.context.expand_macros(
                note_text, store.context.scene_substitutions(cid, sid), cid, sid)
            note_at = store.scenes.append_message(
                cid, sid, "assistant", content,
                speaker=store.scenes.DIRECTOR_SPEAKER)
        if not ephemeral:
            names = store.appearances.player_names(cid, sid)
            speaker = names[0] if len(names) == 1 else None
            if speaker:
                store.scenes.stamp_user_speaker(cid, sid, speaker)
            # Macros resolved once at persist time (#137): a player's
            # {{roll:1d20}} must not re-roll on every later context build
            # (retry, next turn, ...).
            original = store.context.expand_macros(
                turn.content, store.context.scene_substitutions(cid, sid), cid, sid)
            # The store phase (regex spec 5.1), on the expanded post and before
            # it is written. `content` is what the transcript holds, which is
            # also what the take-back below matches the post by.
            content, fired = store.regex.view.store_phase(original, cid=cid, role="user")
            # The id is minted here, in the same hold as the append, for every
            # new post whatever the tracker's setting: the tracker keys a
            # record to a user post by it (an index renumbers on every cut),
            # and a post written while it was off is still one a campaign that
            # switches it back on can track (Retry on an untracked post).
            post_id = uuid.uuid4().hex
            posted_at = store.scenes.append_message(
                cid, sid, "user", content, speaker=speaker, post_id=post_id)
            if fired:
                streaming._record_rewrite(cid, sid, post_id, original, fired, content)
            # The tracker's `pending` mark, in the hold that wrote the post:
            # the acquisition is reentrant here, where after the hold it would
            # be a second wait on the campaign lock in front of the turn.
            # `None` with the tracker off.
            tracked = tracker_routes.mark(cid, sid, store.tracker.paths.post_key(post_id))
            # In the SAME hold as the append it describes. This is what lets a
            # recovery after the run record expired ask the only decisive
            # question -- is this attempt's post still here? -- rather than
            # matching text, which is not an identifier.
            #
            # Swallowed, and only here. `attempts` refuses to rewrite a file it
            # could not read (see `_read`), which on the ROLLBACK path is what
            # keeps a post from being deleted against a marker still saying it
            # is there. On this path the same refusal would fail an otherwise
            # good send over a bookkeeping file, and the cost of the missing
            # marker is a recovery that hands back words already in the
            # transcript -- a duplicate, the recoverable side again.
            with contextlib.suppress(OSError):
                store.attempts.remember(cid, run.scene_identity, run.attempt_id)
    if tracked:
        # The player's post is a tracked post of its own: what they said or did
        # can change the scene's state before anyone answers. Marked `pending`
        # inside the hold above; the run is started here, after it, because
        # reserving one needs no campaign lock and goes through the lifespan
        # portal. Started before the turn, so the post's update queues ahead of
        # the reply's on the scene's tracker lock and the two run in transcript
        # order. Fail-soft: a run that cannot start marks the record `failed`.
        # If the turn later takes the post back, the record goes with it
        # (`tracker.after_take_back`), landed or not.
        tracker_routes.start(request.app, cid, sid, tracked, client, run.scene_identity)
    if character_turns.enabled() or turn.speaker_ref:
        return _start_round(cid, sid, turn, request, client, resolved, run, ephemeral=ephemeral,
                            posted_at=posted_at, content=content, post_id=post_id,
                            embed_client=embed_client)
    resolved = _resolved_for_a_turn(resolved)
    primary = resolved.chain.primary
    if ephemeral:
        # `content` when a note was stored (macros already resolved, so the
        # model sees exactly what the transcript holds), the template's default
        # otherwise. `compose_director_turn` still appends it as the final user
        # message and `_project_history` drops the stored copy, so the prompt
        # is byte-identical to what it was before notes were persisted.
        note = content or prompts.render("scene/director_note.j2")
        view = history_recall.gather_view_sync(
            request.app, cid, sid, seed=note, resolved=resolved,
            llm_client=client, embed_client=embed_client, run_id=run.id, post=note_at,
            identity=run.scene_identity)
        messages, breakdown = store.context.compose_director_turn(
            cid, sid, note, turn=_turn_override(turn),
            describe=store.prompt_log.capturing(), model=primary.model,
            images=store.post_images.images_for(primary), history_view=view)
        # AFTER the stream is constructed, not before. `_chat_stream` claims the
        # turn under the campaign lock synchronously, before it returns -- so a
        # contended campaign raises StoreBusy there and nothing is ever sent.
        # Recording first would leave Turn history showing a request the model
        # never saw. The generator body has not run at this point; only the
        # claim has.
        outcome = StreamOutcome()
        # A director turn runs where the send resolved `chat` to: one route,
        # one resolution (`for_task`), metered and generated as `director`.
        stream = _chat_stream(cid, sid, messages, operations.for_task(resolved, "director"),
                              client, task="director",
                              identity=run.scene_identity, outcome=outcome,
                              after_turn=_followups._follow_up_hook(request.app, cid, sid, client))
        _record_prompt(cid, sid, "director", breakdown,
                       model=primary.model, kind=primary.kind, messages=messages,
                       conn=resolved.chain)
        # DETACHED, like the ordinary send below. This branch used to return the
        # response directly, which left its reservation running forever -- the
        # scene answered `run_in_flight` from then on -- while the generation
        # itself still died with the socket. It persists a reply like any other
        # turn; only the player's note is ephemeral.
        runs.start_detached(request.app, run, lambda: stream.body_iterator,
                            outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    view = history_recall.gather_view_sync(
        request.app, cid, sid, seed="", resolved=resolved,
        llm_client=client, embed_client=embed_client, run_id=run.id, post=posted_at,
        identity=run.scene_identity)
    messages, breakdown = store.context.compose_turn(
        cid, sid, turn=_turn_override(turn),
        describe=store.prompt_log.capturing(), model=primary.model,
        images=store.post_images.images_for(primary), history_view=view)

    # The post has to precede the stream — `build_messages` renders history out
    # of the transcript, so a turn the model never sees is a turn it cannot
    # answer — which is exactly what makes a failed generation able to strand
    # it. Hand `_chat_stream` the undo so the pair is transactional (#95): if
    # the turn produces nothing at all, the post comes back off. `posted_at`
    # travels with it because nothing holds a lock across the stream, so by the
    # time the undo runs the tail may belong to a different turn entirely.
    # The producer is unchanged; what changed is who consumes it. `runner`
    # drives it on the lifespan loop and buffers every frame on the run, so the
    # work outlives this request -- a locked phone or a backgrounded tab drops
    # a subscriber, not the turn. The response tails that same buffer.
    #
    # Called HERE, synchronously, and only its iteration is deferred.
    # `_chat_stream` claims the turn under the campaign lock before it returns,
    # so a contended campaign raises `StoreBusy` at this line and the route
    # answers 409 having sent nothing -- exactly as it did before. Building it
    # inside the runner's factory instead moved that claim off the request, and
    # a contended turn started answering 200 and failing later, which
    # `test_a_turn_that_never_claims_records_nothing` caught.
    outcome = StreamOutcome()
    stream = _chat_stream(
        cid, sid, messages, resolved, client,
        undo_user_post=lambda: _take_the_post_back(cid, sid, posted_at, content, run,
                                                   post_id),
        task="chat", identity=run.scene_identity, outcome=outcome,
        after_turn=_followups._follow_up_hook(request.app, cid, sid, client))
    # AFTER the stream is built, never before: `_chat_stream` claims the turn
    # and can raise, and a prompt recorded ahead of it leaves Turn history
    # showing a request the model never saw (`test_a_turn_that_never_claims_
    # records_nothing`).
    _record_prompt(cid, sid, "chat", breakdown,
                   model=primary.model, kind=primary.kind, messages=messages,
                   conn=resolved.chain)
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _resolved_for_a_turn(resolved: UsableInference | None) -> UsableInference:
    """`resolved`, which a send that reaches a turn outside the round engine
    always has: only the round engine is handed none (a Manual-mode post that
    speaks to nobody), and `_chat_run` hands that to it first. Checked rather
    than asserted, so the guarantee holds under `python -O` too."""
    if resolved is None:
        raise RuntimeError("a turn outside the round engine was handed no resolution")
    return resolved


def _start_round(cid: str, sid: str, turn: ChatTurn, request: Request,
                 client: LLMClient, resolved: UsableInference | None, run, *,
                 ephemeral: bool, posted_at,
                 content: str, post_id: str | None, embed_client=None):
    """`_chat_run`'s hand-off to the round engine (`character_turns.start`).

    A refusal from `start` comes before any round opened (its defence
    against a settings change since `refuse_an_unanswerable_pick`), and the
    answer is then an error -- so the post this send wrote comes back off, as
    a failed turn's does. A director note stays, for `note_text`'s reasons."""
    try:
        return character_turns.start(
            cid,sid,request,client,resolved,run=run,post=posted_at,
            note=(content or prompts.render("scene/director_note.j2")) if ephemeral else "",
            turn=_turn_override(turn),automatic=not ephemeral,
            actor_ref=turn.speaker_ref,after_turn=_followups._follow_up_hook(request.app,cid,sid,client),
            kind="post" if not ephemeral else ("note" if content else "continue"),
            trigger=content,
            # The player's own words only: the template above is app wording.
            typed_note=content if ephemeral else "",
            embed_client=embed_client, history_enabled=True)
    except HTTPException:
        if posted_at is not None:
            with store.locks.campaign_lock(cid):
                _take_the_post_back(cid, sid, posted_at, content, run, post_id)
        raise


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, request: Request, body: RetryBody | None = None,
               client: LLMClient = Depends(get_llm),
               embed_client=Depends(get_embeddings),
               x_grimoire_attempt: str | None = Header(default=None)):
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _turn_override(body)
    scene = _require_scene(cid, sid)
    resolved = require_inference("retry", cid)
    # Ahead of the retirement, not behind it: a refusal must not cost a decision
    # for a request that then does nothing at all. Ahead of the RESERVATION too:
    # a run reserved for a request that was never going to do anything has to be
    # released again, and not taking it is simpler than unwinding it.
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to retry")
    run, fresh = runs.reserve_turn(request.app, cid, sid, "retry", x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _retry_run(cid, sid, body, request, client, resolved, run,
                          embed_client=embed_client)


def _retry_run(cid: str, sid: str, body, request: Request,
               client: LLMClient, resolved: UsableInference, run, *, embed_client=None):
    """The body of a retry, once the scene is reserved -- see `_chat_run` for
    why every exit from it has to be wrapped rather than audited."""
    if character_turns.enabled():
        return character_turns.retry(cid,sid,request,client,resolved,run,
                                      _followups._follow_up_hook(request.app,cid,sid,client),
                                      embed_client=embed_client)
    # Same order as the send above, for the same reason — see there. Fenced like
    # it too: a scene replaced between the reservation and here must not collect
    # this turn's heal and retired proposal.
    with store.locks.campaign_lock(cid):
        if streaming._scene_moved(cid, sid, run.scene_identity):
            raise HTTPException(status_code=404, detail="scene not found")
        store.proposals.heal(cid, sid)
        _disown_dead_pending(cid, sid)
        store.proposals.supersede(cid, sid)  # a fresh generation retires the old decision
    primary = resolved.chain.primary
    view = history_recall.gather_view_sync(
        request.app, cid, sid, seed="", resolved=resolved,
        llm_client=client, embed_client=embed_client, run_id=run.id,
        identity=run.scene_identity)
    messages, breakdown = store.context.compose_turn(
        cid, sid, turn=_turn_override(body),
        describe=store.prompt_log.capturing(), model=primary.model,
        images=store.post_images.images_for(primary), history_view=view)
    outcome = StreamOutcome()
    stream = _chat_stream(cid, sid, messages, resolved, client,   # claims the turn; see above
                          task="retry", identity=run.scene_identity, outcome=outcome,
                          after_turn=_followups._follow_up_hook(request.app, cid, sid, client))
    _record_prompt(cid, sid, "retry", breakdown,
                   model=primary.model, kind=primary.kind, messages=messages,
                   conn=resolved.chain)
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _restore_reroll(cid: str, sid: str, removed: dict):
    """Put the outgoing reply back, and take the hint back with it.

    Two halves of one undo. The transcript half is #95's: a reroll that produces
    nothing must not cost the reply it deleted. The sidecar half is this PR's —
    `archive` recorded the guidance against a replacement that never landed, and
    with the original run live again `_resolve` would credit it with an
    instruction it was not generated from, then persist that on the next write.

    Best-effort on the second, deliberately: the reply is the artifact that
    cannot be regenerated, and a wrong label is not worth failing a restore for.
    """
    def restore() -> None:
        store.scenes.restore_trailing_assistant_run(cid, sid, removed)
        with contextlib.suppress(OSError):
            store.alternates.disown_pending(cid, sid)
    return restore


@router.post("/campaigns/{cid}/scenes/{sid}/regenerate")
def post_regenerate(cid: str, sid: str, request: Request,
                    body: RegenerateBody | None = None,
                    client: LLMClient = Depends(get_llm),
                    embed_client=Depends(get_embeddings),
                    x_grimoire_attempt: str | None = Header(default=None)):
    """Redo the most recent post: park the trailing assistant reply as an
    alternate, stream a fresh one."""
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _turn_override(body)
    _require_scene(cid, sid)
    if character_turns.enabled():
        messages = store.responses.migrate_if_needed(cid, sid)["messages"]
        rid = next((m["response_id"] for m in reversed(messages) if m.get("response_id")),None)
        if rid is None:
            raise HTTPException(400,detail="no response to regenerate")
        return character_turns.regenerate_response(cid,sid,rid,request,body,client,x_grimoire_attempt)

    # The one-shot route override (#77), resolved here rather than inside
    # `_regenerate_run`: a body naming a connection that does not exist, or one
    # with no key, must refuse BEFORE the reservation below — past it the route
    # has archived and removed the outgoing reply, and a 400 raised there would
    # report a rejected request over a scene that is one reply short.
    # `resolved` is always the resolution that serves and `routed` says whether it is
    # somewhere this turn would not have gone anyway — two answers, because the
    # stamps below need the second and the generation needs the first, and a
    # single sentinel could not carry both without the caller re-resolving.
    # The task literal lives here, at the call site, because this is where a
    # generation knows what it is: a reroll of a scene reply routes exactly
    # where the reply it replaces would have (#142).
    resolved, routed = override_inference(body, "regenerate", cid)
    # RESERVED BEFORE THE FIRST MUTATOR, which matters more here than anywhere
    # else: this route archives the outgoing reply and removes it from the
    # transcript before the replacement exists, so a 409 raised afterwards
    # would report "nothing happened" over a scene that is one reply short.
    run, fresh = runs.reserve_turn(request.app, cid, sid, "regenerate",
                                   x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _regenerate_run(cid, sid, body, request, client, resolved, run, routed=routed,
                               embed_client=embed_client)


def _regenerate_run(cid: str, sid: str, body, request: Request,
                    client: LLMClient, resolved: UsableInference, run, *, routed: bool, embed_client=None):
    """The body of a reroll, once the scene is reserved -- see `_chat_run`."""
    guidance = (body.guidance or "").strip() if body else ""
    # What this reroll will actually be sent to, after `override_inference`
    # has folded in whatever the body asked for. Read off the resolution's
    # primary target rather than off the body, so it names the resolved route
    # in every case -- an override naming only a connection reports THAT
    # connection's model, and a Claude connection with none configured
    # reports the one the dispatcher substitutes rather than the empty string
    # it stores.
    primary = resolved.chain.primary
    ran_on = primary.model
    # `routed` is whether this turn ran somewhere it would NOT have gone
    # anyway -- not merely whether the caller typed something. A body naming
    # the active connection at its own model is not an override, and review
    # caught the earlier reading of this flag turning such a request into one
    # that displaced the snapshot stamp below. Decided once, by
    # `override_inference`, which compares the resolved route against the
    # standing one on the EFFECTIVE model; re-deriving it here off `body` would
    # be a second place for that rule to drift. The two stamps below answer to
    # it differently -- and deliberately, because they answer different
    # questions:
    #
    # - The ALTERNATE is stamped on every reroll. It is the only record a
    #   variant has of the generation that produced it, and the whole point of
    #   the set is reading variants side by side; "which model wrote this one"
    #   is answerable nowhere else. Always stamping also catches the case an
    #   override-only rule would miss, since the scene's `model` frontmatter is
    #   written once at creation and goes stale the moment the active
    #   connection is repointed. "" is then left meaning "no record" -- every
    #   pre-#77 variant, and every variant reconciled out of the transcript.
    # - The SNAPSHOT is stamped only when the route was overridden, because its
    #   `model` is a field the LIVE context panel reports for the same scene
    #   too, and `store.prompt_log` argues at length that the two agreeing
    #   matters more than either being exactly right. A deliberate one-shot
    #   override is the one case where they are describing different things
    #   (this turn, versus the next one) rather than drifting apart.
    removed: dict | None = None   # set only when there is actually a reply to drop
    # ONE lock across the heal, the decision, the archive and the removal. A gap
    # anywhere in that span is a gap another writer's generation can land in —
    # and the removal would then take a reply the archive never saw, losing
    # exactly what the non-destructive guarantee promises to keep. Held only
    # across a read and two file writes; the stream starts after it is released.
    with store.locks.campaign_lock(cid):
        # Fenced first, like every other setup block: a scene replaced between
        # the reservation and here must not have its reply archived and removed
        # by a turn that started on a different scene entirely.
        if streaming._scene_moved(cid, sid, run.scene_identity):
            raise HTTPException(status_code=404, detail="scene not found")
        # Heal now, retire later. Healing is what can append a 🎲 line, and the
        # checks below have to judge the transcript that leaves behind — but
        # RETIRING the decision waits until the reroll has actually committed to
        # happening, or a failure cancels a decision whose narration is still
        # exactly what the reader sees.  `heal` is idempotent and `supersede`
        # calls it again itself.
        #
        # INSIDE the lock, not before it: `heal` is a no-op on a proposal that
        # is still `resolving`, and the resolution needs this same lock to
        # persist. Healing outside it let that resolution land in the gap, so
        # the read below saw no roll line, the guards passed, and the line only
        # appeared when `supersede` healed it — after the narration that
        # produced the roll had already been removed. Reentrant, so `heal`
        # taking it again is free.
        store.proposals.heal(cid, sid)
        replacing = _reroll_replaces(cid, sid)
        # Archive BEFORE the removal, and let the replacement join the set on
        # its own when the next read reconciles: no callback has to fire deep
        # inside the stream's persist path, and a stream that dies between the
        # two leaves the outgoing reply recoverable rather than gone.
        #
        # Called even with nothing to remove. There is no run to keep then, but
        # the hint still has to be re-aimed at whatever lands next, or the
        # variant that does land is filed under the *previous* attempt's
        # guidance. `archive` writes nothing at all for a scene that has no set,
        # so the refusals above still leave the scene untouched.
        store.alternates.archive(cid, sid, guidance, ran_on)
        # The durable half of the hint (store/steering.py): the alternates
        # copy above is a display label that dies with the set, this one is
        # what the end-of-scene absorb reads. Unconditional — a stream that
        # dies does not un-say the correction, and the error banner's Retry
        # re-sends the same guidance, which consecutive-dedupe absorbs.
        if guidance:
            store.steering.record(cid, sid, guidance)
        if replacing:
            removed = _remove_outgoing_reply(cid, sid)
        # Built BEFORE the retirement below, not after: `supersede` writes
        # proposals.json and can fail, and until this exists there is nothing
        # holding the way back — the reply would be gone with the decision it
        # was derived from still pending and still acceptable.
        restore = _restore_reroll(cid, sid, removed) if removed else None
    # Everything from here to the `return` runs with the scene one reply short,
    # and until the stream exists there is nothing holding the way back: the
    # restore hooks live inside `_chat_stream`'s generator, so a raise here
    # would delete a reply and hand the caller a 500 with no trace of it.
    # Reachable without any race — `build_messages` reads the whole store, and
    # `prompts.render` compiles a template (review, #95).
    try:
        # rendered before the context build so its tokens can be reserved against
        # the context budget -- it is appended unconditionally, so the packer must
        # not fit the prompt to a ceiling this then pushes it over. Named in
        # `appended` rather than reserved-then-appended, so the snapshot reports
        # the guidance the model actually read (#157).
        block = prompts.render("scene/regenerate_guidance.j2", guidance=guidance) if guidance else ""
        view = history_recall.gather_view_sync(
            request.app, cid, sid, seed="", resolved=resolved,
            llm_client=client, embed_client=embed_client, run_id=run.id,
            identity=run.scene_identity)
        messages, breakdown = store.context.compose_turn(
            cid, sid, turn=_turn_override(body),
            appended=(("Regenerate guidance", "system", block),) if block else (),
            describe=store.prompt_log.capturing(), model=primary.model,
            images=store.post_images.images_for(primary), history_view=view)
        # LAST, because it is the one step with no way back. Everything that can
        # refuse has now refused — the guards, the removal, and the setup above,
        # which reads the whole store and compiles a template and so can fail on
        # its own — and the stream is the next statement. Retiring any earlier
        # cancelled a decision whose narration the restore then put straight
        # back, still valid and no longer resolvable.
        #
        # Outside the lock, unlike the archive and the removal: `supersede`
        # takes it itself, and holding it across the context build would
        # stretch a span documented as a read and two writes over the slowest
        # part of the request.
        store.proposals.supersede(cid, sid)
    except BaseException:
        if restore is not None:
            restore()
        raise
    # AFTER the supersede, not beside the compose that built it. Everything above
    # can still refuse and unwind the reroll without the model ever being called,
    # and a snapshot written before that point would leave Turn history showing a
    # regeneration that never happened. From here the stream is the next
    # statement, so a recorded turn is one that was really attempted. `record`
    # cannot raise, so this adds no failure of its own to a path that has just
    # passed its last one.
    # The old reply had to go before the context was built — the builders read
    # the transcript, so the model cannot be asked to replace something it can
    # still see. That leaves a window where the scene is one reply short and the
    # replacement does not exist yet, and a generation producing nothing would
    # end it there: a reply destroyed by a reroll the player stopped or that
    # never started (#95). Hand the stream the way back.
    #
    # The parked alternate is the other half of that guarantee and survives
    # either way: `archive` ran before the removal, so a stream that never
    # lands leaves the reply recoverable through the swipe control even if the
    # restore hook never fires.
    #
    # One window this does NOT close, deliberately, because closing it is a
    # redesign rather than a guard: between this return and the generator's
    # first step, the response body can be cancelled outright — uvicorn reports
    # ASGI spec 2.3, so Starlette races `stream_response` against
    # `listen_for_disconnect` in a task group, and an already-queued disconnect
    # cancels the former before it runs. An async generator that never started
    # runs none of its body on close, so no hook here can fire. The fix is to
    # stop deleting ahead of the replacement at all — remove the old run inside
    # `finalize`, under the same lock that writes the new reply — which is
    # tracked with the other transcript-identity work rather than bolted on.
    outcome = StreamOutcome()
    stream = _chat_stream(cid, sid, messages, resolved, client, restore_removed=restore,
                          identity=run.scene_identity, outcome=outcome,
                          task="regenerate",
                          after_turn=_followups._follow_up_hook(request.app, cid, sid, client))
    # Last of all: after `supersede` (which can refuse and unwind the reroll) AND
    # after the turn claim inside `_chat_stream` (which can raise StoreBusy on a
    # contended campaign). Both would leave Turn history showing a regeneration
    # the model never saw.
    # The frozen panel names this attempt; the live panel resolves the next
    # ordinary turn independently, so a one-shot override cannot leak into it.
    _record_prompt(cid, sid, "regenerate", breakdown,
                   model=primary.model, kind=primary.kind, messages=messages,
                   conn=resolved.chain)
    # `on_unstarted` is `restore` again, for the one path the stream's own hooks
    # cannot cover: a Stop that arrived while this route was still in the
    # synchronous setup above. The runner honours it with a checkpoint BEFORE
    # entering the producer, so the generator's `finally` -- where `restore`
    # otherwise lives -- never runs, and review caught what that leaves behind:
    # a reroll reported `cancelled` with the old reply removed and no
    # replacement, permanently one reply short.
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result, on_unstarted=restore)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _reroll_replaces(cid: str, sid: str) -> bool:
    """Whether a reroll replaces the trailing reply -- False when it streams
    into the empty slot a dead reroll left -- refusing, as a 400, a transcript
    with nothing a reroll could replace. Called in the hold that heals, archives
    and removes, after the heal."""
    # Read AFTER the heal, which can append a 🎲 line the pre-heal snapshot
    # doesn't have. Judging the checks below on a stale snapshot let the
    # ROLL_SPEAKER guard pass and `remove_trailing_assistant_run` then
    # refuse (IndexError -> 500) — it never deletes a roll line, but the
    # caller deserves the 400 instead.
    msgs = _require_scene(cid, sid)["messages"]
    if not msgs:
        raise HTTPException(status_code=400, detail="nothing to regenerate")
    # Trailing scene transitions are stepped over, not consumed: reroll
    # targets the last generation BENEATH them, and every check below has to
    # look at that generation rather than at the transition line on top.
    core = msgs[:len(msgs) - store.scenes.trailing_transitions(msgs)]
    # An archived set whose slot is EMPTY means the previous reroll's stream
    # died before its replacement landed. Generations can sit back to back
    # (an empty send and a director turn persist no player message), so what
    # is exposed at the tail is then the generation BEFORE that slot —
    # removing it would delete a reply nobody asked to reroll and carry the
    # slot past the parked one, losing both. Stream into the empty slot.
    parked = store.alternates.state(cid, sid)
    replacing = (bool(core) and core[-1]["role"] == "assistant"
                 and not (parked["runs"] and parked["active"] is None))
    if not replacing:
        return False
    if all(m["role"] == "assistant" for m in core):
        raise HTTPException(status_code=400,
                            detail="cannot regenerate the opening post")
    if core[-1].get("speaker") in store.scenes.SYNTHETIC_SPEAKERS:
        # Enumerated from the same tuple `remove_trailing_assistant_run`
        # refuses on, not from one speaker name: only ROLL_SPEAKER is
        # reachable here (trailing transitions are already stripped
        # above), but a future synthetic speaker gets this 400 rather
        # than the bare IndexError (500) that refusal would surface.
        raise HTTPException(status_code=400,
                            detail="cannot regenerate past a manual dice roll")
    return True


def _remove_outgoing_reply(cid: str, sid: str) -> dict:
    """Take the reply a reroll replaces out of the transcript, returning what
    `_restore_reroll` puts back. Called after `archive` recorded the hint, in
    the same hold."""
    try:
        return store.scenes.remove_trailing_assistant_run(cid, sid)
    except Exception as exc:
        # The archive recorded the hint against a replacement that is now
        # never going to land, and the reply it was meant to replace is still
        # live. Left there, the next resolution credits that unchanged reply
        # to an instruction it never received.
        #
        # Best-effort: whatever stopped the removal (a full disk, most
        # likely) may stop this too, and the original failure is the one
        # worth reporting.
        with contextlib.suppress(OSError):
            store.alternates.disown_pending(cid, sid)
        if isinstance(exc, store.scenes.TurnSizesDesynced):
            # Refusing beats guessing: the recorded turn boundaries don't
            # fit the transcript, so any deletion could take blocks from
            # an earlier generation. The transcript is untouched.
            raise HTTPException(
                status_code=400,
                detail="this scene's recorded turn boundaries no longer match its "
                       "transcript — delete the last reply manually to regenerate") from exc
        raise
