"""One detached run drives a bounded chain of persisted rounds and rescues only its own unfinished work."""

from __future__ import annotations

import logging
from contextlib import aclosing

import anyio
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import prompts, store
from ...llm_errors import LLMError
from .. import epistemic as epistemic_stage
from .. import history_recall, runs, streaming
from ..common import require_inference
from . import _generation, _persistence, _planning, _prompts, _state
from ._router import router as router

_log = logging.getLogger('grimoire.character_turns')


def start(
    cid,
    sid,
    request,
    client,
    resolved,
    *,
    run,
    post=None,
    note="",
    turn=None,
    automatic=False,
    actor_ref=None,
    after_turn=None,
    round_record=None,
    appended=(),
    continuation=None,
    kind="note",
    trigger="",
    typed_note="",
    embed_client=None,
    history_enabled=False,
):
    """Open (or resume) a round and hand its frames to a detached run.

    `kind` is what asked for a fresh round -- `post` (a player post),
    `continue` (an empty send) or `note` (a director note, a replay) -- and
    `trigger` the text it answers. Together with the scene's group-play order
    they plan the round's speakers (`_plan`). A resumed round (`round_record`
    given: a retry, a roll resume) is never re-planned.

    A `resolved` of None is the caller saying no connection was needed
    (`answers_nothing`). If the plan made here says otherwise after all -- the
    order changed in between -- the connection is required now.

    `resolved` is whatever resolution of the scene route opened the round
    (a send's `chat`, a retry's, a roll's `continuation`, a replay's): every
    contribution runs on it, under its own task (`_round_frames`)."""
    with store.locks.campaign_lock(cid):
        claim = _state._Claim.take(cid, sid, run)
        claim.fence()
        if round_record is None:
            planned = _planning._plan(cid, sid, kind, trigger, actor_ref)
            if resolved is None and (planned["actor_ref"]
                                     or not (automatic and planned["mode"] == "manual")):
                resolved = require_inference("chat", cid)
            if (planned["actor_ref"] is None and len(planned["eligible"]) > 1
                    and not (automatic and planned["mode"] == "manual")):
                # The first speaker will be picked (`_first_actor` -> `_select`).
                # `refuse_an_unanswerable_pick` already asked this before the
                # send wrote anything; this is the defence for a settings change
                # in between, and refusing here, rather than inside the run,
                # keeps the refusal a 409 instead of an opaque `run_failed`.
                # `post_chat` takes its post back when it lands. `_select`
                # resolves again in the run, so a change after this is still
                # honoured.
                require_inference("response-selector", cid, operation="decide")
            chain = {}
            if kind == "post" and planned["mode"] != "manual":
                # The rounds a player post may run on for (`_follow_on`);
                # Manual never auto-continues.
                rounds = _planning._settings(cid, sid)["auto_rounds"]
                chain = {"auto_remaining": rounds, "auto_total": rounds, "round_index": 1}
            round_record = store.responses.new_round(
                cid,
                sid,
                automatic=automatic,
                post=post,
                run_id=run.id,
                note=note,
                turn=turn,
                **planned,
                **chain,
                typed_note=typed_note,
            )
    outcome = streaming.StreamOutcome()
    frames = _frames(
        claim,
        client,
        resolved,
        round_record,
        outcome,
        app=request.app,
        after_turn=after_turn,
        appended=appended,
        continuation=continuation,
        embed_client=embed_client,
        history_enabled=history_enabled,
    )
    runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _pause(claim, turn, actor, outcome):
    """Park the contribution in flight on the roll its fence proposed: the
    proposal, the round paused on it with `actor` to resume, and the prose
    before the fence saved as an incomplete variant (`_save`)."""
    cid, sid = claim.cid, claim.sid
    record, round_record, continuation = turn.record, turn.round_record, turn.continuation
    with store.locks.campaign_lock(cid):
        claim.fence()
        if continuation:
            store.proposals.commit_narration(cid, sid, continuation, lambda: None)
            streaming._prune_rewrites(cid, sid)   # what the commit trimmed
        payload = streaming._make_proposal(cid, sid, turn.watcher.roll)
        proposal = store.proposals.new(cid, sid, payload, round_record["post"])
        _state._round_state(
            cid,
            sid,
            round_record,
            status="paused",
            pending_response=record["id"],
            actor_ref=actor,
            proposal_id=proposal["id"],
        )
        # Proposal and paused identity survive before any pre-fence prose.
        # The prose before the roll fence is a post the transcript shows, so it
        # is tracked; the resumed part marks the finished variant again, over
        # the whole response.
        at = _persistence._save(claim, turn, "incomplete")
        _state._round_state(
            cid,
            sid,
            round_record,
            status="paused",
            pending_response=record["id"],
            actor_ref=actor,
            proposal_id=proposal["id"],
        )
        outcome.persisted(at)
        streaming._turn_settled(cid)
        return proposal


