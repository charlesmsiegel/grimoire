"""A post plans bounded rounds whose persisted speaker choices survive retry and recovery."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ... import decisions, draws, prompts, store
from ... import inference as operations
from ...llm_errors import LLMError
from .. import decision_capture, streaming
from ..common import require_inference
from . import _state
from ._router import router as router

_log = logging.getLogger('grimoire.character_turns')

#: Where a round's planning draws its randomness (talkativeness rolls, the
#: Natural shuffle). A seam for tests, which patch it to prove a retry or a
#: recovery continues the stored plan rather than rolling a new one.
_rng: Callable[[], random.Random] = random.Random


def _settings(cid, sid):
    return store.group_play.settings_of(store.scenes.read_scene(cid, sid)["meta"])


def _decision_successor(round_record):
    """A Directed automatic contribution delegates its follow-on to decide().

    Continue, reply-as without text, reroll and the other order modes retain
    their existing one-response or planned semantics.
    """
    return (round_record["automatic"]
            and round_record.get("mode", "directed") == "directed")


def _handoff_pool(cid, sid, round_record):
    """The round's eligible actors a Directed handoff may still name: whoever
    has started sitting out since the round opened is no longer one of them."""
    out = set(_settings(cid, sid)["sitting_out"])
    return [entry for entry in round_record["eligible"] if entry["ref"] not in out]


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
    cast = _state.roster(cid, sid)
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


def _follow_on(claim, round_record, lead):
    """The next automatic round of a player post's chain, or None when the
    scene's order is Manual now (which never auto-continues) or its
    `auto_rounds` has been lowered below the rounds already started.

    Planned by the scene's current order with the most recent contribution as
    its trigger -- its writer never counted as naming themselves -- and with
    `lead` (a Directed repeat handoff) speaking first. It keeps the original
    post, so usage stays attributed to what the player sent, and answers the
    Continue note, as an empty send would. A Directed round with no lead goes
    through the selector (`_first_actor`)."""
    cid, sid = claim.cid, claim.sid
    with store.locks.campaign_lock(cid):
        claim.fence()
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
        planned = _plan_round(settings, _state.roster(cid, sid), trigger=last["text"],
                              history=history, lead=lead, author=last["ref"])
        if planned["mode"] == "directed" and not lead and last["ref"]:
            # A newly authorised round resets used, not the ban on two
            # consecutive contributions by the same actor.
            planned["eligible"] = [e for e in planned["eligible"]
                                   if e["ref"] != last["ref"]]
        following = store.responses.new_round(
            cid,
            sid,
            automatic=True,
            post=round_record["post"],
            run_id=claim.run.id,
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
    return len(store.group_play.available(settings, _state.roster(cid, sid))) > 1


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
        nxt, plan = store.group_play.next_planned(_settings(cid, sid), _state.roster(cid, sid), plan)
    if not round_record["automatic"] or cancelled or round_record.get("stopped"):
        nxt, plan, lead = None, [], None
    return {"next": nxt, "issue": issue, "plan": plan, "handed_back": handed_back,
            "lead": lead}


def _selector_inputs(cid, sid, round_record):
    """`(item, trigger, author_ref)`: the pick's item, and what the round
    answers for the addressed narrowing (roadmap 02 §5.2 rule 2) -- the
    player-typed note when the round carries one (the player's, so no
    author), else the newest post in the pick's window as the model is shown
    it, with its writer's ref (None for the player's post, or for a post the
    response ledger never saw)."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    conversation = store.response_protocol.observable_conversation(
        messages, lambda posts, offset, total: store.regex.view.view(
            posts, cid=cid, phase="prompt", offset=offset, total=total))
    # The hand-back is described to a native endpoint as the player's turn,
    # by their character's name (the scene's {{user}}).
    player = store.context.macros.scene_substitutions(cid, sid).get("{{user}}", "")
    item = store.response_protocol.selector_item(
        round_record["eligible"], conversation, round_record.get("note", ""), player)
    if round_record.get("typed_note"):
        return item, round_record["typed_note"], None
    newest = next((m for m in reversed(messages)
                   if not store.scenes.is_excluded(m)
                   and m.get("speaker") not in store.scenes.SYNTHETIC_SPEAKERS), None)
    if newest is None or not conversation:
        return item, "", None
    author = (None if newest["role"] == "user"
              else store.responses.actor_refs(cid, sid).get(newest.get("response_id") or ""))
    return item, conversation[-1]["content"], author


