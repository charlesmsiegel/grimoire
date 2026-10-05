"""One assigned actor per call, one bounded round per player post."""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from contextlib import aclosing

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import content_parts, llm_reasoning, prompts, store
from ..llm import LLMClient, effective_model, fallback_sampling
from ..llm_errors import LLMError
from ..model_guidance import PreparedMessages
from . import runs, streaming
from . import tracker as tracker_routes
from .common import (
    _dump,
    _fallback_connection,
    _override_connection,
    _record_prompt,
    _require_connection,
    _require_scene,
    _turn_override,
    get_llm,
)
from .models import GroupSettings, RegenerateBody

router = APIRouter()

#: Where a round's planning draws its randomness (talkativeness rolls, the
#: Natural shuffle). A seam for tests, which patch it to prove a retry or a
#: recovery continues the stored plan rather than rolling a new one.
_rng: Callable[[], random.Random] = random.Random


def enabled():
    return True


def roster(cid, sid):
    return [
        {"ref": f"{a['kind']}:{a['id']}", "name": a["name"]}
        for a in store.appearances.scene_cast(cid, sid)
        if a["role"] != "player"
    ]


def validate_actor(cid, sid, actor_ref):
    if actor_ref and actor_ref not in [r["ref"] for r in roster(cid, sid)] + ["grimoire"]:
        raise HTTPException(400, detail="speaker must be a present NPC or grimoire")


def _fence(cid, sid, run, token):
    if streaming._scene_moved(cid, sid, run.scene_identity) or not streaming._owns_turn(
        cid, sid, token
    ):
        raise store.responses.ResponseConflict(
            "scene_replaced", "The scene changed during generation."
        )


def _settings(cid, sid):
    return store.group_play.settings_of(store.scenes.read_scene(cid, sid)["meta"])


def _handoff_pool(cid, sid, round_record):
    """The round's eligible actors a Directed handoff may still name: whoever
    has started sitting out since the round opened is no longer one of them."""
    out = set(_settings(cid, sid)["sitting_out"])
    return [entry for entry in round_record["eligible"] if entry["ref"] not in out]


def _compose(cid, sid, round_record, actor, conn, appended=()):
    # The prompt must offer the same remaining slots that the engine accepts.
    # Offering the current/used actors teaches an invalid handoff; omitting the
    # narrator hides a valid slot. Explicit one-response requests have no next,
    # and neither does a List/Natural/Manual round: its plan decides, and any
    # handoff block it writes is ignored. With automatic rounds remaining, a
    # Directed handoff to someone who already spoke this round is valid -- it
    # ends the round and that character leads the next (`_successor`) -- so
    # only the current actor is withheld.
    offer = round_record["automatic"] and round_record.get("mode", "directed") == "directed"
    if offer and round_record.get("auto_remaining", 0) > 0:
        used = {actor}
    else:
        used = {*round_record["used"], actor}
    pool = _handoff_pool(cid, sid, round_record) if offer else []
    # Whoever already spoke is marked, so the handoff text can say what
    # naming them does instead of claiming they are excluded.
    candidates = [{**entry, "responded": entry["ref"] in round_record["used"]}
                  for entry in [*pool, {"ref": "grimoire", "name": "Grimoire"}]
                  if offer and entry["ref"] not in used]
    kwargs = {
        "turn": round_record.get("turn"),
        "actor_ref": actor,
        "eligible_speakers": candidates,
        "describe": store.prompt_log.capturing(),
        "model": effective_model(conn),
        "images": store.post_images.images_for(conn),
    }
    if appended:
        kwargs["appended"] = appended
    if round_record.get("note"):
        return store.context.compose_director_turn(cid, sid, round_record["note"], **kwargs)
    return store.context.compose_turn(cid, sid, **kwargs)


def start(
    cid,
    sid,
    request,
    client,
    conn,
    run,
    *,
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
):
    """Open (or resume) a round and hand its frames to a detached run.

    `kind` is what asked for a fresh round -- `post` (a player post),
    `continue` (an empty send) or `note` (a director note, a replay) -- and
    `trigger` the text it answers. Together with the scene's group-play order
    they plan the round's speakers (`_plan`). A resumed round (`round_record`
    given: a retry, a roll resume) is never re-planned.

    An empty `conn` is the caller saying no connection was needed
    (`answers_nothing`). If the plan made here says otherwise after all -- the
    order changed in between -- the connection is required now."""
    with store.locks.campaign_lock(cid):
        token = streaming._claim_turn(cid, sid)
        _fence(cid, sid, run, token)
        if round_record is None:
            planned = _plan(cid, sid, kind, trigger, actor_ref)
            if not conn and (planned["actor_ref"]
                             or not (automatic and planned["mode"] == "manual")):
                conn = _require_connection("chat", cid)
            chain = {}
            if kind == "post" and planned["mode"] != "manual":
                # The rounds a player post may run on for (`_follow_on`);
                # Manual never auto-continues.
                rounds = _settings(cid, sid)["auto_rounds"]
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
            )
    outcome = streaming.StreamOutcome()
    frames = _frames(
        cid,
        sid,
        client,
        conn,
        run,
        token,
        round_record,
        outcome,
        app=request.app,
        after_turn=after_turn,
        appended=appended,
        continuation=continuation,
    )
    runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _plan(cid, sid, kind, trigger, actor_ref):
    """The round's `eligible`, lead `actor_ref`, `mode` and remaining `plan`,
    plus `present`: the whole present cast, whose observation the round
    anchors however narrow `eligible` is.

    A scene that never stored group-play settings reads as the defaults --
    Directed, nobody sitting out, no talkativeness set (which Directed never
    filters) -- so it plans exactly as before settings existed.

    An explicit lead (Respond as, a reply-as chip, a replay) is never planned
    away, sitting out or not. With a post it still opens a round by mode, and
    in List/Natural the planner's sequence -- the forced lead removed -- is
    what follows it."""
    cast = roster(cid, sid)
    scene = store.scenes.read_scene(cid, sid)
    settings = store.group_play.settings_of(scene["meta"])
    mode = settings["order"]
    history = scene["messages"]
    if kind == "post":
        return _plan_round(settings, cast, trigger=trigger, history=history, lead=actor_ref)
    if actor_ref:
        return {"eligible": cast, "actor_ref": actor_ref, "mode": mode, "plan": [],
                "present": cast}
    lead = None
    if kind == "continue":
        lead = store.group_play.plan_continue(
            settings, cast, last=_last_contribution(cid, sid, history), history=history,
            rng=_rng())
    # Sitting-out characters never reach the selector.
    return {"eligible": store.group_play.available(settings, cast), "actor_ref": lead,
            "mode": mode, "plan": [], "present": cast}


