"""JSON views of a campaign's situation for the campaign hub (113): pure functions of domain values.

Every view says what kind of thing it shows. A belief or an observation is always an actor's (``kind``
``belief``/``observation``, with its ``holder``); world truth is only an accepted 021 event or a 017
relation, and only in a director view. Nothing here reads the store.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from grimoire.core.identity import InstanceRef

Names = Callable[[InstanceRef | None], str | None]


def ref(value: object) -> str | None:
    return None if value is None else str(value)


def moment_view(moment) -> dict:  # type: ignore[no-untyped-def]
    return {"sequence": moment.sequence, "scene": moment.scene_id, "turn": moment.turn_id}


def actor_ref(actor: InstanceRef | None, names: Names) -> dict | None:
    return None if actor is None else {"ref": str(actor), "name": names(actor)}


# -- adjudication (029) ---------------------------------------------------------------------------------

def factor_view(factor) -> dict:  # type: ignore[no-untyped-def]
    return {"id": factor.factor_id, "bearing": factor.bearing, "text": factor.text}


def stake_view(stake) -> dict:  # type: ignore[no-untyped-def]
    return {"id": stake.stake_id, "text": stake.text}


def label_view(label) -> dict:  # type: ignore[no-untyped-def]
    against = label.against
    return {"label": label.label, "state": label.state, "reason": label.reason,
            "factors": [factor_view(f) for f in label.factors], "stakes": [stake_view(s) for s in label.stakes],
            "against": None if against is None else {"obstacle": against.obstacle_id,
                                                     "approach": against.approach_id, "novel": against.novel}}


def framed_view(framed, names: Names) -> dict | None:  # type: ignore[no-untyped-def]
    """The open action as 029 holds it, what results it allows, and the fingerprint a consent names."""
    if framed is None:
        return None
    frame, assessment = framed.frame, framed.assessment
    return {"attempt": frame.attempt_id, "actor": actor_ref(frame.actor, names), "intent": frame.intent,
            "moment": moment_view(frame.moment), "factors": [factor_view(f) for f in frame.factors],
            "stakes": [stake_view(s) for s in frame.stakes], "classification": assessment.classification,
            "allowed": list(assessment.allowed), "fingerprint": framed.fingerprint}


def outcome_view(outcome, names: Names) -> dict | None:  # type: ignore[no-untyped-def]
    if outcome is None:
        return None
    decision = outcome.decision
    return {"attempt": outcome.frame.attempt_id, "actor": actor_ref(outcome.frame.actor, names),
            "classification": outcome.classification, "result": decision.result, "basis": decision.basis,
            "cites": sorted(decision.cites), "consequences": sorted(decision.consequences),
            "decided_by": outcome.decided_by, "consented_by": outcome.consented_by}


# -- effects (021, through 035) -------------------------------------------------------------------------

def op_view(op, names: Names) -> dict:  # type: ignore[no-untyped-def]
    return {"kind": op.kind, "predicate": op.predicate, "subject": actor_ref(op.subject, names),
            "object": actor_ref(op.object, names), "agent": actor_ref(op.agent, names), "backed_by": op.backed_by}


def line_view(acted, names: Names) -> dict:  # type: ignore[no-untyped-def]
    line = acted.line
    return {"kind": line.kind, "holder": actor_ref(line.holder, names), "item": line.item, "amount": line.amount,
            "seen": line.seen, "to": line.to, "target": actor_ref(line.target, names),
            "agent": actor_ref(acted.agent, names), "backed_by": acted.backed_by}


def effect_view(effect, names: Names) -> dict:  # type: ignore[no-untyped-def]
    """One turn effect and its keys. ``endorsers`` says, for each player whose character acts, whether that
    player's own key is given; ``approved_by`` is the director's key. ``fingerprint`` is what a player endorses."""
    proposal = effect.proposal
    given = {(item.contribution_id, item.principal) for item in effect.endorsements}
    holdings = proposal.holdings
    return {"number": effect.number, "state": effect.state, "reason": effect.reason,
            "summary": proposal.summary, "proposed_by": proposal.proposed_by,
            "sequence": proposal.moment.sequence,
            "operations": [op_view(op, names) for op in proposal.operations],
            "holdings": None if holdings is None else {"lines": [line_view(acted, names) for acted in holdings.lines],
                                                        "note": holdings.note},
            "waivers": [{"change": item.change, "reason": item.reason} for item in effect.waivers],
            "endorsers": [{"contribution": cid, "principal": who, "given": (cid, who) in given}
                          for cid, who in effect.endorsers],
            "approved_by": effect.approved_by, "event": effect.event_id, "fingerprint": effect.fingerprint,
            "accepted_on": effect.accepted_on}


def turn_view(record, names: Names, player: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """A turn's adjudication and effects only: its transcript is the chat page's (111). With ``player`` (the
    table's view), only the effects that player holds a key on: an effect is world truth once accepted, and one
    no character of theirs acts in is the director's alone."""
    post = record.post
    effects = [effect for effect in record.effects
               if player is None or any(who == player for _, who in effect.endorsers)]
    return {"turn": record.turn_id, "number": record.number, "open": record.open,
            "kept": record.kept is not None, "passed": record.passed_by is not None,
            "post": None if post is None else {"actor": actor_ref(post.actor, names), "kind": post.kind,
                                               "withheld": post.withheld, "channel": post.channel},
            "labels": [label_view(label) for label in record.labels],
            "effects": [effect_view(effect, names) for effect in effects]}


def event_view(event, names: Names) -> dict:  # type: ignore[no-untyped-def]
    """An accepted event: world truth from its moment on (director view only)."""
    return {"kind": "accepted_event", "event": event.ref.event_id, "chat": str(event.chat),
            "moment": moment_view(event.moment), "summary": event.summary, "proposed_by": event.proposed_by,
            "operations": [{"kind": item.op.kind, "predicate": item.op.predicate,
                            "subject": actor_ref(item.op.subject, names), "object": actor_ref(item.op.object, names)}
                           for item in event.operations],
            "holdings": None if event.holdings is None else
            [{**line_view(acted, names), "domain": domain}
             for acted, domain in zip(event.holdings.holdings.lines, event.holdings.domains)],
            "committed_by": event.committed_by, "accepted_at": event.accepted_at,
            "revoked": event.revocation is not None}


def relation_view(entry, names: Names) -> dict:  # type: ignore[no-untyped-def]
    relation = entry.relation if hasattr(entry, "relation") else entry
    return {"kind": "relation", "relation": relation.ref.relation_id, "predicate": relation.predicate,
            "subject": actor_ref(relation.subject, names), "object": actor_ref(relation.object, names),
            "since": relation.since.sequence, "provenance": relation.provenance,
            "ended": None if relation.ending is None else relation.ending.until.sequence}


# -- obstacles (040) --------------------------------------------------------------------------------------

def obstacle_view(state, names: Names) -> dict:  # type: ignore[no-untyped-def]
    """An obstacle as it stands. 040 keeps no hidden facts: what a frame would show, this shows."""
    obstacle = state.obstacle
    return {"obstacle": obstacle.obstacle_id, "goal": obstacle.goal, "status": state.status,
            "problem": state.problem, "revision": state.revision, "overcome_by": state.overcome_by,
            "actors": [actor_ref(actor, names) for actor in sorted(obstacle.actors, key=str)],
            "facts": [{"id": fact.fact_id, "kind": fact.kind, "text": fact.text,
                       "bearings": {approach: bearing for approach, bearing in fact.bearings}}
                      for fact in state.facts],
            "approaches": [{"id": approach.approach_id, "text": approach.text, "risks": sorted(approach.risks),
                            "costs": [{"id": cost.cost_id, "text": cost.text} for cost in approach.costs]}
                           for approach in obstacle.approaches],
            "stakes": [{"id": stake.stake_id, "text": stake.text} for stake in obstacle.stakes],
            "attempts": [{"attempt": item.attempt.attempt_id, "approach": item.attempt.approach.approach_id,
                          "listed": item.attempt.listed, "status": item.status, "result": item.result,
                          "consequences": [{"kind": c.kind, "id": c.consequence_id, "text": c.text}
                                           for c in item.consequences]}
                         for item in state.attempts],
            "due": [{"kind": c.kind, "id": c.consequence_id, "text": c.text,
                     "changes": [f"{c.kind}.{c.consequence_id}.{n}" for n in range(1, len(c.changes) + 1)]}
                    for c in state.due()]}


# -- knowledge (012/020): always an actor's, never world truth --------------------------------------------

def knowledge_view(knowledge, names: Names) -> dict:  # type: ignore[no-untyped-def]
    """One actor's perspective: their own observations and beliefs. None of it is world truth."""
    holder = actor_ref(knowledge.actor, names)

    def belief(item) -> dict:  # type: ignore[no-untyped-def]
        return {"kind": "belief", "holder": holder, "belief": item.belief_id, "proposition": item.proposition_id,
                "text": item.text, "stance": item.stance, "moment": moment_view(item.moment),
                "evidence": list(item.evidence_ids), "supersedes": item.supersedes}

    return {"actor": holder, "as_of": knowledge.as_of,
            "observations": [{"kind": "observation", "holder": holder, "evidence": item.evidence_id,
                              "type": item.kind, "channel": item.channel, "content": item.content,
                              "moment": moment_view(item.moment),
                              "attributed_to": actor_ref(item.attributed_to, names)}
                             for item in knowledge.evidence],
            "sent": [{"kind": "sent_report", "holder": holder, "evidence": item.evidence_id,
                      "recipient": actor_ref(item.recipient, names), "text": item.text, "channel": item.channel,
                      "moment": moment_view(item.sent_at)} for item in knowledge.sent],
            "beliefs": [belief(item) for item in knowledge.current],
            "superseded": [belief(item) for item in knowledge.history],
            "inherited": None if knowledge.inherited is None else [belief(item) for item in knowledge.inherited],
            "inherited_superseded": None if knowledge.inherited_history is None else
            [belief(item) for item in knowledge.inherited_history]}


# -- stage (041), party (033), holdings (023), items (034) ------------------------------------------------

def stage_view(stage, names: Names) -> dict | None:  # type: ignore[no-untyped-def]
    if stage is None:
        return None
    return {"scene": stage.scene_id, "as_of": stage.as_of,
            "areas": [{"id": area.area_id, "label": area.label} for area in stage.areas],
            "ways": [{"id": way.way_id, "sides": list(way.sides), "state": way.state} for way in stage.ways],
            "placements": [{"who": actor_ref(item.who, names), "area": item.area,
                            "holder": actor_ref(item.holder, names), "basis": item.basis}
                           for item in stage.placements],
            "absences": [{"actor": actor_ref(item.actor, names), "via": item.via, "basis": item.basis}
                         for item in stage.absences]}


def holdings_view(view) -> dict:  # type: ignore[no-untyped-def]
    return {"counts": dict(sorted(view.counts().items()))}


def items_view(items: Iterable, equipped: Mapping[str, Iterable]) -> dict:
    def one(item) -> dict:  # type: ignore[no-untyped-def]
        return {"item": str(item.item), "name": item.name}

    return {"held": [one(item) for item in items],
            "equipped": {slot: [one(item) for item in found] for slot, found in sorted(equipped.items())}}


__all__ = [
    "Names", "actor_ref", "effect_view", "event_view", "factor_view", "framed_view", "holdings_view", "items_view",
    "knowledge_view", "label_view", "moment_view", "obstacle_view", "outcome_view", "relation_view", "stage_view",
    "turn_view",
]
