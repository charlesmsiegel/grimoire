"""Labelling a player character's action (035 milestone 2): one ``labelling`` call, its reply bounded into
proposed 029 labels and written on the action's turn. Nothing here frames, decides or accepts anything.

Two handles, by the requester's decision in the Wave 6 audit. The labeller's call runs on its own untrusted
handle (``LabelRuntime``), and so does the proposal: before a labelling that holds anything read from the
reply is written, the runner's ``label_handle`` (untrusted, issued by the library's authority) must be allowed
to ``propose`` that turn's canonical payload. The owner's handle then writes the turn file, as it writes every turn
(TOOL_AUTHORITY's narrow 035 exception: it never acts on model text). A labelling with nothing read from a
reply (running, refused) is the runner's own record and needs no proposal.
"""

from __future__ import annotations

import json
from dataclasses import replace

from grimoire.context import ContextError, controller_projection
from grimoire.contextstore import player_context
from grimoire.core import DIRECTOR
from grimoire.core.identity import ChatRef
from grimoire.core.inference import InferencePlanError, InferenceStage, OptionalRequest, TurnUsageLedger, plan_turn
from grimoire.adjudication import AdjudicationError
from grimoire.labelling import LabelRefused, facts, label_messages, parse_attempt, parse_labels
from grimoire.obstacles import Attempt, ObstacleError, check_attempt
from grimoire.play.accept import next_sequence
from grimoire.play.adjudicate import frame_of, outcome_lines
from grimoire.play.obstacles import attempt_labels, check_moment, fixed_attempt, open_obstacle, refuse_live_link
from grimoire.play.obstacles import offered as obstacle_offer
from grimoire.play.boundaries import HELD, UNREADABLE, check_presented
from grimoire.play.scene import WRITE_RETRIES, _perceived, _refuse, _table_audience, _table_known
from grimoire.play.tracing import record_calls, span
from grimoire.providers.meter import TurnMeter
from grimoire.scene import MAX_LABELS, Against, CallRecord, Label, SceneRefused, TurnRecord, heard_for, render
from grimoire.scene.codec import turn_to_payload
from grimoire.scenestore import StoredTurn
from grimoire.security import Permission
from grimoire.store.layout import address_parts

#: The planned call's purpose: framing, never a reply.
PURPOSE = "frame a player action"


def own_propositions(knowledge, controller: str, controls, actor, as_of: int) -> frozenset[str]:
    """The propositions ``actor`` believes on ``controller``'s own authority (020's ``controller_knowledge``, the
    projection 031's controller pack is built from): a belief authored by anyone else (an NPC-era conclusion the
    player inherited) is not the player's. Control that is ambiguous, or knowledge that cannot be projected,
    offers none (fail closed)."""
    projected = controller_projection(knowledge, actor, controller, controls, as_of=as_of)
    if projected is None:
        return frozenset()
    return frozenset(belief.proposition_id for belief in projected.current if belief.stance == "believed")