async def _frames(
    claim,
    client,
    resolved,
    round_record,
    outcome,
    *,
    app=None,
    after_turn=None,
    appended=(),
    continuation=None,
    embed_client=None,
    history_enabled=False,
):
    """One run carries a player post's whole chain of rounds: the first, then
    each follow-on `_follow_on` opens while rounds remain, announced by a
    `round_start` frame. The turn settles once, after the last of them."""
    cid, sid, run = claim.cid, claim.sid, claim.run
    turn = _state._Progress(round_record, appended, continuation, streaming._asked(resolved))
    try:
        actor, turn.round_record = await _planning._first_actor(cid, sid, client, round_record)
        while True:
            round_frames = _round_frames(
                claim, client, resolved, turn, actor, outcome,
                app=app, embed_client=embed_client, history_enabled=history_enabled
            )
            async with aclosing(round_frames) as frames:
                async for frame in frames:
                    yield frame
            if turn.terminal:
                return
            if not _planning._chain_continues(turn.round_record, turn.ending, run.cancel_requested):
                break
            following = await run_in_threadpool(
                _planning._follow_on, claim, turn.round_record, turn.ending["lead"]
            )
            if following is None:
                break
            turn.round_record = following
            yield streaming._sse(
                {
                    "round_start": {
                        "index": following["round_index"],
                        "of": following["auto_total"] + 1,
                    }
                }
            )
            if run.cancel_requested:
                # Stopped on the announcement: no selector call, and the
                # round ends below with nobody asked to speak (`_stop`).
                actor = None
                continue
            actor, turn.round_record = await _planning._first_actor(cid, sid, client, following)
        outcome.land()
        yield streaming._sse({"done": True})
    except store.responses.ResponseConflict as exc:
        _state._abort_meter(turn.meter)
        # A lost scene/turn fence must never rescue into its replacement.
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except LLMError as exc:
        await _rescue(claim, turn, outcome, exc)
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except BaseException:
        with anyio.CancelScope(shield=True):
            await _rescue(claim, turn, outcome)
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await streaming._fire_follow_up(after_turn, outcome, cid, sid)
            await _state._start_tracking(app, cid, sid, client, turn.tracked, run.scene_identity)


def _history_replays(cid, sid, round_record):
    pending = round_record.get("pending_response")
    if not pending:
        return False
    try:
        return store.responses.get(cid, sid, pending, private=True)["status"] != "complete"
    except Exception:  # noqa: BLE001 -- only a hint: _prepare decides under the lock, and fails there if it must
        return False


def _round_post(cid, sid):
    try:
        return streaming._answering_post(store.scenes.read_scene(cid, sid)["messages"])
    except Exception:  # noqa: BLE001 -- an unattributable post costs the rerank's `post`, never the round
        return None