def _plan_round(settings, cast, *, trigger, history, lead, author=None):
    """A round answering `trigger` -- a player post, or the contribution a
    follow-on round continues from (whose writer is `author`) -- planned by
    the scene's order, with `lead`, if any, forced to speak first."""
    mode = settings["order"]
    planned = store.group_play.plan_post(
        settings, cast, trigger=trigger, history=history, rng=_rng(),
        force=(lead,) if lead else (), author=author)
    actor, plan = planned["actor_ref"], planned["plan"]
    if lead:
        # The forced ref leads; the planner's own lead, if any, is next.
        plan = ([actor] if actor else []) + plan
        actor = lead
    if mode not in ("list", "natural"):
        actor, plan = lead, []
    return {"eligible": planned["eligible"], "actor_ref": actor, "mode": mode, "plan": plan,
            "present": cast}


def _follow_on(cid, sid, run, token, round_record, lead):
    """The next automatic round of a player post's chain, or None when the
    scene's order is Manual now (which never auto-continues) or its
    `auto_rounds` has been lowered below the rounds already started.

    Planned by the scene's current order with the most recent contribution as
    its trigger -- its writer never counted as naming themselves -- and with
    `lead` (a Directed repeat handoff) speaking first. It keeps the original
    post, so usage stays attributed to what the player sent, and answers the
    Continue note, as an empty send would. A Directed round with no lead goes
    through the selector (`_first_actor`)."""
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        scene = store.scenes.read_scene(cid, sid)
        settings = store.group_play.settings_of(scene["meta"])
        if settings["order"] == "manual":
            return None
        # `auto_rounds` is re-read here, so lowering it mid-chain takes
        # effect: this is follow-on number `index`, and it starts only while
        # the setting still allows that many. Raising it never extends a
        # chain past what the post started with.
        index = round_record.get("round_index", 1)
        allowed = settings["auto_rounds"]
        if allowed < index:
            return None
        remaining = min(round_record.get("auto_remaining", 0) - 1, allowed - index)
        history = scene["messages"]
        last = _last_contribution(cid, sid, history) or {"ref": None, "text": ""}
        planned = _plan_round(settings, roster(cid, sid), trigger=last["text"],
                              history=history, lead=lead, author=last["ref"])
        following = store.responses.new_round(
            cid,
            sid,
            automatic=True,
            post=round_record["post"],
            run_id=run.id,
            note=prompts.render("scene/director_note.j2"),
            turn=round_record.get("turn"),
            auto_remaining=remaining,
            round_index=index + 1,
            auto_total=min(round_record.get("auto_total", 0), allowed),
            **planned,
        )
        # A detached write -- the round, and the presence it anchors -- that
        # no contribution may follow (the selector can hand back), so it
        # stamps the campaign for itself.
        streaming._turn_settled(cid)
        return following


#: The issues a handoff block raises. Only Directed reads the handoff, so
#: only Directed ends a chain on one; a List/Natural/Manual contribution keeps
#: it recorded on its variant and its plan carries on.
_HANDOFF_ISSUES = frozenset(
    {"missing or invalid handoff", "ineligible or repeated speaker", "repeated speaker"})


def _ending_issue(round_record, after_issue, content_issue):
    """What stops a chain after a contribution: the successor choice's own
    issue, or the contribution's -- an authority violation or an empty
    response ends it in every mode, a handoff-shaped one only in Directed."""
    if (round_record.get("mode", "directed") != "directed"
            and content_issue in _HANDOFF_ISSUES):
        content_issue = None
    return after_issue or content_issue


def _chain_continues(round_record, ending, cancelled):
    """Whether a round that just ended with nobody next runs on into a
    follow-on round. `ending` is its last contribution's successor choice
    plus that contribution's `status`, None when no contribution closed it
    (nothing generated, the selector handed back, a speaker left)."""
    return not (
        cancelled
        or ending is None
        or ending["issue"]
        or ending["status"] != "complete"
        or not round_record["used"]
        or round_record.get("mode", "directed") == "manual"
        or round_record.get("auto_remaining", 0) <= 0
        or (ending["handed_back"] and not ending["lead"])
    )


def _stop(cid, sid, round_record):
    """Stop ends the whole chain: no rounds remain, the plan is gone, and the
    round is `stopped`, so a Retry finishes the interrupted contribution and
    `_successor` names nobody after it, in any mode.

    A round with no contribution in flight (`pending_response`) had nothing
    interrupted -- a follow-on stopped before its first speaker, a successor
    chosen but not yet started -- so it ends `complete` with nobody next, and
    a Retry has nothing to answer."""
    with store.locks.campaign_lock(cid):
        stopped = store.responses.update_round(
            cid, sid, round_record["id"], auto_remaining=0, plan=[], stopped=True)
        if stopped["status"] in ("pending", "incomplete") and not stopped["pending_response"]:
            stopped = store.responses.update_round(
                cid, sid, round_record["id"], status="complete", actor_ref=None)
        return stopped


