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

from grimoire.contextstore import actor_context
from grimoire.core import DIRECTOR
from grimoire.core.identity import ChatRef
from grimoire.core.inference import InferencePlanError, InferenceStage, OptionalRequest, TurnUsageLedger, plan_turn
from grimoire.core.authority import AuthorityError
from grimoire.core.knowledge import KnowledgeError
from grimoire.core.perspective import player_knowledge
from grimoire.labelling import LabelRefused, facts, label_messages, parse_labels
from grimoire.play.scene import WRITE_RETRIES, _refuse
from grimoire.providers.meter import TurnMeter
from grimoire.scene import MAX_LABELS, CallRecord, Label, SceneRefused, TurnRecord, heard_for, render
from grimoire.scene.codec import turn_to_payload
from grimoire.scenestore import StoredTurn
from grimoire.security import Permission
from grimoire.store.layout import address_parts

#: The planned call's purpose: framing, never a reply.
PURPOSE = "frame a player action"


def own_propositions(knowledge, controller: str, controls, actor, as_of: int) -> frozenset[str]:
    """The propositions ``actor`` believes on ``controller``'s own authority (020's ``player_knowledge``): a
    belief authored by anyone else (an NPC-era conclusion the player inherited) is not the player's. Control
    that is ambiguous, or knowledge that cannot be projected, offers none (fail closed)."""
    try:
        projected = player_knowledge(knowledge, controller, controls, as_of=as_of)
    except (AuthorityError, KnowledgeError):
        return frozenset()
    return frozenset(belief.proposition_id for known in projected.actors
                     if known.actor.instance_id == actor.instance_id
                     for belief in known.current if belief.stance == "believed")


def label_action(runner, chat: ChatRef, by) -> Label:
    """See ``SceneRunner.label``."""
    context = runner._context(chat)
    scene = context.scene
    principal = runner._principal(scene, by)
    runner._ready(scene)
    if runner.labeller is None or runner.label_handle is None:
        raise _refuse("no_labeller", "no labeller is configured")
    scope = context.captured.scope
    if scope is not None and scope.session_zero is not None:  # as for generation: its boundaries are unread
        raise _refuse("session_zero_unreadable", "this slice cannot read a session zero yet; labelling waits")
    record = scene.pending
    post = record.post if record is not None else None
    if post is None or post.kind != "action":
        raise _refuse("not_an_action", "only the open turn's action post is labelled")
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
    heard = heard_for(scene.records, post.actor, scene.names, before=scene.imported, narration=False)
    built = actor_context(context.view, chat, ledger, post.actor, as_of=as_of, budget=runner.budget,
                          present=scene.cast, scene_text=render(heard))
    offered = facts(built.pack.items, own=own_propositions(ledger.knowledge, controller, scene.controls, post.actor,
                                                           as_of))
    messages = label_messages(scene.names.get(post.actor) or post.actor.instance_id, post.text, offered,
                              render(heard[-12:]))

    planned = CallRecord(call.call_id.phase, call.stage, call.call_id.actor_id, call.call_id.ordinal, "planned")
    running = Label(number, "running", as_of + 1, calls=(planned,), by=principal)
    stored = scene.turns.last
    current = stored.record
    for previous in current.labels:
        if previous.state in ("running", "proposed"):
            current = current.with_label(replace(previous, state="superseded"))
    stored = runner.scenes.write(current.with_label(running), stored)

    meter = TurnMeter(TurnUsageLedger(plan))
    try:
        reply = runner.labeller.label(meter, call, messages)
    except Exception as error:  # an approvals callback or adapter bug: the usage so far is still recorded
        status, reason, decoded = "failed", type(error).__name__, None
    else:
        status, reason, decoded = reply.status, reply.reason, reply.decoded
    finished = replace(running, state="refused", reason=reason or status)
    if status == "decoded":
        try:
            factors, stakes = parse_labels(decoded, offered)
        except LabelRefused as refused:
            status, reason = "rejected", refused.reason
            finished = replace(running, state="refused", reason=refused.reason)
        else:
            status = "accepted"
            finished = replace(running, state="proposed", factors=factors, stakes=stakes)
    elif status == "unparseable":  # the model answered, but not with one JSON value
        status = "rejected"
    attempts = tuple(attempt for attempt in meter.ledger.attempts if attempt.call_id == call.call_id)
    finished = replace(finished, calls=(CallRecord(planned.phase, planned.stage, planned.actor_id, planned.ordinal,
                                                   status, reason, attempts),))
    return _land(runner, chat, stored, finished)


def _proposed(runner, record: TurnRecord) -> bool:
    """Whether the label handle may propose ``record``'s canonical payload at its address."""
    address = "/".join(address_parts(record.ref))
    payload = json.dumps(turn_to_payload(record), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return runner.label_handle.authorize(Permission.PROPOSE, address, payload).allowed


def _land(runner, chat: ChatRef, stored: StoredTurn, label: Label) -> Label:
    """Write the finished labelling over its running one, re-reading the turn if it changed meanwhile."""
    for _ in range(WRITE_RETRIES):
        existing = next((each for each in stored.record.labels if each.label == label.label), None)
        if existing is None:
            break
        if existing.state != "running":  # a newer labelling superseded this one: keep the usage only
            label = replace(label, state="superseded", factors=(), stakes=())
        record = stored.record.with_label(label)
        if label.state == "proposed" and not _proposed(runner, record):
            label = replace(label, state="refused", factors=(), stakes=(), reason="not_authorized")
            record = stored.record.with_label(label)
        try:
            runner.scenes.write(record, stored)
            return label
        except SceneRefused as error:
            if error.reason != "turn_changed":
                raise
        found = next((s for s in runner.scenes.turns(chat).turns if s.record.ref == stored.record.ref), None)
        if found is None:
            break
        stored = found
    raise _refuse("turn_changed", "another call changed this turn; the labelling could not be saved")


__all__ = ["PURPOSE", "label_action"]