async def _round_history(app, cid, sid, client, embed_client, resolved, turn, actor, *,
                         enabled=True, run=None):
    """One step's history view, for that step's own perspective (11-S3).

    The narrator and each NPC gather separately: an NPC's query is built from
    what it observed and its view holds only what it may see, so one step's
    retrieval is never another's. The driver keeps one immutable view per
    (round, speaker), including an empty or failed one. The outside-lock hint
    only avoids wasted work; _prepare owns replay, and a stale pending peek may
    waste a gather but cannot replace the snapshot _prepare chooses under its
    campaign hold.
    """
    if not enabled:
        return None
    key = (turn.round_record["id"], actor)
    if turn.appended or turn.continuation:
        # A roll continuation never retrieves, but the second half of a reply
        # is composed with the same view its first half was, when this round
        # already gathered it (a resume in a later request has none).
        return turn.history_by_round.get(key)
    # Off reads nothing past the config: no replay peek, no transcript.
    if not await run_in_threadpool(history_recall.enabled):
        return None
    if await run_in_threadpool(_history_replays, cid, sid, turn.round_record):
        return None
    if key not in turn.history_by_round:
        narrator = actor == "grimoire"
        post = turn.round_record.get("post")
        if post is None and turn.round_record.get("typed_note"):
            # A stored director note has an index even though the round's post
            # field records player-role posts only. An untyped continue stays
            # unattributed, rather than charging some earlier visible post.
            post = await run_in_threadpool(_round_post, cid, sid)
        view = await history_recall.gather_view(
            app, cid, sid,
            # A director note steers the narrator; no character heard it.
            seed=(turn.round_record.get("note") or "") if narrator else "",
            resolved=resolved, llm_client=client, embed_client=embed_client,
            post=post, run_id=run.id if run is not None else "",
            actor_ref=None if narrator else actor,
            identity=run.scene_identity if run is not None else None,
        )
        # The round's history cost so far, carried on each step's view so its
        # row shows what recall per speaker has cost (spec 11 §14 Q7).
        spent = turn.history_cost.setdefault(turn.round_record["id"], [0, 0])
        if view is not None:
            # A planned step retrieves (and may embed) once per round (10).
            spent[0] += history_recall.retrievals(view)
            spent[1] += history_recall.embeds(view)
        turn.history_by_round[key] = history_recall.costed(view, *spent)
    if run is not None and run.cancel_requested:
        raise anyio.get_cancelled_exc_class()()
    return turn.history_by_round[key]


async def _refine_step(cid, sid, client, turn, actor, view, response_id, run=None):
    """`(view, record, response_id)`: one actor step's history with spec
    11's Decision stage folded in (`routes/epistemic.refine`, §5), asked once
    per (round, speaker) and never by the narrator, a roll continuation or a
    replay (`_round_history` hands a replay no view). Off, the view comes back
    as it went in and nothing past the switch is read. A call that sent parts
    charges the round's budget on its record, so a resumed round keeps it."""
    if view is None or actor in (None, "grimoire") or turn.appended or turn.continuation:
        return view, None, response_id
    key = (turn.round_record["id"], actor)
    if key in turn.refined:
        return view, turn.refined[key], response_id
    refined = await epistemic_stage.refine(
        view, cid=cid, sid=sid, client=client, post=turn.round_record.get("post"),
        round_id=turn.round_record["id"],
        asked=epistemic_stage.asked_in(turn.round_record), response_id=response_id)
    if refined.sent:
        spent = epistemic_stage.asked_in(turn.round_record) + refined.sent
        try:
            turn.round_record = await run_in_threadpool(
                store.responses.update_round, cid, sid, turn.round_record["id"],
                **{epistemic_stage.ROUND_FIELD: spent})
        except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn (§5.4)
            # Fail closed: the round reads as spent for the rest of this
            # request, so a count that could not be kept never asks more.
            _log.error("epistemic round budget not recorded for %s/%s: %s", cid, sid,
                       type(exc).__name__)
            turn.round_record = {**turn.round_record,
                                 epistemic_stage.ROUND_FIELD: epistemic_stage.EPISTEMIC_ROUND_ITEMS}
    turn.history_by_round[key] = refined.view
    turn.refined[key] = refined.record
    if run is not None and run.cancel_requested:
        raise anyio.get_cancelled_exc_class()()
    return refined.view, refined.record, refined.response_id