def label_action(runner, chat: ChatRef, by, obstacle: str | None = None) -> Label:
    """See ``SceneRunner.label``. With ``obstacle``, the labeller also chooses the approach (``play/obstacles.py``)."""
    context = runner._context(chat)
    scene = context.scene
    principal = runner._principal(scene, by)
    runner._ready(scene)
    if runner.labeller is None or runner.label_handle is None:
        raise _refuse("no_labeller", "no labeller is configured")
    # 016, as for generation and before any call: the labeller's stakes are model text the table reads
    check_presented(runner, chat, _table_audience(scene, principal), complete=_table_known(scene))
    record = scene.pending
    post = record.post if record is not None else None
    if post is None or post.kind != "action":
        raise _refuse("not_an_action", "only the open turn's action post is labelled")
    if post.private:
        raise _refuse("private_action", "an action that may carry a private message is not adjudicated at the table")
    controller = scene.controller(post.actor)
    if post.actor not in scene.cast or controller in (None, DIRECTOR):
        raise _refuse("not_a_player_action", "only a present player character's action is labelled")
    if principal != controller and principal not in runner.authority.owners:
        raise _refuse("not_controller", "the acting character's player, or the owner, asks for its labels")
    if record.framed is not None:
        raise _refuse("already_framed", "this action is framed already")
    number = len(record.labels) + 1
    if number > MAX_LABELS:
        raise _refuse("too_many_labels", f"an action is labelled at most {MAX_LABELS} times")
    # 040: the obstacle the press names must be open to an attempt now; read before any call is planned
    state = None if obstacle is None else open_obstacle(runner, chat.campaign_id, obstacle)
    # an action whose attempt the obstacle ledger links (and no later one superseded) is labelled against that
    # obstacle only: framed plainly, it would leave the obstacle diverged
    refuse_live_link(runner, record, obstacle)
    # and keeps the approach its link took: the labeller is offered only that one
    fixed = None if state is None else fixed_attempt(state, record)
    players = [actor.instance_id for actor in scene.players]
    try:
        plan = plan_turn(chat.campaign_id, record.label_turn(number),
                         present_actor_ids=[actor.instance_id for actor in scene.cast],
                         player_controlled_ids=players,
                         optional_requests=[OptionalRequest(InferenceStage.LABELLING, None, PURPOSE)],
                         policy=runner.policy)
    except InferencePlanError:
        raise _refuse("labelling_unplannable", "the labelling call cannot be planned") from None
    if not plan.calls:
        raise _refuse("labelling_declined", "the inference policy allows no labelling call")
    (call,) = plan.calls

    ledger, as_of = runner._knowledge(chat)
    # the character's own window: narration reaches the table, not what the character perceived
    _, bare = outcome_lines(runner, scene.records, scene.names)  # a settled action's result, where it was
    heard = heard_for(scene.records, post.actor, scene.names, before=scene.imported, narration=False,
                      outcomes=bare)
    try:  # 031: packed for the character's present controller, never as an NPC's (only what that player may see)
        built = player_context(context.view, chat, ledger, post.actor, controller=controller, controls=scene.controls,
                               as_of=as_of, budget=runner.budget, present=_perceived(scene, post.actor),
                               scene_text=render(heard))
    except ContextError as error:
        if error.reason != "uncontrolled_audience":
            raise
        raise _refuse("not_a_player_action", "only a present player character's action is labelled") from None
    if "knowledge_unprojected" in built.pack.notes:  # fail closed: never a labelling over a memory-less pack
        raise _refuse("knowledge_unprojected", "the acting character's knowledge cannot be projected for its player")
    # every believed proposition of the pack is its controller's own already; checked again, fail closed
    offered = facts(built.pack.items, own=own_propositions(ledger.knowledge, controller, scene.controls, post.actor,
                                                           as_of))
    messages = label_messages(scene.names.get(post.actor) or post.actor.instance_id, post.text, offered,
                              render(heard[-12:]), obstacle=None if state is None else obstacle_offer(state, fixed))

    planned = CallRecord(call.call_id.phase, call.stage, call.call_id.actor_id, call.call_id.ordinal, "planned")
    stored = scene.turns.last
    current = stored.record
    for previous in current.labels:
        if previous.state in ("running", "proposed"):
            current = current.with_label(replace(previous, state="superseded"))
    with runner.store.lock:  # one campaign sequence, after every one used, taken and written in one step
        sequence = next_sequence(runner, chat.campaign_id, as_of)
        if state is not None:
            check_moment(state, sequence)  # nothing is written, nor called, for an attempt out of the obstacle's order
        running = Label(number, "running", sequence, calls=(planned,), by=principal,
                        against=None if state is None else Against(state.obstacle.obstacle_id))
        # a call starts only on a turn still on the chat's line with no pause or sync conflict standing (028, 016)
        stored = runner._write(current.with_label(running), stored, unpaused=True)

    meter = TurnMeter(TurnUsageLedger(plan))
    try:
        with span("provider", calls=1):
            reply = runner.labeller.label(meter, call, messages)
    except Exception as error:  # an approvals callback or adapter bug: the usage so far is still recorded
        status, reason, decoded = "failed", type(error).__name__, None
    else:
        status, reason, decoded = reply.status, reply.reason, reply.decoded
    record_calls(meter)  # 048: one sample per booked attempt, when the call is traced
    finished = replace(running, state="refused", reason=reason or status)
    if status == "decoded":
        try:
            if state is None:
                factors, stakes = parse_labels(decoded, offered)
                against = None
            else:
                against, factors, stakes = _against(state, record, running, decoded, offered, fixed)
        except LabelRefused as refused:
            status, reason = "rejected", refused.reason
            finished = replace(running, state="refused", reason=refused.reason)
        else:
            status = "accepted"
            finished = replace(running, state="proposed", factors=factors, stakes=stakes,
                               against=against or running.against)
    elif status == "unparseable":  # the model answered, but not with one JSON value
        status = "rejected"
    attempts = tuple(attempt for attempt in meter.ledger.attempts if attempt.call_id == call.call_id)
    finished = replace(finished, calls=(CallRecord(planned.phase, planned.stage, planned.actor_id, planned.ordinal,
                                                   status, reason, attempts),))
    return _land(runner, chat, stored, finished, principal)


