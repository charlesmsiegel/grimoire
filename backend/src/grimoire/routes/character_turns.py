"""One assigned actor per call, one bounded round per player post."""

from __future__ import annotations

import copy
import logging
import random
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import content_parts, decisions, llm_reasoning, prompts, store
from .. import inference as operations
from ..llm import (
    ATTEMPTED,
    FALLBACK_KEY,
    LLMClient,
    effective_model,
    fallback_sampling,
    prefill_capable,
)
from ..llm_errors import LLMError
from ..model_guidance import PreparedMessages
from ..store.inference import providers as inference_providers
from . import runs, streaming
from . import tracker as tracker_routes
from .common import (
    _dump,
    _record_prompt,
    _require_scene,
    _turn_override,
    get_llm,
    override_inference,
    require_inference,
)
from .models import GroupSettings, RegenerateBody

router = APIRouter()
_log = logging.getLogger("grimoire.character_turns")

#: Where a round's planning draws its randomness (talkativeness rolls, the
#: Natural shuffle). A seam for tests, which patch it to prove a retry or a
#: recovery continues the stored plan rather than rolling a new one.
_rng: Callable[[], random.Random] = random.Random


def enabled():
    return True


def roster(cid, sid):
    return store.response_protocol.npc_roster(store.appearances.scene_cast(cid, sid))


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
    typed_note="",
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
                conn = require_inference("chat", cid).conn
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
                typed_note=typed_note,
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
    # Posts in context only: a hidden post names nobody, breaks no silence and
    # is no contribution to continue from (`_follow_on` reads the same way).
    history = store.scenes.without_excluded(scene["messages"])
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
        history = store.scenes.without_excluded(scene["messages"])
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


def _may_pick(cid, sid, kind):
    """Whether a fresh round of `kind` (with no explicit lead) may open on the
    speaker pick (`_first_actor` -> `_select`), answered from the scene alone.

    `_plan` is the exact answer, but it draws on `_rng`, and drawing here
    would move every seeded plan after it. This is the same reading with the
    random part taken at its widest, so it errs towards "may":

    - a post: Manual generates nothing and List and Natural always name their
      lead; Directed hands the selector whoever passes the talkativeness
      roll, which is at most everyone available (and a Directed chain's
      follow-on rounds go through the selector as well);
    - an empty send: List and Natural name the next speaker, Directed and
      Manual leave it to the selector;
    - a director note: every order leaves it to the selector.

    Either way it is a pick only with more than one available to pick from."""
    settings = _settings(cid, sid)
    mode = settings["order"]
    if kind == "post" and mode != "directed":
        return False
    if kind == "continue" and mode not in ("directed", "manual"):
        return False
    return len(store.group_play.available(settings, roster(cid, sid))) > 1


def refuse_an_unanswerable_pick(cid, sid, *, kind, actor_ref):
    """The speaker pick's 409 (spec 5.3's `incapable`: the Decision role on a
    model that can neither generate nor decide natively), raised while
    the request is here to be told and BEFORE `post_chat` reserves or writes
    anything -- a refusal after its first mutator would tell the player
    nothing happened when the post, a retired roll proposal and a started
    tracker update say otherwise. `start` resolves again as a defence."""
    if actor_ref or not _may_pick(cid, sid, kind):
        return
    require_inference("response-selector", cid, operation="decide")


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