def _last_contribution(cid, sid, history):
    """The newest contribution an empty send continues from, as the planner
    takes it: `{"ref", "text"}`, `ref` None for a post the ledger never saw."""
    last = next((m for m in reversed(history) if m["role"] == "assistant"
                 and m.get("speaker") not in store.scenes.SYNTHETIC_SPEAKERS), None)
    if last is None:
        return None
    ref = store.responses.actor_refs(cid, sid).get(last.get("response_id") or "")
    return {"ref": ref, "text": last["content"]}


def answers_nothing(cid, sid, *, director, content, speaker_ref):
    """Whether a send will generate nothing, so needs no connection: a player
    post naming no speaker in a Manual scene appends the post and completes its
    round empty -- the player picks who answers with the reply-as chips."""
    return (not director and bool(content.strip()) and not speaker_ref
            and not store.scenes.is_pcless(cid, sid)
            and _settings(cid, sid)["order"] == "manual")


def _successor(cid, sid, round_record, handoff, cancelled, actor=None):
    """Who speaks after the contribution that just landed.

    `round_record["used"]` must already include that contribution's actor
    (`actor`, when the caller knows it).
    Directed: the contribution's handoff, validated against the round's
    eligible minus whoever now sits out; `handed_back` is an explicit
    `next: null`. With automatic rounds remaining, a handoff to someone who
    already spoke this round is no issue: it ends the round, and that ref is
    the `lead` of the follow-on round (`_follow_on`) -- never the actor
    handing off, whom the prompt never offers. List, Natural and Manual: the
    stored plan alone decides (`next_planned`, which skips whoever has left or
    started sitting out) and the handoff is ignored. Either way nobody follows
    a non-automatic round, a Stop, or a round a Stop already marked `stopped`
    (so a Retry finishes only the interrupted contribution), and the plan is
    cleared with it.

    `{"next", "issue", "plan", "handed_back", "lead"}`."""
    plan = list(round_record.get("plan") or [])
    issue = None
    handed_back = False
    lead = None
    if round_record.get("mode", "directed") == "directed":
        refs = [entry["ref"] for entry in _handoff_pool(cid, sid, round_record)]
        nxt, issue = store.response_protocol.validate_handoff(
            handoff, [*refs, "grimoire"], round_record["used"])
        handed_back = nxt is None and issue is None
        if (issue == "repeated speaker" and round_record.get("auto_remaining", 0) > 0
                and handoff["next"] != actor):
            lead, issue = handoff["next"], None
    else:
        nxt, plan = store.group_play.next_planned(_settings(cid, sid), roster(cid, sid), plan)
    if not round_record["automatic"] or cancelled or round_record.get("stopped"):
        nxt, plan, lead = None, [], None
    return {"next": nxt, "issue": issue, "plan": plan, "handed_back": handed_back,
            "lead": lead}


def _selector_messages(cid, sid, round_record):
    messages = store.scenes.read_scene(cid, sid)["messages"]
    tail = messages[-12:]
    # The prompt view of the posts it reads, depth counted over the whole scene.
    shown = store.regex.view.view(tail, cid=cid, phase="prompt",
                                  offset=len(messages) - len(tail), total=len(messages))
    public = [
        {
            "speaker": m.get("speaker") or ("You" if m["role"] == "user" else "Grimoire"),
            "content": m["content"],
        }
        for m in shown
        if m.get("speaker") not in store.scenes.SYNTHETIC_SPEAKERS
    ]
    return [
        {
            "role": "system",
            "content": prompts.render(
                "scene/response_selector.j2",
                roster=round_record["eligible"],
                conversation=public,
                note=round_record.get("note", ""),
            ),
        }
    ]


def _prepare(cid, sid, run, token, round_record, actor, conn, appended):
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        pending = round_record.get("pending_response")
        if pending:
            record = store.responses.get(cid, sid, pending, private=True)
            if record["status"] != "complete" and not appended:
                messages = PreparedMessages.from_snapshot(
                    record.get("resume_snapshot") or record["snapshot"], effective_model(conn),
                    campaign=cid,
                )
                _capture(
                    cid,
                    sid,
                    "continuation" if round_record.get("continuation") else "retry",
                    messages,
                    conn,
                )
                return record, messages
        messages, _breakdown = _compose(cid, sid, round_record, actor, conn, appended)
        # The round's eligible first, then the whole present cast: an explicit
        # pick a talkativeness roll filtered out is still who they are.
        speaker = next(
            (r["name"] for r in [*round_record["eligible"], *roster(cid, sid)]
             if r["ref"] == actor),
            "Grimoire",
        )
        if pending and appended:
            record = store.responses.get(cid, sid, pending, private=True)
            store.responses.save_resume_prompt(cid, sid, pending, messages.snapshot())
            # The original pre-response snapshot remains the reroll boundary.
        else:
            record = store.responses.prepare(
                cid, sid, round_record["id"], actor, speaker, messages.snapshot()
            )
        _capture(cid, sid, "continuation" if appended else "chat", messages, conn)
        return record, messages


