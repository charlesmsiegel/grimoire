"""Reroll and Keep writing create variants from frozen response context without destroying the previous take."""

from __future__ import annotations

from dataclasses import dataclass

import anyio
from fastapi import Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ... import prompts, store, wire
from ...llm import ATTEMPTED, LLMClient, prefill_capable
from ...llm_errors import LLMError
from ...model_guidance import PreparedMessages
from ...store.inference import providers as inference_providers
from .. import runs, streaming
from .. import tracker as tracker_routes
from ..common import _dump, _require_scene, _turn_override, get_llm, override_inference
from ..models import GroupSettings, RegenerateBody
from . import _generation, _persistence, _prompts, _state
from ._router import router as router


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
        record = store.responses.get(cid, sid, rid)
    except store.responses.ResponseNotFound as exc:
        raise _state._public_error(exc) from exc
    # The inspector's "Approach" line (roadmap 02 §6.5), worded here so the
    # client keeps no copy of the stance and tension vocabularies.
    return {**record, "approach": store.turn_plan.approach(record.get("intent"))}


@router.get("/campaigns/{cid}/scenes/{sid}/responses/{rid}/swipe")
def get_response_swipe(cid: str, sid: str, rid: str):
    _require_scene(cid, sid)
    try:
        return store.responses.swipe_state(cid, sid, rid)
    except store.responses.ResponseNotFound as exc:
        raise _state._public_error(exc) from exc


