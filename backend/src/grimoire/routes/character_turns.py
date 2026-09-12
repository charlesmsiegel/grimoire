"""One assigned actor per call, one bounded round per player post."""

from __future__ import annotations

import json
from contextlib import aclosing

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import llm_reasoning, prompts, store
from ..llm import LLMClient, effective_model
from ..llm_errors import LLMError
from ..model_guidance import PreparedMessages
from . import runs, streaming
from .common import (
    _override_connection,
    _record_prompt,
    _require_connection,
    _require_scene,
    get_llm,
)
from .models import RegenerateBody

router = APIRouter()


def enabled():
    return store.config.read_config().get("character_response_mode", "individual") != "combined"


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


def _compose(cid, sid, round_record, actor, conn, appended=()):
    # The prompt must offer the same remaining slots that the engine accepts.
    # Offering the current/used actors teaches an invalid handoff; omitting the
    # narrator hides a valid slot. Explicit one-response requests have no next.
    used = {*round_record["used"], actor}
    candidates = [entry for entry in [*round_record["eligible"],
                                     {"ref": "grimoire", "name": "Grimoire"}]
                  if round_record["automatic"] and entry["ref"] not in used]
    kwargs = {
        "turn": round_record.get("turn"),
        "actor_ref": actor,
        "eligible_speakers": candidates,
        "describe": store.prompt_log.capturing(),
        "model": effective_model(conn),
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
):
    with store.locks.campaign_lock(cid):
        token = streaming._claim_turn(cid, sid)
        _fence(cid, sid, run, token)
        if round_record is None:
            round_record = store.responses.new_round(
                cid,
                sid,
                eligible=roster(cid, sid),
                automatic=automatic,
                post=post,
                run_id=run.id,
                actor_ref=actor_ref,
                note=note,
                turn=turn,
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
        after_turn=after_turn,
        appended=appended,
        continuation=continuation,
    )
    runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