async def _settle_recovered_successor(cid, sid, client, round_record, after, cancelled):
    """A persisted reply may still owe its Decision pick after a crash."""
    if not after.get("needs_decision"):
        return round_record, after
    used, actor = after["used"], after["actor"]
    if after["content_issue"] or cancelled:
        choice = _planning._successor_answer(round_record, used, actor, None, after["content_issue"])
    else:
        choice = await _planning._select_successor(
            cid, sid, client, round_record, used, actor, after["response_id"])
    if cancelled:
        choice = _planning._successor_answer(round_record, used, actor, None)
    ending = {**choice, "issue": _planning._ending_issue(
        round_record, choice["issue"], after["content_issue"])}
    settled = await run_in_threadpool(
        _state._round_state, cid, sid, round_record, used=used,
        pending_response=None, actor_ref=choice["next"], plan=choice["plan"],
        status="pending" if choice["next"] else "complete", issue=choice["issue"],
        continuation=None, completed_response=after["response_id"],
        control_issue=choice["issue"])
    return settled, ending


async def _after_saved_contribution(cid, sid, client, round_record, watcher, used, actor,
                                    response_id, cancelled):
    """Choose the successor without making the streaming loop itself a router."""
    if round_record.get("stopped") or cancelled:
        return _planning._successor_answer(round_record, used, actor, None)
    if not _planning._decision_successor(round_record):
        return await run_in_threadpool(
            _planning._successor, cid, sid, {**round_record, "used": used}, watcher.handoff,
            cancelled, actor)
    if cancelled or watcher.issue:
        return _planning._successor_answer(round_record, used, actor, None, watcher.issue)
    result = await _planning._select_successor(
        cid, sid, client, round_record, used, actor, response_id)
    if cancelled:
        return _planning._successor_answer(round_record, used, actor, None)
    return result


async def _speaker_left(cid, sid, turn, actor):
    """Whether `actor` has left the scene since it was chosen: the round then
    completes with that issue and nobody after it is asked."""
    current = await run_in_threadpool(_state.roster, cid, sid)
    if actor in [r["ref"] for r in current] + ["grimoire"]:
        return False
    await run_in_threadpool(
        _state._round_state,
        cid,
        sid,
        turn.round_record,
        status="complete",
        issue="speaker left the scene",
    )
    turn.ending = None
    return True


def _fresh_contribution(turn):
    """A fresh contribution, the only one besides a reroll that may be
    offered the decide tool (§8.4): not a retry, a roll resume or a roll
    continuation."""
    return (not turn.round_record.get("pending_response") and not turn.appended
            and not turn.continuation)


async def _compose_contribution(claim, client, resolved, turn, actor, *,
                                app=None, embed_client=None, history_enabled=False):
    """Compose `actor`'s contribution and return its messages: the turn
    decision, the step's history and its Decision stage, then `_prepare`,
    which lands the record in flight on `turn` with a fresh watcher."""
    cid, sid, run = claim.cid, claim.sid, claim.run
    # The turn decision (roadmap 02 §6.3), asked before the contribution
    # is composed and never by a resume, a roll continuation, the
    # narrator or a turn the player directed. Off, nothing is read past
    # the switch, minted or asked, and `_prepare` is called as before.
    plan, response_id = await _generation._pre_generation(cid, sid, client, turn, actor)
    if run.cancel_requested:
        # Stop during the decision: a cancel normally cancels the run's
        # scope and the decision with it, but one that only set the flag
        # (before the scope was installed) must not go on to compose and
        # send a paid generation.
        raise anyio.get_cancelled_exc_class()()
    history = await _round_history(
        app, cid, sid, client, embed_client, resolved, turn, actor,
        enabled=history_enabled, run=run)
    history, epistemic_record, response_id = await _refine_step(
        cid, sid, client, turn, actor, history, response_id, run=run)
    turn.record, messages, turn.composed, turn.composed_settings = await run_in_threadpool(
        _prompts._prepare, claim, turn.round_record, actor, resolved, turn.appended,
        plan=plan, response_id=response_id, history_view=history,
        epistemic=epistemic_record)
    turn.appended = ()
    turn.watcher = store.response_protocol.ResponseWatcher(
        perception=actor != "grimoire",
        require_handoff=not _planning._decision_successor(turn.round_record))
    return messages