def _save(cid, sid, run, token, record, watcher, status, round_record, continuation=None,
          tracked=None, connection=""):
    """Persist this contribution's variant. A key the tracker marked `pending`
    for it is appended to `tracked`, for the caller to start once the turn's
    terminal frames are out (see `_start_tracking`). `connection` is the id of
    the connection that served it, recorded on the variant and its post."""
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        text, authority_issue, rewrite = _normalise(cid, sid, record, watcher.narration,
                                                    connection)
        if authority_issue:
            watcher.handoff = None
            watcher.issue = authority_issue
        if not text and status == "complete":
            status = "incomplete"
            watcher.handoff = None
            watcher.issue = "empty response"
        saved = lambda: store.responses.save_variant(
            cid,
            sid,
            record["id"],
            text,
            status,
            handoff=watcher.handoff if status == "complete" else None,
            issue=watcher.issue,
            part=continuation or "",
            reasoning=watcher.reasoning + watcher.preparation_note,
            connection=connection,
        )
        if continuation and status == "complete":
            if not store.proposals.commit_narration(cid, sid, continuation, saved):
                raise store.responses.ResponseConflict(
                    "proposal_stale", "The roll continuation is stale."
                )
        else:
            saved()
        if text and rewrite:
            streaming._record_rewrite(cid, sid, record["id"], *rewrite, text)
        streaming._turn_settled(cid)
        if text and tracked is not None:
            # Marked HERE, in the hold that wrote the variant, where the
            # tracker's lock acquisition is reentrant: anywhere later on this
            # path it would be a fresh wait in front of the player's frames.
            marked = tracker_routes.mark_response(cid, sid, record["id"])
            if marked:
                tracked.append(marked)
        return streaming._tail_length(cid, sid) if text else None


def _normalise(cid, sid, record, text, connection=""):
    """The reply as it is stored, the authority issue if it spoke for another
    actor, and `(original, fired rule ids)` when the store phase rewrote it
    (else None).

    The store phase runs LAST, on the text that would otherwise have been
    stored, rather than beside the macro expansion: what it records is what
    Restore original writes back, so it has to be stored text already -- not
    text still carrying markers this function is about to strip. `connection`
    is the one that served the reply, for its connection-level rules."""
    text, _ = store.state_fence.split_block(text)
    text = store.context.resolve_art_handles(cid, text, sid)
    text = store.context.expand_macros(
        text, store.context.scene_substitutions(cid, sid), cid, sid
    ).strip()
    markers = store.scenes._markers(text)
    parts = [text[: markers[0].start()].strip()] if markers else [text]
    issue = None
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        label = marker.group(1)
        allowed = label == record["speaker"] and label != "You"
        if record["actor_ref"] == "grimoire":
            allowed = label == "Grimoire" and not marker.group(2)
        if allowed:
            parts.append(text[marker.end() : end].strip())
        else:
            issue = "response attempted to speak for another actor"
    text = "\n\n".join(part for part in parts if part)
    stored, fired = store.regex.view.store_phase(text, cid=cid, role="model",
                                                 connection=connection)
    return (stored.strip(), issue, (text, fired)) if fired else (text, issue, None)


def _capture(cid, sid, task, messages, conn):
    if not store.prompt_log.capturing():
        return
    breakdown = getattr(messages, "breakdown", None)
    if breakdown is None:
        rows = [
            {
                "id": f"message_{i}",
                "label": m["role"],
                # A message may hold image references (#377): the record keeps
                # the text and charges each reference what the packer does.
                "text": content_parts.text_of(m["content"]),
                "tier": "lock-in",
                "dropped": False,
                "pinned": False,
                "trimmed": 0,
                "tokens": (store.tokens.count_tokens(content_parts.text_of(m["content"]))
                           + store.context.pack.IMAGE_TOKENS
                           * len(content_parts.image_refs(m["content"]))),
            }
            for i, m in enumerate(messages)
        ]
        breakdown = {
            "sections": rows,
            "total_tokens": sum(row["tokens"] for row in rows),
            "dropped_tokens": 0,
            "budget_tokens": store.context.budget_tokens(),
        }
    _record_prompt(cid, sid, task, breakdown, model=effective_model(conn), kind=conn["kind"], messages=messages,
                   conn=conn)
    if isinstance(messages, PreparedMessages):
        # Steered frozen variants have no historical section accounting. Capture
        # their exact rendered messages at actual fallback dispatch instead.
        messages.on_variant = lambda model, _: _capture(
            cid, sid, task, messages.for_model(model), _variant_conn(conn, model)
        )


def _variant_conn(conn: dict, model: str) -> dict:
    """The connection a fallback variant was sent on, as its capture names it.

    The fallback as the facade resolves it, carrying the route's sampler preset
    under `llm.fallback_sampling`'s rule -- not the primary relabelled with the
    fallback's model, which would record the primary's preset and catalog list
    against a request that never carried them. Falls back to that relabel only
    when no fallback resolves any more (repointed since the call began), minus
    the sampling block it cannot vouch for.
    """
    fallback = _fallback_connection()
    if fallback is not None:
        return {**fallback_sampling(conn, fallback), "model": model}
    return {**{k: v for k, v in conn.items() if k not in ("sampling", "model_params")},
            "model": model}


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


async def _select(cid, sid, client, round_record):
    eligible = round_record["eligible"]
    if len(eligible) <= 1:
        return (eligible[0]["ref"] if eligible else "grimoire"), None
    conn = await run_in_threadpool(_require_connection, "response-selector", cid)
    messages = await run_in_threadpool(_selector_messages, cid, sid, round_record)
    meter = store.usage.meter(
        "response-selector",
        campaign=cid,
        scene=sid,
        post=round_record["post"],
        round_id=round_record["id"],
    )
    await run_in_threadpool(_capture, cid, sid, "response-selector", messages, conn)
    try:
        answer = await client.complete(messages, conn, meter.usage)
    except LLMError as exc:
        meter.done("error", exc.kind, detail=exc.detail)
        raise
    except BaseException:
        meter.done("aborted")
        raise
    meter.done()
    try:
        payload = json.loads(answer)
    except ValueError:
        payload = None
    return store.response_protocol.validate_handoff(
        payload, [r["ref"] for r in eligible] + ["grimoire"], []
    )