def _selector_messages(cid, sid, round_record):
    messages = store.scenes.read_scene(cid, sid)["messages"]
    public = [
        {
            "speaker": m.get("speaker") or ("You" if m["role"] == "user" else "Grimoire"),
            "content": m["content"],
        }
        for m in messages[-12:]
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
                    record.get("resume_snapshot") or record["snapshot"], effective_model(conn)
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
        speaker = next(
            (r["name"] for r in round_record["eligible"] if r["ref"] == actor), "Grimoire"
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


def _save(cid, sid, run, token, record, watcher, status, round_record, continuation=None):
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        text, tracked, authority_issue = _normalise(cid, sid, record, watcher.narration)
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
            reasoning=watcher.reasoning,
        )
        if continuation and status == "complete":
            if not store.proposals.commit_narration(cid, sid, continuation, saved):
                raise store.responses.ResponseConflict(
                    "proposal_stale", "The roll continuation is stale."
                )
        else:
            saved()
        if tracked and text:
            streaming._record_turnstate(
                cid,
                sid,
                len(store.scenes.read_scene(cid, sid)["messages"]) - 1,
                [{"speaker": record["speaker"], "content": text}],
                tracked,
            )
        streaming._turn_settled(cid)
        return streaming._tail_length(cid, sid) if text else None


def _normalise(cid, sid, record, text):
    text, tracked = store.turnstate.split_block(text)
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
    state = tracked.get(record["actor_ref"], tracked.get(record["speaker"]))
    tracked = {record["speaker"]: state} if state and record["actor_ref"] != "grimoire" else {}
    tracked = store.turnstate.expand_values(
        tracked,
        lambda value: store.context.expand_macros(
            value, store.context.scene_substitutions(cid, sid), cid, sid
        ),
    )
    return "\n\n".join(part for part in parts if part), tracked, issue


def _capture(cid, sid, task, messages, conn):
    if not store.prompt_log.capturing():
        return
    breakdown = getattr(messages, "breakdown", None)
    if breakdown is None:
        rows = [
            {
                "id": f"message_{i}",
                "label": m["role"],
                "text": m["content"],
                "tier": "lock-in",
                "dropped": False,
                "pinned": False,
                "trimmed": 0,
                "tokens": store.tokens.count_tokens(m["content"]),
            }
            for i, m in enumerate(messages)
        ]
        breakdown = {
            "sections": rows,
            "total_tokens": sum(row["tokens"] for row in rows),
            "dropped_tokens": 0,
            "budget_tokens": store.context.budget_tokens(),
        }
    _record_prompt(cid, sid, task, breakdown, model=effective_model(conn), messages=messages)
    if isinstance(messages, PreparedMessages):
        # Steered frozen variants have no historical section accounting. Capture
        # their exact rendered messages at actual fallback dispatch instead.
        messages.on_variant = lambda model, _: _capture(
            cid, sid, task, messages.for_model(model), {**conn, "model": model}
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


async def _stream_contribution(client, messages, conn, meter, watcher, run):
    async with aclosing(llm_reasoning.stream(client, messages, conn, meter.usage)) as source:
        async for event in source:
            if run.cancel_requested:
                raise anyio.get_cancelled_exc_class()()
            if event.get("thinking_reset"):
                watcher.reasoning = ""
                yield streaming._sse(event)
                continue
            if "thinking_delta" in event:
                watcher.reasoning += event["thinking_delta"]
                yield streaming._sse(event)
                continue
            delta = event["delta"]
            visible = watcher.feed(delta)
            if visible:
                yield streaming._sse({"delta": visible})
            elif not delta:
                yield streaming._HEARTBEAT
            if watcher.roll.complete:
                break
    visible = watcher.finish()
    if visible:
        yield streaming._sse({"delta": visible})
    if run.cancel_requested:
        # Stop can arrive while the final visible delta is being delivered.
        # Rescue must retain this pending slot just as for midstream Stop.
        raise anyio.get_cancelled_exc_class()()


def _pause(cid, sid, run, token, record, watcher, round_record, continuation, outcome, actor):
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
        at = _save(cid, sid, run, token, record, watcher, "incomplete", round_record, continuation)
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
    if actor is None:
        actor, issue = await _select(cid, sid, client, round_record)
        round_record = await run_in_threadpool(
            _round_state, cid, sid, round_record, actor_ref=actor,
            status="pending" if actor else "complete", issue=issue)
    return actor, round_record


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
    after_turn=None,
    appended=(),
    continuation=None,
):
    actor = round_record.get("actor_ref")
    record = None
    watcher = None
    meter = None
    try:
        actor, round_record = await _first_actor(cid, sid, client, round_record)
        while actor and not run.cancel_requested:
            await anyio.lowlevel.checkpoint()
            current = await run_in_threadpool(roster, cid, sid)
            if actor not in [r["ref"] for r in current] + ["grimoire"]:
                await run_in_threadpool(
                    _round_state,
                    cid,
                    sid,
                    round_record,
                    status="complete",
                    issue="speaker left the scene",
                )
                break
            recovered = await run_in_threadpool(
                _recover_completed, cid, sid, run, token, round_record
            )
            if recovered is not None:
                round_record = recovered
                actor = recovered.get("actor_ref")
                continue
            record, messages = await run_in_threadpool(
                _prepare, cid, sid, run, token, round_record, actor, conn, appended
            )
            appended = ()
            watcher = store.response_protocol.ResponseWatcher()
            yield streaming._sse(
                {
                    "response_start": {
                        "id": record["id"],
                        "speaker": record["speaker"],
                        "actor_ref": actor,
                    }
                }
            )
            meter = store.usage.meter(
                "continuation" if continuation else "chat",
                campaign=cid,
                scene=sid,
                post=round_record["post"],
                round_id=round_record["id"],
                response_id=record["id"],
            )
            async for frame in _stream_contribution(client, messages, conn, meter, watcher, run):
                yield frame
            meter.done()
            meter = None
            paused = watcher.roll.complete or watcher.roll.truncated
            status = "incomplete" if paused or run.cancel_requested else "complete"
            if paused:
                proposal = await run_in_threadpool(
                    _pause,
                    cid,
                    sid,
                    run,
                    token,
                    record,
                    watcher,
                    round_record,
                    continuation,
                    outcome,
                    actor,
                )
                yield streaming._sse({"proposal": {**proposal["payload"], "id": proposal["id"]}})
                outcome.land()
                yield streaming._sse({"done": True})
                return
            at = await run_in_threadpool(
                _save, cid, sid, run, token, record, watcher, status, round_record, continuation
            )
            outcome.persisted(at)
            if watcher.issue == "empty response":
                await run_in_threadpool(_round_state, cid, sid, round_record, status="incomplete")
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
            used = [*round_record["used"], actor]
            # Persistence is complete before metadata may authorize a successor.
            next_actor, issue = store.response_protocol.validate_handoff(
                watcher.handoff, [r["ref"] for r in round_record["eligible"]] + ["grimoire"], used
            )
            next_actor = (
                next_actor if round_record["automatic"] and not run.cancel_requested else None
            )
            round_record = await run_in_threadpool(
                _round_state,
                cid,
                sid,
                round_record,
                used=used,
                pending_response=None,
                actor_ref=next_actor,
                status="pending" if next_actor else "complete",
                issue=issue,
                continuation=None,
                completed_response=record["id"],
                control_issue=issue,
            )
            yield streaming._sse({"response_end": {"id": record["id"], "status": status}})
            actor = next_actor
            record = None
            watcher = None
            continuation = None
        outcome.land()
        yield streaming._sse({"done": True})
    except store.responses.ResponseConflict as exc:
        _abort_meter(meter)
        # A lost scene/turn fence must never rescue into its replacement.
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except LLMError as exc:
        await _rescue(
            cid, sid, run, token, record, watcher, round_record, continuation, outcome, meter, exc
        )
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except BaseException:
        with anyio.CancelScope(shield=True):
            await _rescue(
                cid, sid, run, token, record, watcher, round_record, continuation, outcome, meter
            )
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await streaming._fire_follow_up(after_turn, outcome)


def _abort_meter(meter):
    if meter:
        meter.done("aborted")


async def _rescue(
    cid, sid, run, token, record, watcher, round_record, continuation, outcome, meter, error=None
):
    if meter:
        if error:
            meter.done("error", error.kind, detail=error.detail)
        else:
            meter.done("aborted")

    def save():
        with store.locks.campaign_lock(cid):
            _fence(cid, sid, run, token)
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
                    )
                    outcome.persisted(at)
            current_round = store.responses.unfinished(cid, sid)
            if current_round and current_round["status"] != "paused":
                _round_state(cid, sid, round_record, status="incomplete")

    await run_in_threadpool(save)


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
        actor, issue = store.response_protocol.validate_handoff(
            variant.get("handoff"),
            [r["ref"] for r in round_record["eligible"]] + ["grimoire"],
            used,
        )
        if not round_record["automatic"] or run.cancel_requested:
            actor = None
        return _round_state(
            cid,
            sid,
            round_record,
            used=used,
            pending_response=None,
            actor_ref=actor,
            status="pending" if actor else "complete",
            issue=issue,
            continuation=None,
        )