async def _generate_contribution(claim, client, resolved, turn, actor, messages, *, fresh):
    """Announce the composed contribution and stream it, metered, with the
    decide tool when `fresh` and offered. Once the call is done, `turn` holds
    the connection that served it and what made it, and no meter."""
    cid, sid, run = claim.cid, claim.sid, claim.run
    record = turn.record
    yield streaming._sse(
        {
            "response_start": {
                "id": record["id"],
                "speaker": record["speaker"],
                "actor_ref": actor,
            }
        }
    )
    # Whatever turn opened the round, a contribution is filed -- and
    # generated -- as `chat`, or as `continuation` when it finishes a roll:
    # one route, so the round's one resolution serves both (`for_task`).
    task = "continuation" if turn.continuation else "chat"
    on_task = operations.for_task(resolved, task)
    tool = None
    if fresh:
        tool, turn.tool_record = await _generation._contribution_tool(
            cid, sid, client, turn.round_record, actor, response_id=record["id"],
            task=task, messages=messages, resolved=on_task, run=run)
    turn.meter = _generation._LoopMeter(task, tool.holders) if tool is not None else store.usage.meter(
        task,
        campaign=cid,
        scene=sid,
        post=turn.round_record["post"],
        round_id=turn.round_record["id"],
        response_id=record["id"],
    )
    async for frame in _generation._stream_contribution(
            client, messages, on_task, turn.meter, turn.watcher, run, cid, task=task,
            tool=tool):
        yield frame
    # Read before the meter is dropped: the facade stamps the attempt
    # that ran on `meter.usage`, which a fallback makes someone other
    # than the resolution's primary.
    turn.served = streaming._served(turn.meter, streaming._asked(resolved))
    turn.meter.done()
    turn.made_by = _persistence._made_by(
        turn.meter, turn.meter.task, turn.composed, turn.round_record.get("typed_note", ""),
        settings=turn.composed_settings,
    )
    turn.meter = None


async def _conclude_contribution(claim, turn, actor, outcome, status, *, paused):
    """File the streamed contribution: `paused` on the roll its fence
    proposed, or saved as `status`. A pause or an empty response writes the
    run's last frames and marks `turn.terminal`."""
    cid, sid = claim.cid, claim.sid
    if paused:
        # The chain waits on the roll: `auto_remaining` stays on the
        # paused round, and its resume carries on from it.
        proposal = await run_in_threadpool(_pause, claim, turn, actor, outcome)
        turn.terminal = True
        yield streaming._sse({"proposal": {**proposal["payload"], "id": proposal["id"]}})
        outcome.land()
        yield streaming._sse({"done": True})
        return
    at = await run_in_threadpool(_persistence._save, claim, turn, status)
    outcome.persisted(at)
    if turn.watcher.issue == "empty response":
        await run_in_threadpool(
            _state._round_state, cid, sid, turn.round_record, status="incomplete")
        turn.terminal = True
        outcome.fail("empty_response", "The model returned no response prose.")
        yield streaming._sse(
            {
                "error": {
                    "kind": "empty_response",
                    "detail": "The model returned no response prose.",
                }
            }
        )


async def _hand_on(claim, client, turn, actor, status):
    """Close the saved contribution and choose who follows it: the round's
    `ending` (whose `next` is the successor, or None) and its record move on,
    and nothing is left in flight."""
    cid, sid, run = claim.cid, claim.sid, claim.run
    record, watcher = turn.record, turn.watcher
    used = [*turn.round_record["used"], actor]
    # Persistence is complete before metadata may authorize a successor.
    # The end frame goes out first, so a Stop or a sit-out the player
    # gives on seeing it is what the successor choice reads.
    yield streaming._sse({"response_end": {"id": record["id"], "status": status}})
    after = await _after_saved_contribution(
        cid, sid, client, turn.round_record, watcher, used, actor,
        record["id"], run.cancel_requested)
    # The contribution's own issue (it spoke for another actor) stops a
    # chain as surely as a bad handoff does -- a handoff-shaped one only
    # where the handoff is read (`_ending_issue`).
    turn.ending = {**after, "status": status,
                   "issue": _planning._ending_issue(turn.round_record, after["issue"], watcher.issue)}
    next_actor, issue = after["next"], after["issue"]
    turn.round_record = await run_in_threadpool(
        _state._round_state,
        cid,
        sid,
        turn.round_record,
        used=used,
        pending_response=None,
        actor_ref=next_actor,
        plan=after["plan"],
        status="pending" if next_actor else "complete",
        issue=issue,
        continuation=None,
        completed_response=record["id"],
        control_issue=issue,
    )
    turn.record = None
    turn.watcher = None
    turn.continuation = None