def _reasoning_frame(event, watcher, liveness):
    """The frame for a reasoning event, recording it on the watcher; None for an
    event that carries reply text."""
    if event.get("thinking_reset"):
        watcher.reasoning = ""
    elif "thinking_delta" in event:
        watcher.reasoning += event["thinking_delta"]
    else:
        return None
    liveness.sent()
    return streaming._sse(event)


async def _stream_events(client, messages, conn, meter, watcher, run, display):
    # Heartbeats are throttled here, at the producer, for `streaming._Liveness`'s
    # reason: every SSE line arrives as an empty delta. Liveness records frames
    # that went out, so a display frame the throttle held back is not one.
    liveness = streaming._Liveness()
    async with aclosing(llm_reasoning.stream(client, messages, conn, meter.usage)) as source:
        async for event in source:
            if run.cancel_requested:
                raise anyio.get_cancelled_exc_class()()
            reasoning = _reasoning_frame(event, watcher, liveness)
            if reasoning:
                yield reasoning
                continue
            delta = event["delta"]
            frames = await display.frames(watcher.feed(delta))
            if frames:
                liveness.sent()
                for frame in frames:
                    yield frame
            elif (not delta or display.active) and liveness.due():
                # An empty delta is the facade waiting on the model; with a
                # display rule in force, a non-empty one whose text is hidden
                # sends nothing either (`_fence_stream` says why it is only then).
                yield streaming._HEARTBEAT
            if watcher.roll.complete:
                break


async def _stream_contribution(client, messages, conn, meter, watcher, run, cid=None):
    # Display rules for the connection that serves the call, settled on the
    # first visible text (`streaming._Display`), so a fallback's reply is shaped
    # by the rules it is saved under. Frames replace `delta` frames one for
    # one, so `keep` counts from this contribution's `response_start`.
    display = streaming._Display(cid, conn, meter)
    try:
        async with aclosing(
            _stream_events(client, messages, conn, meter, watcher, run, display)
        ) as events:
            async for frame in events:
                yield frame
    except LLMError:
        # What the throttle held back is on screen after a refresh and must be
        # before it: `_rescue` saves the partial whole. A delta stream has
        # nothing held back, so it sends nothing here.
        await display.build()
        if display.active:
            for frame in await display.frames(watcher.finish(), last=True):
                yield frame
        raise
    visible = watcher.finish()
    for frame in await display.frames(visible, last=True):
        yield frame
    if run.cancel_requested:
        # Stop can arrive while the final visible delta is being delivered.
        # Rescue must retain this pending slot just as for midstream Stop.
        raise anyio.get_cancelled_exc_class()()