def _public_error(exc):
    if isinstance(exc, store.responses.ResponseNotFound):
        return HTTPException(404, detail="response not found")
    return HTTPException(409, detail={"kind": exc.kind, "detail": exc.detail})


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
        try:
            store.responses.delete(cid, sid, rid)
        except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
            raise _public_error(exc) from exc
        return store.scenes.read_scene(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{vid}/activate")
def activate_response(cid: str, sid: str, rid: str, vid: str, request: Request):
    _require_scene(cid, sid)
    with runs.scene_held_free(request.app, cid, sid):
        try:
            store.responses.activate(cid, sid, rid, vid)
        except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
            raise _public_error(exc) from exc
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
            messages = PreparedMessages.from_snapshot(record["snapshot"], effective_model(conn))
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
        frames = _reroll_frames(cid, sid, rid, client, conn, run, token, record, messages, outcome)
        runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))


async def _reroll_frames(cid, sid, rid, client, conn, run, token, record, messages, outcome):
    watcher = store.response_protocol.ResponseWatcher()
    meter = store.usage.meter(
        "regenerate",
        campaign=cid,
        scene=sid,
        post=record.get("post"),
        round_id=record["round_id"] or "",
        response_id=rid,
    )
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
        async for frame in _stream_contribution(client, messages, conn, meter, watcher, run):
            yield frame
        meter.done()

        accepted = await run_in_threadpool(
            _accept_reroll, cid, sid, rid, run, token, record, watcher
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


def _accept_reroll(cid, sid, rid, run, token, record, watcher):
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        if run.cancel_requested or watcher.roll.complete or watcher.roll.truncated:
            return False
        text, _, issue = _normalise(cid, sid, record, watcher.narration)
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
            reasoning=watcher.reasoning,
        )
        store.responses.activate(cid, sid, rid, variant["id"])
        streaming._turn_settled(cid)
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