async def _round_frames(claim, client, resolved, turn, actor, outcome, *,
                        app=None, embed_client=None, history_enabled=False):
    """One round's contributions, from `actor` until nobody is next. Every
    write lands on `turn`, which is what `_frames` rescues from on failure."""
    cid, sid, run = claim.cid, claim.sid, claim.run
    turn.ending = None
    while actor and not run.cancel_requested:
        await anyio.lowlevel.checkpoint()
        turn.served = streaming._asked(resolved)
        if await _speaker_left(cid, sid, turn, actor):
            break
        recovered = await run_in_threadpool(_recover_completed, claim, turn.round_record)
        if recovered is not None:
            turn.round_record, after = await _settle_recovered_successor(
                cid, sid, client, *recovered, run.cancel_requested)
            turn.ending = {**after, "status": "complete"}
            actor = turn.round_record.get("actor_ref")
            continue
        turn.made_by = None
        turn.tool_record = None
        fresh = _fresh_contribution(turn)
        messages = await _compose_contribution(
            claim, client, resolved, turn, actor,
            app=app, embed_client=embed_client, history_enabled=history_enabled)
        async for frame in _generate_contribution(
                claim, client, resolved, turn, actor, messages, fresh=fresh):
            yield frame
        paused = turn.watcher.roll.complete or turn.watcher.roll.truncated
        status = "incomplete" if paused or run.cancel_requested else "complete"
        async for frame in _conclude_contribution(
                claim, turn, actor, outcome, status, paused=paused):
            yield frame
        if turn.terminal:
            return
        async for frame in _hand_on(claim, client, turn, actor, status):
            yield frame
        actor = turn.ending["next"]
    if run.cancel_requested:
        turn.round_record = await run_in_threadpool(_planning._stop, cid, sid, turn.round_record)


async def _rescue(claim, turn, outcome, error=None):
    """Keep what a broken turn has in hand (`turn`): the contribution in
    flight saved for Retry -- or paused on its roll -- and the round marked
    `incomplete`; a Stop ends the chain however it is kept."""
    meter = turn.meter
    # Before `meter.done`, for `_frames`' reason: what a rescued partial is
    # filed under is the attempt that was running when the turn broke. With no
    # meter left, `turn.served` is what `_frames` already settled on.
    if meter:
        turn.served = streaming._served(meter, turn.served)
        if error:
            meter.done("error", error.kind, detail=error.detail)
        else:
            meter.done("aborted")
        turn.made_by = _persistence._made_by(
            meter, meter.task, turn.composed, turn.round_record.get("typed_note", ""),
            settings=turn.composed_settings,
        )

    cid, sid = claim.cid, claim.sid
    record, watcher, round_record = turn.record, turn.watcher, turn.round_record

    def save():
        with store.locks.campaign_lock(cid):
            claim.fence()
            if claim.run.cancel_requested:
                # A Stop ends the chain however the interrupted contribution
                # is kept below -- saved for Retry, or paused on its roll.
                _planning._stop(cid, sid, round_record)
            if watcher and record:
                current = store.responses.get(cid, sid, record["id"], private=True)
                if current["status"] != "complete":
                    watcher.finish()
                    if watcher.roll.complete or watcher.roll.truncated:
                        _pause(claim, turn, record["actor_ref"], outcome)
                        return
                    at = _persistence._save(claim, turn, "incomplete")
                    outcome.persisted(at)
            # Only this run's own round, and only while it is unfinished: a
            # Stop may already have completed it (`_stop`), and marking it
            # `incomplete` again would hand Retry a round nobody interrupted.
            current_round = store.responses.unfinished(cid, sid)
            if (current_round and current_round["id"] == round_record["id"]
                    and current_round["status"] != "paused"):
                _state._round_state(cid, sid, round_record, status="incomplete")

    await run_in_threadpool(save)