async def _select(cid, sid, client, round_record):
    """`(next, issue, pick)` for a round with no lead: one `decide()` item, a choice
    over the eligible refs and `grimoire`, null allowed (spec 7.4). A round
    of one or none needs no question. The seam's 409 comes first, as before;
    an `LLMError` propagates as before, filed by the meter `decide` opens.

    The pick is one decision scope (`decision_capture`, roadmap 01b): each
    call is held as it settles -- the decide prompt as it was sent, the
    attempt it ran on, and what it decided -- and the scope files one
    prompt-log entry once `decide` has returned or failed. Nothing here reads
    a file on the event loop: the resolution, the scene read, the regex view
    and the item's own templates run in the threadpool, `decide` renders its
    prompt in a worker thread, and the scope files from the threadpool.

    A request `decide` refuses before sending (two eligible refs that read as
    one once normalised, or more than 254 of them, which with `grimoire` is
    past a choice's 255 options) is today's invalid handoff: the round raises the
    issue and control returns to the player, never a 500 mid-turn.

    With the campaign sampling the pick (`decide_speaker_sampling`, roadmap
    02-C2a), the issue mapping is read first and unchanged, and only then is
    WHO drawn from the report (`response_protocol.sampled_pick`), the draw's
    record returned as `pick` for `_first_actor` to store with the speaker.
    Off, `pick` is None and nothing is read past the switch. A `ValueError`
    from the draw is a defect: the answer stands, no pick is stored, and it
    is logged at ERROR by class only."""
    eligible = round_record["eligible"]
    if len(eligible) <= 1:
        return (eligible[0]["ref"] if eligible else "grimoire"), None, None
    resolved = await run_in_threadpool(
        lambda: require_inference("response-selector", cid, operation="decide"))
    sampling = await run_in_threadpool(_samples_pick, cid)
    item, trigger, author = await run_in_threadpool(_selector_inputs, cid, sid, round_record)
    try:
        async with decision_capture.capturing(cid, sid, "response-selector") as scope:
            decision = await operations.decide(
                "response-selector", [item], client=client, resolved=resolved,
                campaign=cid, scene=sid, post=round_record["post"],
                round_id=round_record["id"], capture=scope.hook())
    except decisions.DecideRequestError as exc:
        _log.warning("speaker pick refused for %s/%s, control returns to the player: %s",
                     cid, sid, exc)
        return None, store.response_protocol.INVALID_HANDOFF, None
    selection = store.response_protocol.selection_of(decision.items[0])
    if not sampling:
        return (*selection, None)
    try:
        offered = [entry["ref"] for entry in eligible] + [store.response_protocol.GRIMOIRE_REF]
        drawn = store.response_protocol.sampled_pick(
            item, decision.items[0], offered,
            store.response_protocol.addressed(trigger, eligible, author), draws.new_seed())
    except ValueError as exc:
        _log.error("speaker draw for %s/%s failed, the answer stands: %s",
                   cid, sid, type(exc).__name__)
        return (*selection, None)
    return store.response_protocol.next_of(selection, drawn), selection[1], drawn.record



def _successor_candidates(cid, sid, round_record, used, actor):
    """The actual legal NEXT-speaker pool, rebuilt after prose is saved.

    Default Directed play never repeats an NPC within one player round; the
    current actor is excluded even if the player enabled automatic extra
    rounds. A scene's explicitly enabled extra rounds allow earlier (non-
    current) speakers to lead the following round. The narrator has its own
    single-use slot and is never a replacement for an established NPC.
    """
    extended = round_record.get("auto_remaining", 0) > 0
    choices = [entry for entry in _handoff_pool(cid, sid, round_record)
               if entry["ref"] != actor and (extended or entry["ref"] not in used)]
    narrator = actor != "grimoire" and (extended or "grimoire" not in used)
    return choices, narrator


def _successor_inputs(cid, sid, round_record, choices, narrator):
    """Decide from the just-saved observable conversation, not a writer hint."""
    messages = store.scenes.read_scene(cid, sid)["messages"]
    # A follow-on is one player's response round. Earlier (possibly private)
    # transcript entries are unnecessary for choosing who reacts NOW.
    # Keep original transcript indices when applying prompt-phase regex rules.
    start = next((i for i in range(len(messages) - 1, -1, -1)
                  if messages[i]["role"] == "user"
                  and not store.scenes.is_excluded(messages[i])), 0)
    conversation = store.response_protocol.observable_conversation(
        messages[start:], lambda posts, offset, total: store.regex.view.view(
            posts, cid=cid, phase="prompt", offset=start + offset,
            total=start + total))
    player = store.context.macros.scene_substitutions(cid, sid).get("{{user}}", "")
    item = store.response_protocol.selector_item(
        choices, conversation, round_record.get("note", ""), player,
        follow_on=True, include_grimoire=narrator)
    trigger = conversation[-1]["content"] if conversation else ""
    return item, trigger