def _selector_item(cid, sid, round_record):
    """The speaker pick as a decision item: the round's eligible roster, and
    the observable transcript gathered by `response_protocol` through the
    prompt-phase regex view (stored text is raw; the pick reads what a model
    is shown)."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    conversation = store.response_protocol.observable_conversation(
        messages, lambda posts, offset, total: store.regex.view.view(
            posts, cid=cid, phase="prompt", offset=offset, total=total))
    return store.response_protocol.selector_item(
        round_record["eligible"], conversation, round_record.get("note", ""))


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
                if record.get("resume_snapshot"):
                    return record, messages, "resume", record.get("resume_settings")
                return record, messages, "primary", None
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
            store.responses.save_resume_prompt(
                cid, sid, pending, messages.snapshot(), getattr(messages, "settings", None)
            )
            # The original pre-response snapshot remains the reroll boundary.
        else:
            record = store.responses.prepare(
                cid, sid, round_record["id"], actor, speaker, messages.snapshot(),
                getattr(messages, "settings", None),
            )
        _capture(cid, sid, "continuation" if appended else "chat", messages, conn)
        if pending and appended:
            return record, messages, "resume", getattr(messages, "settings", None)
        return record, messages, "primary", None


def _save(cid, sid, run, token, record, watcher, status, round_record, continuation=None,
          tracked=None, connection="", made_by=None):
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
            made_by=made_by,
        )
        before = streaming._active_variant(cid, sid, record["id"]) if continuation else ""
        if continuation and status == "complete":
            if not store.proposals.commit_narration(cid, sid, continuation, saved):
                raise store.responses.ResponseConflict(
                    "proposal_stale", "The roll continuation is stale."
                )
        else:
            saved()
        # Keyed by the part, as the transcript message it describes is: a
        # resumed continuation is a second message under the same response,
        # and its record must not replace the first part's.
        key = store.regex.rewrites.key_for(
            {"response_id": record["id"], "response_part": continuation or ""})
        if text and rewrite:
            streaming._record_rewrite(cid, sid, key, *rewrite, text,
                                      streaming._active_variant(cid, sid, record["id"]))
        if before:
            streaming._carry_rewrite(cid, sid, record["id"], before, skip=key)
        if continuation:
            # `commit_narration` trims an earlier attempt at the continuation.
            streaming._prune_rewrites(cid, sid)
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
        # `for_connection`: a tailed prompt (Keep writing) ends the way the
        # fallback's own connection was sent; for any other it is `for_model`.
        def capture_variant(model, _breakdown):
            variant = _variant_conn(conn, model)
            _capture(cid, sid, task, messages.for_connection(variant, model), variant)
        messages.on_variant = capture_variant


def _variant_conn(conn: dict, model: str) -> dict:
    """The connection a fallback variant was sent on, as its capture names it.

    The fallback this call carries (`llm.FALLBACK_KEY`, the resolver's own),
    with the route's sampler preset under `llm.fallback_sampling`'s rule --
    not the primary relabelled with the fallback's model, which would record
    the primary's preset and catalog list against a request that never carried
    them. Falls back to that relabel only when the call carries no fallback (a
    connection built by hand), minus the sampling block it cannot vouch for.
    """
    fallback = conn.get(FALLBACK_KEY)
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
    """`(next, issue)` for a round with no lead: one `decide()` item, a choice
    over the eligible refs and `grimoire`, null allowed (spec 7.4). A round
    of one or none needs no question. The seam's 409 comes first, as before;
    an `LLMError` propagates as before, filed by the meter `decide` opens.

    The capture records the decide prompt as it is sent (spec 9.4). Nothing
    here reads a file on the event loop: the resolution, the scene read, the
    regex view and the item's own templates run in the threadpool, `decide`
    renders its prompt in a worker thread, and the capture is handed back to
    the threadpool.

    A request `decide` refuses before sending (two eligible refs that read as
    one once normalised, or more than 254 of them, which with `grimoire` is
    past a choice's 255 options) is today's invalid handoff: the round raises the
    issue and control returns to the player, never a 500 mid-turn."""
    eligible = round_record["eligible"]
    if len(eligible) <= 1:
        return (eligible[0]["ref"] if eligible else "grimoire"), None
    resolved = await run_in_threadpool(
        lambda: require_inference("response-selector", cid, operation="decide"))
    item = await run_in_threadpool(_selector_item, cid, sid, round_record)
    try:
        decision = await operations.decide(
            "response-selector", [item], client=client, resolved=resolved,
            campaign=cid, scene=sid, post=round_record["post"], round_id=round_record["id"],
            capture=lambda msgs: run_in_threadpool(
                _capture, cid, sid, "response-selector", msgs, resolved.conn))
    except decisions.DecideRequestError as exc:
        _log.warning("speaker pick refused for %s/%s, control returns to the player: %s",
                     cid, sid, exc)
        return None, store.response_protocol.INVALID_HANDOFF
    return store.response_protocol.selection_of(decision.items[0])


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
           tracked=None, connection="", made_by=None):
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        if continuation:
            store.proposals.commit_narration(cid, sid, continuation, lambda: None)
            streaming._prune_rewrites(cid, sid)   # what the commit trimmed
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
                   tracked=tracked, connection=connection, made_by=made_by)
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
        # Which snapshot the contribution in flight runs on, and what made it
        # once its call is done: built before the meter is dropped, so a Stop
        # landing while `_save`/`_pause` runs still has it for `_rescue`.
        self.composed = "primary"
        self.composed_settings = None
        self.made_by = None


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
            made_by=turn.made_by, composed=turn.composed,
            composed_settings=turn.composed_settings,
        )
        outcome.fail(exc.kind, exc.detail)
        yield streaming._sse({"error": {"kind": exc.kind, "detail": exc.detail}})
    except BaseException:
        with anyio.CancelScope(shield=True):
            await _rescue(
                cid, sid, run, token, turn.record, turn.watcher, turn.round_record,
                turn.continuation, outcome, turn.meter, tracked=tracked, served=turn.served,
                made_by=turn.made_by, composed=turn.composed,
                composed_settings=turn.composed_settings,
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
        turn.made_by = None
        turn.record, messages, turn.composed, turn.composed_settings = await run_in_threadpool(
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
        turn.made_by = _made_by(
            turn.meter, turn.meter.task, turn.composed, turn.round_record.get("typed_note", ""),
            settings=turn.composed_settings,
        )
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
                made_by=turn.made_by,
            )
            turn.terminal = True
            yield streaming._sse({"proposal": {**proposal["payload"], "id": proposal["id"]}})
            outcome.land()
            yield streaming._sse({"done": True})
            return
        at = await run_in_threadpool(
            _save, cid, sid, run, token, record, watcher, status, turn.round_record,
            turn.continuation, tracked, served, made_by=turn.made_by,
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


def _made_by(
    meter, task: str, composed: str, note: str, guidance: str = "", *,
    settings: dict | None = None, extra: dict | None = None,
) -> dict | None:
    """What wrote a variant, for its `made_by`: the route the meter's holder
    says served the call, read after `meter.done()`.

    The holder rather than `meter.row`, because the row is None when the ledger
    append failed while the holder still has the values; and the holder rather
    than the connection the route resolved, because `llm._stamp` resets it per
    attempt, so it names a fallback that answered. A key the holder lacks is
    left out, never guessed (`effective_model` would be a guess). No reads -- it
    runs on the event loop -- and fail-soft (it logs): provenance never fails a
    turn.

    `settings` is given only for a resume-composed call: the record holds ONE
    `resume_settings`, which a later roll fence's recomposition overwrites, so
    a variant written from an earlier resume carries a copy of what its prompt
    rendered. A primary-composed variant reads the record's `settings`, which
    nothing overwrites, so it carries no copy.

    `extra` is merged last: a Keep writing variant's `extends` and `mode`."""
    if meter is None:
        return None
    try:
        usage = meter.usage
        served = {
            "connection_id": (usage.get(ATTEMPTED) or {}).get("id"),
            "connection": usage.get("connection"),
            "model": usage.get("model"),
            "provider": usage.get("provider"),
        }
        return {
            "task": task,
            **{key: value for key, value in served.items() if value},
            "composed": composed,
            "guidance": (guidance or "")[: store.alternates.MAX_GUIDANCE_CHARS],
            "note": (note or "")[: store.alternates.MAX_GUIDANCE_CHARS],
            **({"settings": copy.deepcopy(settings)} if settings is not None else {}),
            **copy.deepcopy(extra or {}),
        }
    except Exception:  # noqa: BLE001 - provenance must never fail a turn
        _log.exception("could not record what made a %s response", task)
        return None


def _abort_meter(meter):
    if meter:
        meter.done("aborted")


async def _rescue(
    cid, sid, run, token, record, watcher, round_record, continuation, outcome, meter, error=None,
    *, tracked=None, served="", made_by=None, composed="primary", composed_settings=None,
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
        made_by = _made_by(
            meter, meter.task, composed, round_record.get("typed_note", ""),
            settings=composed_settings,
        )

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
                            made_by=made_by,
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
                        made_by=made_by,
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
            streaming._prune_rewrites(cid, sid)   # what the commit trimmed
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


@router.get("/campaigns/{cid}/scenes/{sid}/responses/{rid}/swipe")
def get_response_swipe(cid: str, sid: str, rid: str):
    _require_scene(cid, sid)
    try:
        return store.responses.swipe_state(cid, sid, rid)
    except store.responses.ResponseNotFound as exc:
        raise _public_error(exc) from exc


@router.delete("/campaigns/{cid}/scenes/{sid}/responses/{rid}")
def delete_response(cid: str, sid: str, rid: str, request: Request):
    _require_scene(cid, sid)
    with runs.scene_held_open(request.app, cid, sid):
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
        streaming._prune_rewrites(cid, sid)
        return store.scenes.read_scene(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{vid}/activate")
def activate_response(cid: str, sid: str, rid: str, vid: str, request: Request):
    _require_scene(cid, sid)
    with runs.scene_held_open(request.app, cid, sid):
        try:
            store.responses.activate(cid, sid, rid, vid)
        except (store.responses.ResponseNotFound, store.responses.ResponseConflict) as exc:
            raise _public_error(exc) from exc
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
    conn = resolved.conn
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
            try:
                store.responses.require_context_included(
                    store.scenes.read_scene(cid, sid)["messages"], record)
            except store.responses.ResponseConflict as exc:
                raise _public_error(exc) from exc
            token = streaming._claim_turn(cid, sid)
            # Lock-free and non-minting, inside this hold: the round's typed
            # note, which a reroll of a director turn replays with its snapshot.
            note = store.responses.round_typed_note(cid, sid, record["round_id"])
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
                # The durable half of the steer (store/steering.py), which the
                # end-of-scene absorb reads. Last in the hold, after every
                # refusal and every read that can raise, so a reroll that never
                # runs does not say a correction it never made.
                store.steering.record(cid, sid, body.guidance)
        outcome = streaming.StreamOutcome()
        frames = _reroll_frames(
            request.app, cid, sid, rid, client, conn, run, token, record, messages, outcome,
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
    conn = resolved.conn
    run, fresh = runs.reserve_turn(request.app, cid, sid, "extend", x_grimoire_attempt)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    guidance = (body.guidance if body else None) or ""
    with runs.reservation(request.app, run):
        with store.locks.campaign_lock(cid):
            plan = _extend_target(cid, sid, rid)
            token = streaming._claim_turn(cid, sid)
            note = store.responses.round_typed_note(cid, sid, plan.record["round_id"])
            messages = _extend_messages(plan.snapshot, conn, plan.partial, guidance, plan.words,
                                        campaign=cid)
            if guidance:
                # Last in the hold, after every refusal, as a reroll's steer.
                store.steering.record(cid, sid, guidance)
        outcome = streaming.StreamOutcome()
        frames = _reroll_frames(
            request.app, cid, sid, rid, client, conn, run, token, plan.record, messages, outcome,
            note=note, guidance=guidance, task="extend", extend=plan,
        )
        runs.start_detached(request.app, run, lambda: frames, outcome=outcome.result)
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))


async def _reroll_frames(app, cid, sid, rid, client, conn, run, token, record, messages, outcome,
                         *, note="", guidance="", task: str = "regenerate",
                         extend: ExtendPlan | None = None):
    """A reroll's stream -- or, with `extend`, a Keep writing continuation's.

    The two differ only at the edges: what the meter and prompt log call the
    call (`task`), whether the perception fence is expected (not in a prefill,
    where the model is mid-reply), the `extend.seed` the live bubble grows from,
    and how the result lands (`_accept_extend` joins it onto the reply)."""
    perception = record["actor_ref"] != "grimoire"
    if extend is not None:
        perception = perception and not _prefills(conn)
    watcher = store.response_protocol.ResponseWatcher(perception=perception)
    meter = store.usage.meter(
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
        await run_in_threadpool(_capture, cid, sid, task, messages, conn)
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
        async for frame in _stream_contribution(
                client, messages, conn, meter, watcher, run, cid):
            yield frame
        served = streaming._served(meter, conn)
        meter.done()
        refusal: tuple[str, str] | None
        if extend is None:
            made_by = _made_by(meter, task, "primary", note, guidance)
            accepted = await run_in_threadpool(
                _accept_reroll, cid, sid, rid, run, token, record, watcher, tracked, served,
                made_by=made_by,
            )
            refusal = None if accepted else (
                "replacement_incomplete", "The previous response was retained.")
        else:
            mode = _served_mode(messages, meter, conn)
            made_by = _made_by(
                meter, task, extend.composed, note, guidance,
                settings=extend.settings if extend.composed == "resume" else None,
                extra={"extends": extend.extends, "mode": mode},
            )
            refusal = await run_in_threadpool(
                _accept_extend, cid, sid, rid, run, token, watcher, extend, mode,
                tracked, made_by, served,
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
        with anyio.CancelScope(shield=True):
            await _start_tracking(app, cid, sid, client, tracked, run.scene_identity)


def _accept_reroll(cid, sid, rid, run, token, record, watcher, tracked=None, connection="",
                   made_by=None):
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
        _land_variant(cid, sid, rid, text.strip(), handoff=watcher.handoff, issue=watcher.issue,
                      reasoning=watcher.reasoning + watcher.preparation_note,
                      tracked=tracked, made_by=made_by, connection=connection,
                      rewrite=rewrite)
        return True


def _land_variant(cid, sid, rid, text, *, handoff, issue, reasoning, tracked=None,
                  made_by=None, connection="",
                  rewrite: tuple[str, list[str]] | None = None) -> None:
    """Save `text` as a new complete variant of `rid` and make it the one shown.

    The tail a reroll and a Keep writing continuation share. Called inside the
    caller's campaign-lock hold, after its fence. `connection` is the one that
    served the call; `rewrite` is `(original, fired rule ids)` when the store
    phase rewrote `text`, recorded under the response for the new variant --
    activating folds the response into one message, keyed by its bare id."""
    variant = store.responses.save_variant(
        cid,
        sid,
        rid,
        text,
        "complete",
        handoff=handoff,
        issue=issue,
        activate=False,
        reasoning=reasoning,
        connection=connection,
        made_by=made_by,
    )
    store.responses.activate(cid, sid, rid, variant["id"])
    if rewrite:
        streaming._record_rewrite(cid, sid, rid, *rewrite, text, variant["id"])
    streaming._prune_rewrites(cid, sid)
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
        raise _public_error(exc) from exc
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
        raise _public_error(exc) from exc
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


def _served_mode(messages, meter, conn) -> str:
    """The tail the attempt that ANSWERED was sent -- a fallback picks its own,
    so the primary's mode is not evidence of what the model continued."""
    attempted = (meter.usage.get(ATTEMPTED) if meter is not None else None) or conn
    return messages.mode_for(attempted) or _extend_mode(attempted)


def _accept_extend(cid, sid, rid, run, token, watcher, plan: ExtendPlan, mode: str,
                   tracked=None, made_by=None, connection="") -> tuple[str, str] | None:
    """Join the continuation onto the reply as a new active variant, or say why
    not -- in which case nothing is written and the active variant stays."""
    with store.locks.campaign_lock(cid):
        _fence(cid, sid, run, token)
        if run.cancel_requested:
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
        text, issue, rewrite = _normalise(cid, sid, plan.record, raw, connection)
        if not text:
            return "replacement_incomplete", _EXTEND_RETAINED
        previous: dict = next(
            (v for v in plan.record["variants"] if v["id"] == plan.extends), {})
        joiner = _extend_joiner(lead, text, mode)
        _land_variant(cid, sid, rid, plan.old + joiner + text,
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


def _prefills(conn: dict) -> bool:
    """Whether a "Keep writing" attempt on `conn` is sent as a prefill.

    The connection's own opt-in (`llm.prefill_capable`, a gateway rule that
    cannot read the store), unless its provider preset rules prefill out: the
    Anthropic API's models from Claude 4.6 on answer a trailing assistant turn
    with a 400 (`never_for`), so an opt-in there would fail every Keep writing. `claude` lists prefill
    under `never` too, and is exempt: its SDK path has sent an opted-in
    connection the prefill tail since play controls IV, and slice B changes
    nothing an existing store does."""
    if not prefill_capable(conn):
        return False
    preset = inference_providers.infer(conn)
    if preset.kind == "claude":
        return True
    return "prefill" not in inference_providers.never_for(preset,
                                                          str(conn.get("model") or ""))


def _extend_mode(conn: dict) -> str:
    """The tail a "Keep writing" attempt on `conn` is sent."""
    return "prefill" if _prefills(conn) else "instruction"


def _extend_messages(snapshot: dict, conn: dict, partial: str, guidance: str,
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
    return PreparedMessages.from_snapshot(snapshot, effective_model(conn), campaign=campaign) \
        .with_tails(tails, _extend_mode, conn)


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
                validate_actor(cid, sid, actor)
                return actor
        except store.responses.ResponseNotFound:
            pass
    matches = [a["ref"] for a in roster(cid, sid) if a["name"] == first.get("speaker")]
    return matches[0] if len(matches) == 1 else "grimoire"