def resume_roll(
    cid,
    sid,
    pid,
    request,
    client,
    *,
    resolved,
    run,
    round_record,
    resolution,
    after_turn=None,
    appended_block=None,
):
    round_record = store.responses.update_round(cid, sid, round_record["id"], continuation=pid)
    block = appended_block or prompts.render("scene/roll_declined.j2")
    return start(
        cid,
        sid,
        request,
        client,
        resolved,
        run=run,
        round_record=round_record,
        appended=(("Roll resolution", "system", block),),
        continuation=pid,
        after_turn=after_turn,
    )


def retry(cid, sid, request, client, resolved, run, after_turn=None, *, embed_client=None):
    round_record = store.responses.unfinished(cid, sid)
    if round_record is None:
        raise HTTPException(
            409, detail={"kind": "nothing_to_retry", "detail": "No unfinished response remains."}
        )
    if round_record["status"] == "paused":
        raise HTTPException(
            409,
            detail={"kind": "roll_pending", "detail": "Resolve or decline the pending roll first."},
        )
    messages = store.scenes.read_scene(cid, sid)["messages"]
    if not _retry_context_matches(messages, round_record):
        raise HTTPException(
            409,
            detail={
                "kind": "context_changed",
                "detail": "The transcript changed since this response stopped.",
            },
        )
    return start(
        cid,
        sid,
        request,
        client,
        resolved,
        run=run,
        round_record=round_record,
        after_turn=after_turn,
        continuation=round_record.get("continuation"),
        embed_client=embed_client,
        history_enabled=True,
    )


def _retry_context_matches(messages, round_record):
    expected = round_record.get("transcript_hash")
    if store.responses.transcript_hash(messages) == expected:
        return True
    rid = round_record.get("pending_response")
    part = round_record.get("continuation") or ""
    suffix = [
        m
        for m in messages[round_record["watermark"] :]
        if m.get("response_id") == rid and m.get("response_part", "") == part
    ]
    return (
        len(suffix) == len(messages) - round_record["watermark"]
        and store.responses.transcript_hash(messages[: round_record["watermark"]]) == expected
    )


def _recover_completed(claim, round_record):
    """`(round, successor choice)` once a pending contribution that already
    completed is published and the round advanced past it; None when the
    round has no completed contribution left to recover."""
    pending = round_record.get("pending_response")
    if not pending:
        return None
    cid, sid = claim.cid, claim.sid
    with store.locks.campaign_lock(cid):
        claim.fence()
        record = store.responses.get(cid, sid, pending, private=True)
        if record["status"] != "complete":
            return None
        continuation = round_record.get("continuation")
        if continuation:
            if not store.proposals.commit_narration(
                cid, sid, continuation, lambda: store.responses.publish_saved(cid, sid, pending)
            ):
                raise store.responses.ResponseConflict(
                    "proposal_stale", "The roll continuation is stale."
                )
            streaming._prune_rewrites(cid, sid)   # what the commit trimmed
        else:
            store.responses.publish_saved(cid, sid, pending)
        variant = next(v for v in record["variants"] if v["id"] == record["active_variant"])
        used = list(dict.fromkeys([*round_record["used"], record["actor_ref"]]))
        if _planning._decision_successor(round_record):
            # The prose made it to disk, but the subsequent Decision may not
            # have run yet. Leave pending_response set until selection settles,
            # so a crash/retry resumes the choice, never generates prose twice.
            return round_record, {"needs_decision": True, "used": used,
                                  "actor": record["actor_ref"], "response_id": pending,
                                  "content_issue": variant.get("issue")}
        after = _planning._successor(cid, sid, {**round_record, "used": used}, variant.get("handoff"),
                           claim.run.cancel_requested, record["actor_ref"])
        return _state._round_state(
            cid,
            sid,
            round_record,
            used=used,
            pending_response=None,
            actor_ref=after["next"],
            plan=after["plan"],
            status="pending" if after["next"] else "complete",
            issue=after["issue"],
            continuation=None,
        ), {**after, "issue": _planning._ending_issue(round_record, after["issue"], variant.get("issue"))}