def _pause(cid, sid, run, token, record, watcher, round_record, continuation, outcome, actor,
           tracked=None, connection=""):
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        if continuation:
            store.proposals.commit_narration(cid, sid, continuation, lambda: None)
        payload = streaming._make_proposal(cid, sid, watcher.roll)
        proposal = store.proposals.new(cid, sid, payload, round_record["post"])
        _round_state(
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
        at = _save(cid, sid, run, token, record, watcher, "incomplete", round_record, continuation,
                   tracked=tracked, connection=connection)
        _round_state(
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


async def _first_actor(cid, sid, client, round_record):
    actor = round_record.get("actor_ref")
    if actor is None and round_record["automatic"] and round_record.get("mode") == "manual":
        # A Manual post: nobody answers until the player picks who.
        round_record = await run_in_threadpool(
            _round_state, cid, sid, round_record, status="complete")
        return None, round_record
    if actor is None:
        actor, issue = await _select(cid, sid, client, round_record)
        round_record = await run_in_threadpool(
            _round_state, cid, sid, round_record, actor_ref=actor,
            status="pending" if actor else "complete", issue=issue)
    return actor, round_record


class _Progress:
    """What a run's frames have in hand, which `_frames`' failure paths read
    back: the round, and the contribution in flight (`record`, `watcher`,
    `meter`, the roll `continuation` it finishes and the `appended` blocks its
    prompt still owes). `served` is the connection that answered that
    contribution, kept here so a rescue after `meter` is dropped (a failing
    `_pause`/`_save`) still files the partial under the fallback that served
    it, not the primary. `ending` is the successor choice of the contribution
    that closed the round, None until one does (`_chain_continues`), and
    `terminal` that the round already wrote the run's last frame (a roll
    pause, an empty response)."""

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


async def _frames(
    cid,
    sid,
    client,
    conn,
    run,
    token,
    round_record,
    outcome,
    *,
    app=None,
    after_turn=None,
    appended=(),
    continuation=None,
):
    """One run carries a player post's whole chain of rounds: the first, then
    each follow-on `_follow_on` opens while rounds remain, announced by a
    `round_start` frame. The turn settles once, after the last of them."""
    turn = _Progress(round_record, appended, continuation, conn.get("id", ""))
    # Tracker keys this turn marked `pending`, started in `finally` once the
    # terminal frames are out -- in transcript order, which is the order the
    # scene's tracker lock runs them in.
    tracked: list[tracker_routes.Mark] = []
    try:
        actor, turn.round_record = await _first_actor(cid, sid, client, round_record)
        while True:
            round_frames = _round_frames(
                cid, sid, client, conn, run, token, turn, actor, outcome, tracked
            )
            async with aclosing(round_frames) as frames:
                async for frame in frames:
                    yield frame
            if turn.terminal:
                return
            if not _chain_continues(turn.round_record, turn.ending, run.cancel_requested):
                break
            following = await run_in_threadpool(
                _follow_on, cid, sid, run, token, turn.round_record, turn.ending["lead"]
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
            actor, turn.round_record = await _first_actor(cid, sid, client, following)
        outcome.land()
        yield streaming._sse({"done": True})
    except store.responses.ResponseConflict as exc:
        _abort_meter(turn.meter)
        # A lost scene/turn fence must never rescue into its replacement.
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except LLMError as exc:
        await _rescue(
            cid, sid, run, token, turn.record, turn.watcher, turn.round_record, turn.continuation,
            outcome, turn.meter, exc, tracked=tracked, served=turn.served,
        )
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except BaseException:
        with anyio.CancelScope(shield=True):
            await _rescue(
                cid, sid, run, token, turn.record, turn.watcher, turn.round_record,
                turn.continuation, outcome, turn.meter, tracked=tracked, served=turn.served,
            )
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await streaming._fire_follow_up(after_turn, outcome)
            await _start_tracking(app, cid, sid, client, tracked, run.scene_identity)


async def _round_frames(cid, sid, client, conn, run, token, turn, actor, outcome, tracked):
    """One round's contributions, from `actor` until nobody is next. Every
    write lands on `turn`, which is what `_frames` rescues from on failure."""
    turn.ending = None
    while actor and not run.cancel_requested:
        await anyio.lowlevel.checkpoint()
        turn.served = conn.get("id", "")
        current = await run_in_threadpool(roster, cid, sid)
        if actor not in [r["ref"] for r in current] + ["grimoire"]:
            await run_in_threadpool(
                _round_state,
                cid,
                sid,
                turn.round_record,
                status="complete",
                issue="speaker left the scene",
            )
            turn.ending = None
            break
        recovered = await run_in_threadpool(
            _recover_completed, cid, sid, run, token, turn.round_record
        )
        if recovered is not None:
            turn.round_record, after = recovered
            turn.ending = {**after, "status": "complete"}
            actor = turn.round_record.get("actor_ref")
            continue
        turn.record, messages = await run_in_threadpool(
            _prepare, cid, sid, run, token, turn.round_record, actor, conn, turn.appended
        )
        record = turn.record
        turn.appended = ()
        turn.watcher = watcher = store.response_protocol.ResponseWatcher(
            perception=actor != "grimoire")
        yield streaming._sse(
            {
                "response_start": {
                    "id": record["id"],
                    "speaker": record["speaker"],
                    "actor_ref": actor,
                }
            }
        )
        turn.meter = store.usage.meter(
            "continuation" if turn.continuation else "chat",
            campaign=cid,
            scene=sid,
            post=turn.round_record["post"],
            round_id=turn.round_record["id"],
            response_id=record["id"],
        )
        async for frame in _stream_contribution(
                client, messages, conn, turn.meter, watcher, run, cid):
            yield frame
        # Read before the meter is dropped: the facade stamps the attempt
        # that ran on `meter.usage`, which a fallback makes someone other
        # than `conn`.
        turn.served = served = streaming._served(turn.meter, conn)
        turn.meter.done()
        turn.meter = None
        paused = watcher.roll.complete or watcher.roll.truncated
        status = "incomplete" if paused or run.cancel_requested else "complete"
        if paused:
            # The chain waits on the roll: `auto_remaining` stays on the
            # paused round, and its resume carries on from it.
            proposal = await run_in_threadpool(
                _pause,
                cid,
                sid,
                run,
                token,
                record,
                watcher,
                turn.round_record,
                turn.continuation,
                outcome,
                actor,
                tracked,
                served,
            )
            turn.terminal = True
            yield streaming._sse({"proposal": {**proposal["payload"], "id": proposal["id"]}})
            outcome.land()
            yield streaming._sse({"done": True})
            return
        at = await run_in_threadpool(
            _save, cid, sid, run, token, record, watcher, status, turn.round_record,
            turn.continuation, tracked, served,
        )
        outcome.persisted(at)
        if watcher.issue == "empty response":
            await run_in_threadpool(
                _round_state, cid, sid, turn.round_record, status="incomplete")
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
            return
        used = [*turn.round_record["used"], actor]
        # Persistence is complete before metadata may authorize a successor.
        # The end frame goes out first, so a Stop or a sit-out the player
        # gives on seeing it is what the successor choice reads.
        yield streaming._sse({"response_end": {"id": record["id"], "status": status}})
        after = await run_in_threadpool(
            _successor, cid, sid, {**turn.round_record, "used": used}, watcher.handoff,
            run.cancel_requested, actor,
        )
        # The contribution's own issue (it spoke for another actor) stops a
        # chain as surely as a bad handoff does -- a handoff-shaped one only
        # where the handoff is read (`_ending_issue`).
        turn.ending = {**after, "status": status,
                       "issue": _ending_issue(turn.round_record, after["issue"], watcher.issue)}
        next_actor, issue = after["next"], after["issue"]
        turn.round_record = await run_in_threadpool(
            _round_state,
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
        actor = next_actor
        turn.record = None
        turn.watcher = None
        turn.continuation = None
    if run.cancel_requested:
        turn.round_record = await run_in_threadpool(_stop, cid, sid, turn.round_record)


def _abort_meter(meter):
    if meter:
        meter.done("aborted")


async def _rescue(
    cid, sid, run, token, record, watcher, round_record, continuation, outcome, meter, error=None,
    *, tracked=None, served="",
):
    # Before `meter.done`, for `_frames`' reason: what a rescued partial is
    # filed under is the attempt that was running when the turn broke. With no
    # meter left, `served` is what `_frames` already settled on.
    if meter:
        served = streaming._served(meter, {"id": served})
        if error:
            meter.done("error", error.kind, detail=error.detail)
        else:
            meter.done("aborted")

    def save():
        with store.locks.campaign_lock(cid):
            _fence(cid, sid, run, token)
            if run.cancel_requested:
                # A Stop ends the chain however the interrupted contribution
                # is kept below -- saved for Retry, or paused on its roll.
                _stop(cid, sid, round_record)
            if watcher and record:
                current = store.responses.get(cid, sid, record["id"], private=True)
                if current["status"] != "complete":
                    watcher.finish()
                    if watcher.roll.complete or watcher.roll.truncated:
                        _pause(
                            cid,
                            sid,
                            run,
                            token,
                            record,
                            watcher,
                            round_record,
                            continuation,
                            outcome,
                            record["actor_ref"],
                            tracked,
                            served,
                        )
                        return
                    at = _save(
                        cid,
                        sid,
                        run,
                        token,
                        record,
                        watcher,
                        "incomplete",
                        round_record,
                        continuation,
                        tracked,
                        served,
                    )
                    outcome.persisted(at)
            # Only this run's own round, and only while it is unfinished: a
            # Stop may already have completed it (`_stop`), and marking it
            # `incomplete` again would hand Retry a round nobody interrupted.
            current_round = store.responses.unfinished(cid, sid)
            if (current_round and current_round["id"] == round_record["id"]
                    and current_round["status"] != "paused"):
                _round_state(cid, sid, round_record, status="incomplete")

    await run_in_threadpool(save)


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


def resume_roll(
    cid,
    sid,
    pid,
    request,
    client,
    conn,
    run,
    round_record,
    resolution,
    after_turn=None,
    *,
    appended_block=None,
):
    round_record = store.responses.update_round(cid, sid, round_record["id"], continuation=pid)
    block = appended_block or prompts.render("scene/roll_declined.j2")
    return start(
        cid,
        sid,
        request,
        client,
        conn,
        run,
        round_record=round_record,
        appended=(("Roll resolution", "system", block),),
        continuation=pid,
        after_turn=after_turn,
    )


def retry(cid, sid, request, client, conn, run, after_turn=None):
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
        conn,
        run,
        round_record=round_record,
        after_turn=after_turn,
        continuation=round_record.get("continuation"),
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


def _recover_completed(cid, sid, run, token, round_record):
    """`(round, successor choice)` once a pending contribution that already
    completed is published and the round advanced past it; None when the
    round has no completed contribution left to recover."""
    pending = round_record.get("pending_response")
    if not pending:
        return None
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
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
        else:
            store.responses.publish_saved(cid, sid, pending)
        variant = next(v for v in record["variants"] if v["id"] == record["active_variant"])
        used = list(dict.fromkeys([*round_record["used"], record["actor_ref"]]))
        after = _successor(cid, sid, {**round_record, "used": used}, variant.get("handoff"),
                           run.cancel_requested, record["actor_ref"])
        return _round_state(
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
        ), {**after, "issue": _ending_issue(round_record, after["issue"], variant.get("issue"))}


def _public_error(exc):
    if isinstance(exc, store.responses.ResponseNotFound):
        return HTTPException(404, detail="response not found")
    return HTTPException(409, detail={"kind": exc.kind, "detail": exc.detail})


@router.get("/campaigns/{cid}/scenes/{sid}/group")
def get_group(cid: str, sid: str):
    return store.group_play.settings_of(_require_scene(cid, sid)["meta"])


@router.put("/campaigns/{cid}/scenes/{sid}/group")
def put_group(cid: str, sid: str, body: GroupSettings):
    # Not `scene_held_free`: these settings change who speaks next, never the
    # transcript's shape. A running chain re-reads them at each successor
    # choice (who sits out) and at each follow-on round (order, plan and the
    # `auto_rounds` cap), so a change lands on its next decision.
    _require_scene(cid, sid)
    try:
        settings = store.group_play.validate(_dump(body))
    except ValueError as exc:
        raise HTTPException(400, detail={"kind": "invalid_group", "detail": str(exc)}) from exc
    try:
        store.scenes.set_group(cid, sid, store.group_play.dump(settings))
    except store.scenes.SceneNotFound as exc:
        raise HTTPException(404, detail="scene not found") from exc
    return {"ok": True, "settings": settings}


@router.get("/campaigns/{cid}/scenes/{sid}/responses/{rid}")
def get_response(cid: str, sid: str, rid: str):
    _require_scene(cid, sid)
    try:
        return store.responses.get(cid, sid, rid)
    except store.responses.ResponseNotFound as exc:
        raise _public_error(exc) from exc


@router.delete("/campaigns/{cid}/scenes/{sid}/responses/{rid}")
def delete_response(cid: str, sid: str, rid: str, request: Request):
    _require_scene(cid, sid)
    with runs.scene_held_free(request.app, cid, sid):
        messages = store.scenes.read_scene(cid, sid)["messages"]
        at = next((i for i, m in enumerate(messages) if m.get("response_id") == rid), None)
        try:
            store.responses.delete(cid, sid, rid)
        except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
            raise _public_error(exc) from exc
        # Every variant's tracker record goes with the response, and whatever
        # now sits where it was (`at` onward, renumbered) was built on it.
        # In the hold that deleted it; fail-soft.
        tracker_routes.after_cut(cid, sid, at)
        return store.scenes.read_scene(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{vid}/activate")
def activate_response(cid: str, sid: str, rid: str, vid: str, request: Request):
    _require_scene(cid, sid)
    with runs.scene_held_free(request.app, cid, sid):
        try:
            store.responses.activate(cid, sid, rid, vid)
        except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
            raise _public_error(exc) from exc
        # A swipe starts no update -- the variant's own record is kept for
        # exactly this -- but every later record was built on the variant that
        # was showing. In the hold that swapped it; fail-soft.
        tracker_routes.after_swipe(cid, sid, rid)
        return store.scenes.read_scene(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate")
def regenerate_response(
    cid: str,
    sid: str,
    rid: str,
    request: Request,
    body: RegenerateBody | None = None,
    client: LLMClient = Depends(get_llm),
    x_grimoire_attempt: str | None = Header(default=None),
):
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _turn_override(body)
    _require_scene(cid, sid)
    conn, _ = _override_connection(body, "regenerate", cid)
    run, fresh = runs.reserve_turn(request.app, cid, sid, "regenerate", x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        with store.locks.campaign_lock(cid):
            try:
                store.responses.editable(cid, sid, rid)
                record = store.responses.get(cid, sid, rid, private=True)
            except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
                raise _public_error(exc) from exc
            if not record["snapshot"]:
                raise HTTPException(
                    409,
                    detail={
                        "kind": "historical_context_unavailable",
                        "detail": "This legacy response has no frozen prompt. Explicitly replay from here instead.",
                    },
                )
            token = streaming._claim_turn(cid, sid)
            messages = PreparedMessages.from_snapshot(record["snapshot"], effective_model(conn),
                                                      campaign=cid)
            if body and body.guidance:
                messages = messages.with_appended(
                    {
                        "role": "system",
                        "content": prompts.render(
                            "scene/response_steer.j2", guidance=body.guidance
                        ),
                    }
                )
        outcome = streaming.StreamOutcome()
        frames = _reroll_frames(
            request.app, cid, sid, rid, client, conn, run, token, record, messages, outcome
        )
        runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))


async def _reroll_frames(app, cid, sid, rid, client, conn, run, token, record, messages, outcome):
    watcher = store.response_protocol.ResponseWatcher(perception=record["actor_ref"] != "grimoire")
    meter = store.usage.meter(
        "regenerate",
        campaign=cid,
        scene=sid,
        post=record.get("post"),
        round_id=record["round_id"] or "",
        response_id=rid,
    )
    # The new variant's tracker key, marked inside `_accept_reroll`'s hold and
    # started in `finally` once the terminal frames are out. The previous
    # variant's record is kept, so swiping back to it is free.
    tracked: list[tracker_routes.Mark] = []
    try:
        await run_in_threadpool(_capture, cid, sid, "regenerate", messages, conn)
        yield streaming._sse(
            {
                "response_start": {
                    "id": rid,
                    "speaker": record["speaker"],
                    "actor_ref": record["actor_ref"],
                }
            }
        )
        async for frame in _stream_contribution(
                client, messages, conn, meter, watcher, run, cid):
            yield frame
        served = streaming._served(meter, conn)
        meter.done()

        accepted = await run_in_threadpool(
            _accept_reroll, cid, sid, rid, run, token, record, watcher, tracked, served
        )
        if accepted:
            outcome.land()
            yield streaming._sse({"response_end": {"id": rid, "status": "complete"}})
            yield streaming._sse({"done": True})
        else:
            outcome.fail("replacement_incomplete", "The previous response was retained.")
            yield streaming._sse(
                {
                    "error": {
                        "kind": "replacement_incomplete",
                        "detail": "The previous response was retained.",
                    }
                }
            )
    except LLMError as exc:
        meter.done("error", exc.kind, detail=exc.detail)
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except BaseException:
        meter.done("aborted")
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await _start_tracking(app, cid, sid, client, tracked, run.scene_identity)


def _accept_reroll(cid, sid, rid, run, token, record, watcher, tracked=None, connection=""):
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        if run.cancel_requested or watcher.roll.complete or watcher.roll.truncated:
            return False
        text, issue, rewrite = _normalise(cid, sid, record, watcher.narration, connection)
        if issue:
            watcher.handoff = None
            watcher.issue = issue
        if not text.strip():
            return False
        variant = store.responses.save_variant(
            cid,
            sid,
            rid,
            text.strip(),
            "complete",
            handoff=watcher.handoff,
            issue=watcher.issue,
            activate=False,
            reasoning=watcher.reasoning + watcher.preparation_note,
            connection=connection,
        )
        store.responses.activate(cid, sid, rid, variant["id"])
        if rewrite:
            streaming._record_rewrite(cid, sid, rid, *rewrite, text.strip())
        # A reroll is a swipe to a new variant: every later tracker record was
        # built on the one it replaced. In this hold, with the swap; fail-soft.
        tracker_routes.after_swipe(cid, sid, rid)
        streaming._turn_settled(cid)
        if tracked is not None:
            # In this hold, for `_save`'s reason: reentrant here, a fresh wait
            # in front of the frames anywhere after it.
            marked = tracker_routes.mark(
                cid, sid, store.tracker.paths.response_key(rid, variant["id"]))
            if marked:
                tracked.append(marked)
        return True


def replay_actor(cid, sid):
    session = store.replay.read(cid)
    steps = session.get("steps", [])[session.get("done", 0) :]
    generation = next((step for step in steps if step.get("kind") == "generation"), {})
    first = next(iter(generation.get("messages", [])), {})
    rid = first.get("response_id")
    if rid:
        try:
            actor = store.responses.get(cid, sid, rid).get("actor_ref")
            if actor:
                validate_actor(cid, sid, actor)
                return actor
        except store.responses.ResponseNotFound:
            pass
    matches = [a["ref"] for a in roster(cid, sid) if a["name"] == first.get("speaker")]
    return matches[0] if len(matches) == 1 else "grimoire"