def _against(state, record: TurnRecord, running: Label, decoded, offered, fixed: Attempt | None = None):
    """A reply against an obstacle: the approach it chose and the frame's labels, the author's part added by rule.
    The frame is checked against the obstacle as read (040's ``check_attempt``), so a labelling that could never be
    framed is refused here, with the obstacle's reason; confirming checks it again as the obstacle stands then.
    With ``fixed`` (the attempt already linked), a reply taking any other approach is ``approach_changed``."""
    chosen, own, stakes = parse_attempt(decoded, offered, [a.approach_id for a in state.obstacle.approaches])
    against, factors, stakes, approach, listed = attempt_labels(state, chosen, running.sequence, record.post.text,
                                                                own, stakes)
    if fixed is not None:
        # the approach is the linked one: the same suggestion, or the same novel action risking as many stakes
        # (s1..sN by id); the frame is checked with the linked approach itself
        if (approach.approach_id, listed) != (fixed.approach.approach_id, fixed.listed) or (
                not listed and approach.text != fixed.approach.text):
            raise LabelRefused("approach_changed", "this action's attempt is linked with another approach; it keeps it")
        if approach.risks != fixed.approach.risks:
            raise LabelRefused("approach_changed", f"this action's novel attempt risks exactly "
                                                   f"{len(fixed.approach.risks)} stakes; the reply lists them")
        approach = fixed.approach
    try:
        frame, _ = frame_of(record, replace(running, state="proposed", factors=factors, stakes=stakes,
                                            against=against))
        check_attempt(state.obstacle, state.facts, Attempt(record.turn_id, approach, listed), frame, state.after)
    except (ObstacleError, AdjudicationError) as error:
        raise LabelRefused(error.reason, str(error)[:200]) from None
    return against, factors, stakes


def _proposed(runner, record: TurnRecord) -> bool:
    """Whether the label handle may propose ``record``'s canonical payload at its address."""
    address = "/".join(address_parts(record.ref))
    payload = json.dumps(turn_to_payload(record), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return runner.label_handle.authorize(Permission.PROPOSE, address, payload).allowed


def _land(runner, chat: ChatRef, stored: StoredTurn, label: Label, principal: str) -> Label:
    """Write the finished labelling over its running one, re-reading the turn if it changed meanwhile. A proposed
    labelling lands only while 016 still presents narration to the table as it stands when it commits (read
    again, under the store lock): a boundary set, or a player seated, while the call was in flight lands it
    ``refused`` with why, its usage kept."""
    def fence() -> None:
        if label.state == "proposed":
            runner._check_now(chat, principal)

    published = next(each for each in stored.record.labels if each.label == label.label)  # as written, running
    for _ in range(WRITE_RETRIES):
        existing = next((each for each in stored.record.labels if each.label == label.label), None)
        if existing is None and len(stored.record.labels) + 1 == label.label:
            # its turn now holds another version (a sync conflict resolved to the peer's meanwhile) with no
            # labelling of this number: that version stands as chosen, and only this call's usage joins it,
            # under the number it was billed under (``<turn_id>.l<n>``)
            label = replace(label, state="superseded", factors=(), stakes=())
        elif existing is None or existing.state not in ("running", "superseded") or \
                replace(published, state=existing.state) != existing:
            break  # another version's own labelling of this number (or a gap before it): nowhere to record it
        elif existing.state != "running":  # a newer labelling superseded this one: keep the usage only
            label = replace(label, state="superseded", factors=(), stakes=())
        record = stored.record.with_label(label)
        if label.state == "proposed" and not _proposed(runner, record):
            label = replace(label, state="refused", factors=(), stakes=(), reason="not_authorized")
            record = stored.record.with_label(label)
        try:  # like a finished take: a labelling in flight always lands, so its call's usage is never lost (028)
            try:
                runner._write(record, stored, landing=True, before_commit=fence)
            except SceneRefused as error:
                if error.reason not in (HELD, UNREADABLE):
                    raise
                label = replace(label, state="refused", factors=(), stakes=(), reason=error.reason)
                runner._write(stored.record.with_label(label), stored, landing=True)
            return label
        except SceneRefused as error:
            if error.reason != "turn_changed":
                raise
        current = runner.scenes.turns(chat)  # its turn may have been set aside by a fork choice meanwhile
        found = next((s for s in (*current.turns, *current.set_aside) if s.record.ref == stored.record.ref), None)
        if found is None:
            break
        stored = found
    raise _refuse("turn_changed", "another call changed this turn; the labelling could not be saved")


__all__ = ["PURPOSE", "label_action"]