@router.delete("/campaigns/{cid}/scenes/{sid}/responses/{rid}")
def delete_response(cid: str, sid: str, rid: str, request: Request):
    _require_scene(cid, sid)
    with runs.scene_held_open(request.app, cid, sid):
        messages = store.scenes.read_scene(cid, sid)["messages"]
        at = next((i for i, m in enumerate(messages) if m.get("response_id") == rid), None)
        try:
            store.responses.delete(cid, sid, rid)
        except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
            raise _state._public_error(exc) from exc
        # Every variant's tracker record goes with the response, and whatever
        # now sits where it was (`at` onward, renumbered) was built on it.
        # In the hold that deleted it; fail-soft.
        tracker_routes.after_cut(cid, sid, at)
        streaming._prune_rewrites(cid, sid)
        return store.scenes.read_scene(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{vid}/activate")
def activate_response(cid: str, sid: str, rid: str, vid: str, request: Request):
    _require_scene(cid, sid)
    with runs.scene_held_open(request.app, cid, sid):
        try:
            store.responses.activate(cid, sid, rid, vid)
        except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
            raise _state._public_error(exc) from exc
        # A swipe starts no update -- the variant's own record is kept for
        # exactly this -- but every later record was built on the variant that
        # was showing. In the hold that swapped it; fail-soft.
        tracker_routes.after_swipe(cid, sid, rid)
        # Activating folds a continued response back into one message, so a
        # later part's own record has nothing left to describe.
        streaming._prune_rewrites(cid, sid)
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
    resolved, _ = override_inference(body, "regenerate", cid)
    run, fresh = runs.reserve_turn(request.app, cid, sid, "regenerate", x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        with store.locks.campaign_lock(cid):
            try:
                store.responses.editable(cid, sid, rid)
                record = store.responses.get(cid, sid, rid, private=True)
            except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
                raise _state._public_error(exc) from exc
            if not record["snapshot"]:
                raise HTTPException(
                    409,
                    detail={
                        "kind": "historical_context_unavailable",
                        "detail": "This legacy response has no frozen prompt. Explicitly replay from here instead.",
                    },
                )
            try:
                store.responses.require_context_included(
                    store.scenes.read_scene(cid, sid)["messages"], record)
            except store.responses.ResponseConflict as exc:
                raise _state._public_error(exc) from exc
            claim = _state._Claim.take(cid, sid, run)
            # Lock-free and non-minting, inside this hold: the round's typed
            # note, which a reroll of a director turn replays with its snapshot.
            note = store.responses.round_typed_note(cid, sid, record["round_id"])
            messages = PreparedMessages.from_snapshot(record["snapshot"],
                                                      resolved.chain.primary.model,
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
                # The durable half of the steer (store/steering.py), which the
                # end-of-scene absorb reads. Last in the hold, after every
                # refusal and every read that can raise, so a reroll that never
                # runs does not say a correction it never made.
                store.steering.record(cid, sid, body.guidance)
        outcome = streaming.StreamOutcome()
        frames = _reroll_frames(
            request.app, claim, rid, client, resolved, record, messages, outcome,
            note=note, guidance=(body.guidance if body else None) or "", task="regenerate",
        )
        runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/extend")
def extend_response(
    cid: str,
    sid: str,
    rid: str,
    request: Request,
    body: RegenerateBody | None = None,
    client: LLMClient = Depends(get_llm),
    x_grimoire_attempt: str | None = Header(default=None),
):
    """Keep writing: continue the trailing response, saved as a new variant
    `old + joiner + continuation` and activated, so the unextended take stays
    one swipe back. A detached `turn` run of kind `extend`, streamed and
    re-attachable exactly like `regenerate_response`."""
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _turn_override(body)
    _require_scene(cid, sid)
    resolved, _ = override_inference(body, "extend", cid)
    run, fresh = runs.reserve_turn(request.app, cid, sid, "extend", x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    guidance = (body.guidance if body else None) or ""
    with runs.reservation(request.app, run):
        with store.locks.campaign_lock(cid):
            plan = _extend_target(cid, sid, rid)
            claim = _state._Claim.take(cid, sid, run)
            note = store.responses.round_typed_note(cid, sid, plan.record["round_id"])
            messages = _extend_messages(plan.snapshot, resolved.chain.primary, plan.partial, guidance,
                                        plan.words, campaign=cid)
            if guidance:
                # Last in the hold, after every refusal, as a reroll's steer.
                store.steering.record(cid, sid, guidance)
        outcome = streaming.StreamOutcome()
        frames = _reroll_frames(
            request.app, claim, rid, client, resolved, plan.record, messages,
            outcome, note=note, guidance=guidance, task="extend", extend=plan,
        )
        runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))


async def _reroll_frames(app, claim, rid, client, resolved, record, messages, outcome, *,
                         note="", guidance="", task: str = "regenerate",
                         extend: ExtendPlan | None = None):
    """A reroll's stream -- or, with `extend`, a Keep writing continuation's.

    The two differ only at the edges: what the meter and prompt log call the
    call (`task`), whether the perception fence is expected (not in a prefill,
    where the model is mid-reply), the `extend.seed` the live bubble grows from,
    and how the result lands (`_accept_extend` joins it onto the reply).
    `resolved` is the reroll's own resolution of `task` (`override_inference`)."""
    cid, sid, run = claim.cid, claim.sid, claim.run
    chain = resolved.chain
    perception = record["actor_ref"] != "grimoire"
    if extend is not None:
        perception = perception and not _prefills(chain.primary)
    watcher = store.response_protocol.ResponseWatcher(perception=perception)
    # A reroll is a new variant of the same response, and the one replay that
    # may be offered the decide tool (§8.4): its loop comes after the frozen
    # snapshot, so it may decide differently, and its decision is this
    # variant's alone. Keep writing continues a reply whose loop is over.
    tool = tool_record = None
    if extend is None:
        tool, tool_record = await _generation._contribution_tool(
            cid, sid, client, {"id": record["round_id"] or "", "post": record.get("post")},
            record["actor_ref"], rid, task, messages, resolved, run)
    meter = _generation._LoopMeter(task, tool.holders) if tool is not None else store.usage.meter(
        task,
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
        await run_in_threadpool(_prompts._capture, cid, sid, task, messages, chain)
        yield streaming._sse(
            {
                "response_start": {
                    "id": rid,
                    "speaker": record["speaker"],
                    "actor_ref": record["actor_ref"],
                    # The reply as shown, which the live bubble grows from and a
                    # re-attached client rebuilds the same view with.
                    **({"extend": {"seed": extend.seed or extend.old}} if extend is not None else {}),
                }
            }
        )
        async for frame in _generation._stream_contribution(
                client, messages, resolved, meter, watcher, run, cid, task=task, tool=tool):
            yield frame
        served = streaming._served(meter, streaming._asked(resolved))
        meter.done()
        refusal: tuple[str, str] | None
        if extend is None:
            made_by = _persistence._made_by(meter, task, "primary", note, guidance)
            accepted = await run_in_threadpool(
                _accept_reroll, claim, rid, record, watcher, tracked=tracked, connection=served,
                made_by=made_by, tool=tool_record,
            )
            refusal = None if accepted else (
                "replacement_incomplete", "The previous response was retained.")
        else:
            mode = _served_mode(messages, meter, chain.primary)
            made_by = _persistence._made_by(
                meter, task, extend.composed, note, guidance,
                settings=extend.settings if extend.composed == "resume" else None,
                extra={"extends": extend.extends, "mode": mode},
            )
            refusal = await run_in_threadpool(
                _accept_extend, claim, rid, watcher, extend, mode, tracked=tracked,
                made_by=made_by, connection=served,
            )
        if refusal is None:
            outcome.land()
            yield streaming._sse({"response_end": {"id": rid, "status": "complete"}})
            yield streaming._sse({"done": True})
        else:
            kind, detail = refusal
            outcome.fail(kind, detail)
            yield streaming._sse({"error": {"kind": kind, "detail": detail}})
    except LLMError as exc:
        meter.done("error", exc.kind, detail=exc.detail)
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except BaseException:
        meter.done("aborted")
        raise
    finally:
        if tool is not None:
            await tool.scope.aclose()   # a no-op once the stream closed it
        with anyio.CancelScope(shield=True):
            await _state._start_tracking(app, cid, sid, client, tracked, run.scene_identity)


def _accept_reroll(claim, rid, record, watcher, *, tracked=None, connection="",
                   made_by=None, tool=None):
    cid, sid = claim.cid, claim.sid
    with store.locks.campaign_lock(cid):
        claim.fence()
        if claim.run.cancel_requested or watcher.roll.complete or watcher.roll.truncated:
            return False
        text, issue, rewrite = _persistence._normalise(cid, sid, record, watcher.narration, connection)
        if issue:
            watcher.handoff = None
            watcher.issue = issue
        if not text.strip():
            return False
        _persistence._land_variant(cid, sid, rid, text.strip(), handoff=watcher.handoff, issue=watcher.issue,
                      reasoning=watcher.reasoning + watcher.preparation_note,
                      tracked=tracked, made_by=made_by, connection=connection,
                      rewrite=rewrite, tool=tool)
        return True


@dataclass(frozen=True)
class ExtendPlan:
    """What a Keep writing run continues, resolved inside the route's hold.

    `old` is the reply as the transcript shows it (`responses.get`'s join), so a
    hand-trimmed reply is continued from the trim and the saved variant is
    `old + joiner + continuation`. `partial` is what the model is shown as its
    own turn: `old`, except while the reply is still split into parts by a
    declined roll, when the prompt is the latest resume snapshot -- which
    already holds the earlier parts as history -- and `partial` is the last
    part alone (plan-gate ruling 1). `partial` is in the prompt view, as any
    history the model reads is; `seed` is the reply in the display view, which
    the live bubble shows ahead of the continuation (`store/regex`)."""

    record: dict
    old: str
    partial: str
    snapshot: dict
    composed: str
    settings: dict | None
    extends: str
    words: int | None
    seed: str = ""


_EXTEND_RETAINED = "The previous response was retained."
_EXTEND_ROLL = "A continuation cannot propose a roll \u2014 reroll the reply instead"


def _refuse(kind: str, detail: str) -> HTTPException:
    return HTTPException(409, detail={"kind": kind, "detail": detail})


def _extend_target(cid: str, sid: str, rid: str) -> ExtendPlan:
    """The plan for continuing `rid`, or the refusal that stops it (gate
    resolution 6). Called inside the route's campaign-lock hold, before the
    turn is claimed, the steer recorded or anything spent; `run_in_flight` and
    `branch_closed` were already answered by `reserve_turn`."""
    try:
        store.responses.editable(cid, sid, rid)
        record = store.responses.get(cid, sid, rid, private=True)
    except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
        raise _state._public_error(exc) from exc
    messages = store.scenes.read_scene(cid, sid)["messages"]
    if not store.responses.is_trailing(messages, rid):
        # Continuing an earlier reply would rewrite what every later post was
        # built on.
        raise _refuse("not_last_response", "Only the last reply can be continued.")
    if not record["snapshot"]:
        raise _refuse("historical_context_unavailable",
                      "This legacy response has no frozen prompt. Explicitly replay from here instead.")
    try:
        store.responses.require_context_included(messages, record)
    except store.responses.ResponseConflict as exc:
        raise _state._public_error(exc) from exc
    if store.responses.unfinished(cid, sid) is not None:
        # Activating the new variant supersedes the round: a paused roll or a
        # Retry the player has not used would be lost.
        raise _refuse("round_open", "Finish or discard the open round before continuing a reply.")
    if (store.proposals.get(cid, sid) or {}).get("status") in store.proposals.NON_TERMINAL:
        raise _refuse("proposal_pending", "Resolve the proposed roll before continuing a reply.")
    if store.pending_reviews.read(cid, sid) is not None:
        # A new variant would invalidate the stored review's watermark: the
        # longest generation in the app, silently thrown away.
        raise _refuse("review_pending",
                      "This scene has a review waiting. Save or dismiss it before continuing a reply.")
    active = next((v for v in record["variants"] if v["id"] == record["active_variant"]), None)
    if active is None or active.get("status") != "complete":
        raise _refuse("variant_incomplete", "Only a completed reply can be continued.")
    parts = [m for m in messages if m.get("response_id") == rid and m.get("response_part")]
    # The reply as the model is shown it (the prompt view, like any history it
    # reads) and as the screen shows it (the display view, which the live
    # bubble grows from); `old` stays the stored text the continuation joins.
    shown = _extend_views(cid, messages, rid)
    if parts and record.get("resume_snapshot"):
        snapshot, composed = record["resume_snapshot"], "resume"
        settings = record.get("resume_settings")
        partial = shown["prompt"][-1] if shown["prompt"] else parts[-1]["content"]
    else:
        snapshot, composed = record["snapshot"], "primary"
        settings = record.get("settings")
        partial = "\n\n".join(shown["prompt"]) if shown["prompt"] else record["content"]
    return ExtendPlan(record=record, old=record["content"], partial=partial,
                      snapshot=snapshot, composed=composed, settings=settings,
                      extends=record["active_variant"],
                      words=(settings or {}).get("words"),
                      seed="\n\n".join(shown["display"]) if shown["display"] else record["content"])


def _extend_views(cid: str, messages: list[dict], rid: str) -> dict[str, list[str]]:
    """Each part of response `rid` in the `prompt` and `display` views, depth
    counted over the whole transcript (`store/regex`). Empty lists when the
    transcript holds no part of it."""
    own = [i for i, m in enumerate(messages) if m.get("response_id") == rid]
    if not own:
        return {"prompt": [], "display": []}
    lo = own[0]
    out = {}
    for phase in ("prompt", "display"):
        window = store.regex.view.view(messages[lo:], cid=cid, phase=phase, offset=lo,
                                       total=len(messages))
        out[phase] = [window[i - lo].get("content", "") for i in own]
    return out


def _served_mode(messages, meter, primary: wire.Target) -> str:
    """The tail the attempt that ANSWERED was sent -- a fallback picks its own,
    so the primary's mode is not evidence of what the model continued. The
    attempt the facade stamped (`llm.ATTEMPTED`), else `primary`."""
    attempted = meter.usage.get(ATTEMPTED) if meter is not None else None
    if not isinstance(attempted, wire.Target):
        attempted = primary
    return messages.mode_for(attempted) or _extend_mode(attempted)


def _accept_extend(claim, rid, watcher, plan: ExtendPlan, mode: str, *,
                   tracked=None, made_by=None, connection="") -> tuple[str, str] | None:
    """Join the continuation onto the reply as a new active variant, or say why
    not -- in which case nothing is written and the active variant stays."""
    cid, sid = claim.cid, claim.sid
    with store.locks.campaign_lock(cid):
        claim.fence()
        if claim.run.cancel_requested:
            return "replacement_incomplete", _EXTEND_RETAINED
        if watcher.roll.complete or watcher.roll.truncated:
            return "extend_roll_refused", _EXTEND_ROLL
        raw, note = watcher.narration, ""
        if mode == "instruction":
            # A watcher started for a prefill expected no fence; the fallback
            # that answered was asked for a reply, and may have written one.
            # Its body is kept as reasoning, as the watcher would have kept it.
            raw, note = store.response_protocol.strip_preparation(raw)
        lead = raw[: len(raw) - len(raw.lstrip())]
        text, issue, rewrite = _persistence._normalise(cid, sid, plan.record, raw, connection)
        if not text:
            return "replacement_incomplete", _EXTEND_RETAINED
        previous: dict = next(
            (v for v in plan.record["variants"] if v["id"] == plan.extends), {})
        joiner = _extend_joiner(lead, text, mode)
        _persistence._land_variant(cid, sid, rid, plan.old + joiner + text,
                      # The reply's handoff was decided when it was written; a
                      # continuation's own fence (if any) is not a second one.
                      handoff=previous.get("handoff"), issue=issue,
                      reasoning=watcher.reasoning + watcher.preparation_note + note,
                      tracked=tracked, made_by=made_by, connection=connection,
                      rewrite=_extend_rewrite(cid, sid, rid, plan, joiner, text, rewrite))
        return None


def _extend_rewrite(cid, sid, rid, plan: ExtendPlan, joiner: str, text: str,
                    rewrite: tuple[str, list[str]] | None) -> tuple[str, list[str]] | None:
    """`(original, fired rule ids)` for the Keep writing variant `plan.old +
    joiner + text`, or None when no part of it holds a stored rewrite.

    The variant is one message (activating it folds the reply's parts), so its
    record covers the whole text: each part of the reply it extends in its
    recorded original where a record still describes that part for the
    variant extended (`_extends_record`), then the joiner, then the
    continuation before the store phase rewrote it (`rewrite`). Without it, a
    continuation with nothing to rewrite would leave the reply's own record
    describing the variant swiped away from, and Restore original gone from a
    reply that still holds rewritten text. Called inside the accept's hold."""
    olds, rules, changed = [plan.old], [], False
    records = store.regex.rewrites.read_all(cid, sid)
    if records:
        parts = [m for m in store.scenes.read_scene(cid, sid)["messages"]
                 if m.get("response_id") == rid]
        if parts and "\n\n".join(m.get("content", "") for m in parts) == plan.old:
            olds = []
            for m in parts:
                rec = _extends_record(m, parts[0], records, plan.extends)
                olds.append(rec["original"] if rec else m.get("content", ""))
                if rec:
                    rules += rec.get("rules", [])
                    changed = True
    if rewrite:
        rules += rewrite[1]
        changed = True
    if not changed:
        return None
    original = "\n\n".join(olds) + joiner + (rewrite[0] if rewrite else text)
    return original, list(dict.fromkeys(rules))


def _extends_record(message: dict, first: dict, records: dict[str, dict],
                    variant: str) -> dict | None:
    """The stored-rewrite record that still describes one part of the reply a
    Keep writing run extends, or None -- the reading the scene read and Restore
    use (`routes.scenes._record_for`), narrowed to the variant extended."""
    rid = message.get("response_id") or ""

    def describes(m: dict, rec: dict) -> bool:
        return (rec.get("variant") in (None, variant)
                and str(rec.get("stored", "")).strip() == str(m.get("content", "")).strip())

    for key in store.regex.rewrites.candidates(message):
        rec = records.get(key)
        if (rec is None or not store.regex.rewrites.claims(message, key, rec)
                or not describes(message, rec)):
            continue
        if (first is not message and key == rid and message.get("response_part")
                and describes(first, rec)):
            continue    # the first part owns its bare key on a tie
        return rec
    return None


#: What may open a continuation that joins its reply with a space rather than a
#: new paragraph, in instruction mode (gate resolution 5): closing punctuation,
#: a closing quote or bracket, an ellipsis or a dash -- the reply's sentence
#: going on rather than a new one starting.
_EXTEND_CLOSERS = frozenset(".,;:!?)]\u201d\u2019'*\u2026\u2014")


def _prefills(target: wire.Target) -> bool:
    """Whether a "Keep writing" attempt on `target` is sent as a prefill.

    The connection's own opt-in (`llm.prefill_capable`, a gateway rule that
    cannot read the store), unless its provider preset rules prefill out: the
    Anthropic API's models from Claude 4.6 on answer a trailing assistant turn
    with a 400 (`never_for`), so an opt-in there would fail every Keep writing. `claude` lists prefill
    under `never` too, and is exempt: its SDK path has sent an opted-in
    connection the prefill tail since play controls IV, and slice B changes
    nothing an existing store does.

    A target carries no explicit provider preset, so it is placed by its kind
    and URL -- the answer its own preset gives, while every preset of a kind
    agrees on prefill (`test_no_provider_preset_changes_a_vision_or_prefill_answer`)."""
    if not prefill_capable(target):
        return False
    preset = inference_providers.infer({"kind": target.kind, "base_url": target.base_url})
    if preset.kind == "claude":
        return True
    return "prefill" not in inference_providers.never_for(preset, target.model)


def _extend_mode(target: wire.Target) -> str:
    """The tail a "Keep writing" attempt on `target` is sent."""
    return "prefill" if _prefills(target) else "instruction"


def _extend_messages(snapshot: dict, primary: wire.Target, partial: str, guidance: str,
                     words: int | None, campaign: str = "") -> PreparedMessages:
    """The extend prompt: the frozen snapshot, then the partial reply as the
    model's own turn, ending one of two ways per attempt (`with_tails`).

    The partial is projected the way history is (images down to their alt
    text) and carries no speaker label -- the model is continuing its own turn.
    Prefill: the partial is last, and a steer rides as the reroll's steer
    message before it. Instruction: a user message after it asks for the rest,
    carrying the steer."""
    partial_message = {"role": "assistant", "content": store.export.drop_images(partial)}
    steer = ([{"role": "system",
               "content": prompts.render("scene/response_steer.j2", guidance=guidance,
                                         continuation=True)}]
             if guidance else [])
    tails = {
        "prefill": [*steer, partial_message],
        "instruction": [partial_message, {
            "role": "user",
            "content": prompts.render("scene/extend_instruction.j2", words=words,
                                      guidance=guidance or ""),
        }],
    }
    return PreparedMessages.from_snapshot(snapshot, primary.model, campaign=campaign) \
        .with_tails(tails, _extend_mode, primary)


def _extend_joiner(lead: str, text: str, mode: str) -> str:
    """What goes between the reply and its continuation.

    Prefill: the model's own leading whitespace decides -- it was writing the
    same message, so no whitespace at all is its choice (finishing a word).
    Instruction: a new paragraph, unless the continuation plainly carries on the
    sentence (lowercase, or closing punctuation first), then a space."""
    if mode == "prefill":
        newlines = lead.count("\n")
        return "\n\n" if newlines >= 2 else "\n" if newlines == 1 else " " if lead else ""
    first = text[:1]
    return " " if first.islower() or first in _EXTEND_CLOSERS else "\n\n"


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
                _state.validate_actor(cid, sid, actor)
                return actor
        except store.responses.ResponseNotFound:
            pass
    matches = [a["ref"] for a in _state.roster(cid, sid) if a["name"] == first.get("speaker")]
    return matches[0] if len(matches) == 1 else "grimoire"