def _successor_answer(round_record, used, actor, selected, issue=None, *, exhausted=False):
    """The Decision answer as the engine's existing successor state shape.

    Only explicit extra rounds can repeat an already used NPC. A repeated
    handoff leads the FOLLOWING round, never another contribution this round.
    A pool exhausted without a model hand-back can start an authorized extra
    round; a deliberate null always returns control to the player.
    """
    lead = None
    if selected == actor:
        selected, issue = None, store.response_protocol.INELIGIBLE
    elif selected in used and selected is not None:
        if round_record.get("auto_remaining", 0) > 0:
            lead, selected = selected, None
        else:
            selected, issue = None, store.response_protocol.INELIGIBLE
    return {"next": selected, "issue": issue, "plan": [], "lead": lead,
            "handed_back": selected is None and lead is None and issue is None
                           and not exhausted}


async def _select_successor(cid, sid, client, round_record, used, actor, response_id=""):
    """Select EACH subsequent Directed speaker with the Decision operation.

    No scene-writing model handoff is consulted. Unlike the round opener,
    even one remaining NPC is a real choice against handing control back.
    Failures stop a successor safely, without failing prose already saved.
    """
    choices, narrator = await run_in_threadpool(
        _successor_candidates, cid, sid, round_record, used, actor)
    if not choices:
        # Never buy a narrator-only tail, even though its slot is present.
        # An explicitly enabled extra round may start by itself; an ordinary
        # round hands back rather than manufacturing a narrator contribution.
        return _successor_answer(
            round_record, used, actor, None,
            exhausted=round_record.get("auto_remaining", 0) > 0)
    try:
        resolved = await run_in_threadpool(
            lambda: require_inference("response-selector", cid, operation="decide"))
        sampling = await run_in_threadpool(_samples_pick, cid)
        item, trigger = await run_in_threadpool(
            _successor_inputs, cid, sid, round_record, choices, narrator)
        async with decision_capture.capturing(cid, sid, "response-selector") as scope:
            scope.note("", "phase", "successor")
            scope.note("", "previous", actor)
            decision = await operations.decide(
                "response-selector", [item], client=client, resolved=resolved,
                campaign=cid, scene=sid, post=round_record["post"],
                round_id=round_record["id"], response_id=response_id,
                capture=scope.hook())
            selection = store.response_protocol.selection_of(decision.items[0])
            if sampling and selection[0] is not None and selection[1] is None:
                try:
                    offered = [entry["ref"] for entry in choices]
                    if narrator:
                        offered.append("grimoire")
                    drawn = store.response_protocol.sampled_pick(
                        item, decision.items[0], offered,
                        store.response_protocol.addressed(trigger, choices, actor),
                        draws.new_seed())
                    selection = (store.response_protocol.next_of(selection, drawn),
                                 selection[1])
                    scope.note("", "draw", drawn.record)
                except ValueError as exc:
                    _log.error("successor draw failed: %s", type(exc).__name__)
            scope.note("", "selected", selection[0])
            scope.note("", "issue", selection[1])
    except (decisions.DecideRequestError, LLMError, HTTPException) as exc:
        _log.warning("Directed successor selection failed; keeping completed response: %s",
                     type(exc).__name__)
        return _successor_answer(round_record, used, actor, None,
                                 store.response_protocol.INVALID_HANDOFF)
    selected, issue = selection
    # The cast and sit-outs can change while a Decision request is running.
    # Never turn a now-ineligible answer into a new actor's call.
    if selected is not None:
        fresh, fresh_narrator = await run_in_threadpool(
            _successor_candidates, cid, sid, round_record, used, actor)
        legal = {entry["ref"] for entry in fresh}
        if fresh_narrator:
            legal.add("grimoire")
        if selected not in legal:
            return _successor_answer(round_record, used, actor, None,
                                     store.response_protocol.INELIGIBLE)
    return _successor_answer(round_record, used, actor, selected, issue)


def _samples_pick(cid):
    """Whether this campaign samples the speaker pick; fail-soft, since an
    optional step that cannot read its own switch leaves the pick as it was."""
    try:
        return store.play_decisions.speaker_sampling(cid)
    except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn
        _log.error("speaker sampling switch unreadable for %s: %s", cid, type(exc).__name__)
        return False


async def _first_actor(cid, sid, client, round_record):
    actor = round_record.get("actor_ref")
    if actor is None and round_record["automatic"] and round_record.get("mode") == "manual":
        # A Manual post: nobody answers until the player picks who.
        round_record = await run_in_threadpool(
            _state._round_state, cid, sid, round_record, status="complete")
        return None, round_record
    if actor is None and round_record.get("pick") is not None:
        # Decided (roadmap 02 §5.3): a drawn pick is stored with its speaker,
        # so a round that holds one is never asked or drawn again -- not by a
        # retry, a recovery or a roll resume, and not when it handed back.
        return None, round_record
    if actor is None:
        actor, issue, pick = await _select(cid, sid, client, round_record)
        round_record = await run_in_threadpool(
            _state._round_state, cid, sid, round_record, actor_ref=actor,
            status="pending" if actor else "complete", issue=issue,
            **({"pick": pick} if pick is not None else {}))
    return actor, round_record
