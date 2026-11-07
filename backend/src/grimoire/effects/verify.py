"""Does the event ledger agree with the relation state? (Sprint 021) Pure checks, reported, never repaired.

Accepted events say what was accepted; 017 relation files are the state. ``verify_receipts``
checks one event's receipts against the graph; ``orphans`` finds relation changes carrying a 021
token that no event records; ``ledger_problems`` checks the event stack itself (ordinals and
sequences); ``wrapup_problems`` and ``check_citations`` hold wrap-ups to the accepted events of
their scene. ``as_knowledge_event`` bridges an accepted event to a 012 ``Event`` for 020, and
``bridge_problems`` checks what 020 stored under accepted ids.

Holdings (023) are checked the same way: ``holdings_failures`` holds an event's holdings receipt (each
holder's logged seq) to the holders' logs, ``holdings_orphans`` finds holder-log entries whose exact
021 cause names an event that does not record them, and ``holdings_partials`` reports a revocation that
left its holdings undo to a human.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import ChatRef, InstanceRef, RelationRef
from grimoire.core.knowledge import Event, Moment
from grimoire.graph import GraphError, GraphIndex, Relation
from grimoire.holdings import Holdings, Operation

from .model import NEVER_APPLIED, AcceptedEvent, EffectError, SceneWrapUp, accepted_token, parse_token, revoked_token
from .parts import inverse_op_id
from .plan import records_fact


@dataclass(frozen=True, slots=True)
class EffectProblem:
    """``code`` names what disagrees; ids only, never record text."""

    code: str
    event_id: str | None = None
    op: int | None = None
    detail: str = ""
    relation_id: str | None = None
    wrapup_id: str | None = None

    def sort_key(self) -> tuple:
        return (self.code, self.event_id or "", -1 if self.op is None else self.op, self.relation_id or "",
                self.wrapup_id or "", self.detail)


def _relation(index: GraphIndex, ref: RelationRef) -> Relation | str:
    try:
        entry = index.relation(ref.relation_id, requested_by=DIRECTOR)
    except GraphError:
        return "missing"
    return entry.relation if isinstance(entry.relation, Relation) else "undecodable"


def receipt_failure(event: AcceptedEvent, op_index: int, index: GraphIndex) -> str | None:
    """Why receipt ``op_index`` of ``event`` does not match the graph, or ``None`` when it does."""
    item, receipt = event.operations[op_index], event.receipts[op_index]
    relation = _relation(index, receipt.relation)
    if isinstance(relation, str):
        return relation
    if not records_fact(relation, item.op):
        return "different fact"
    sequence, accepted = event.moment.sequence, accepted_token(event.event_id)
    if event.active:
        if relation.retraction is not None:
            return "retracted"
        if item.op.kind == "begin":
            if relation.since.sequence != sequence or relation.provenance != accepted:
                return "not begun by this event"
            return None
        ending = relation.ending
        if ending is None or ending.until.sequence != sequence or ending.provenance != accepted:
            return "not ended by this event"
        return None
    # A revoked event is checked by its permanent marks only: the retraction it wrote and the reopening it
    # recorded. Revoking an earlier event (latest first) may later retract a relation it reopened.
    revoked = revoked_token(event.event_id)
    if item.op.kind == "begin":
        if relation.retraction is None or relation.retraction.provenance != revoked:
            return "not retracted by this revocation"
        return None
    if not any(r.provenance == revoked and r.ending.provenance == accepted and r.ending.until.sequence == sequence
               for r in relation.reopenings):
        return "not reopened by this revocation"
    return None


def verify_receipts(event: AcceptedEvent, index: GraphIndex) -> tuple[EffectProblem, ...]:
    skipped = set() if event.revocation is None else {item.op for item in event.revocation.skipped}
    found = []
    for number in range(len(event.operations)):
        if number in skipped:
            continue
        why = receipt_failure(event, number, index)
        if why is not None:
            found.append(EffectProblem("receipt_mismatch", event.event_id, number, why,
                                       event.receipts[number].relation.relation_id))
    return tuple(found)


def edited_effects(events: Iterable[AcceptedEvent], index: GraphIndex) -> tuple[EffectProblem, ...]:
    """``edited_since``: a relation an active event began was ended by something other than a later active
    accepted event (a direct 017 edit). Nothing is wrong with the receipt itself, but undoing the event will
    be refused (``has_dependents``) until the edit is resolved, so it is surfaced now."""
    events = list(events)
    active = {e.event_id for e in events if e.active}
    found = []
    for event in events:
        if not event.active:
            continue
        for number, item in enumerate(event.operations):
            if item.op.kind != "begin":
                continue
            relation = _relation(index, event.receipts[number].relation)
            if isinstance(relation, str) or relation.ending is None:
                continue
            ender = parse_token(relation.ending.provenance)
            if ender is None or ender[0] != "accepted" or ender[1] not in active or ender[1] == event.event_id:
                found.append(EffectProblem("edited_since", event.event_id, number, "ended outside 021",
                                           relation.ref.relation_id))
    return tuple(found)


def partial_revocations(events: Iterable[AcceptedEvent], index: GraphIndex) -> tuple[EffectProblem, ...]:
    """``partial_revocation``: a revoked event's skipped operation whose change is **still in force**: a
    relation it began that is still unretracted, or an ending it made that is still there. The event is
    then only partly undone (half a transfer may remain, an item held twice). Once a human undoes that
    change by hand (retracts the relation, reopens the ending), it is no longer reported."""
    found = []
    for event in events:
        if event.revocation is None:
            continue
        accepted = accepted_token(event.event_id)
        for item in event.revocation.skipped:
            relation = _relation(index, event.receipts[item.op].relation)
            if isinstance(relation, str) or relation.retraction is not None:
                continue
            kind = event.operations[item.op].op.kind
            in_force = (relation.provenance == accepted and relation.since.sequence == event.moment.sequence
                        if kind == "begin" else
                        relation.ending is not None and relation.ending.provenance == accepted
                        and relation.ending.until.sequence == event.moment.sequence)
            if in_force:
                found.append(EffectProblem("partial_revocation", event.event_id, item.op, item.why,
                                           relation.ref.relation_id))
    return tuple(found)


def _tokens(relation: Relation) -> list[str]:
    tokens = [relation.provenance]
    if relation.ending is not None:
        tokens.append(relation.ending.provenance)
    if relation.retraction is not None:
        tokens.append(relation.retraction.provenance)
    for reopening in relation.reopenings:
        tokens += [reopening.provenance, reopening.ending.provenance]
    return tokens


def orphans(events: Iterable[AcceptedEvent], relations: Iterable[Relation]) -> tuple[EffectProblem, ...]:
    """Relation changes whose exact 021 token names an event that does not record them (``orphan_effect``).

    Free-text provenance is never interpreted; only ``effect:accepted/<id>`` and ``effect:revoked/<id>``.
    """
    by_id = {event.event_id: event for event in events}
    found = []
    for relation in relations:
        for token in _tokens(relation):
            parsed = parse_token(token)
            if parsed is None:
                continue
            state, event_id = parsed
            event = by_id.get(event_id)
            touched = event is not None and relation.ref in {r.relation for r in event.receipts}
            if not touched or (state == "revoked" and event.active):  # type: ignore[union-attr]
                found.append(EffectProblem("orphan_effect", event_id, detail=state,
                                           relation_id=relation.ref.relation_id))
    return tuple(sorted(set(found), key=EffectProblem.sort_key))


def predecessors(events: Iterable[AcceptedEvent]) -> dict[str, tuple[str, ...]]:
    """Each event's direct predecessors in the campaign's acceptance order (028).

    An event's ``after``, as it recorded it; for an event without one (accepted by an unidentified library or
    before 028's merge) the event without one just before it by ordinal, so such events form one line in
    ordinal order, as their library accepted them. Ids no event here has are kept: the caller decides.
    """
    events = list(events)
    found = {event.event_id: event.after for event in events if event.after is not None}
    previous: tuple[str, ...] = ()
    for event in sorted((e for e in events if e.after is None), key=lambda e: (e.ordinal, e.event_id)):
        found[event.event_id] = previous
        previous = (event.event_id,)
    return found  # type: ignore[return-value]


def accepted_frontier(events: Iterable[AcceptedEvent]) -> tuple[str, ...]:
    """The events no other event comes after, sorted: what a new acceptance records as its ``after`` (028).

    Revoked events count: a revocation changes what is in force, not the order things were accepted in.
    """
    events = list(events)
    named = {event_id for before in predecessors(events).values() for event_id in before}
    return tuple(sorted(event.event_id for event in events if event.event_id not in named))


def ledger_problems(events: Iterable[AcceptedEvent]) -> tuple[EffectProblem, ...]:
    """The event stack must be totally ordered: unique ordinals, and active sequences never decreasing."""
    events = list(events)
    found = []
    seen: dict[int, str] = {}
    for event in sorted(events, key=lambda e: (e.ordinal, e.event_id)):
        if event.ordinal in seen:
            found.append(EffectProblem("duplicate_ordinal", event.event_id, detail=seen[event.ordinal]))
        seen.setdefault(event.ordinal, event.event_id)
    active = sorted((e for e in events if e.active), key=lambda e: (e.ordinal, e.event_id))
    for earlier, later in zip(active, active[1:]):
        if later.moment.sequence < earlier.moment.sequence:
            found.append(EffectProblem("inconsistent_ledger", later.event_id, detail=earlier.event_id))
    return tuple(found)


def scene_events(events: Iterable[AcceptedEvent], chat: ChatRef, scene_id: str, through: Moment
                 ) -> list[AcceptedEvent]:
    """Active events of one chat scene up to ``through``: what its wrap-up must remember."""
    return [e for e in events if e.active and e.chat == chat and e.moment.scene_id == scene_id
            and e.moment.sequence <= through.sequence]


def check_citations(cites: Iterable[str], events: Iterable[AcceptedEvent], chat: ChatRef, scene_id: str,
                    through: Moment) -> None:
    """A wrap-up must cite exactly the scene's active events up to ``through``; refusals are ``EffectError``."""
    events = list(events)
    cites = set(cites)
    by_id = {e.event_id: e for e in events}
    required = {e.event_id for e in scene_events(events, chat, scene_id, through)}
    for cite in sorted(cites):
        event = by_id.get(cite)
        if event is not None and not event.active:
            raise EffectError("revoked_event", (cite,))
        if cite not in required:
            raise EffectError("unknown_event", (cite,))
    missing = sorted(required - cites)
    if missing:
        raise EffectError("uncited_event", missing)


def wrapup_problems(wrapups: Iterable[SceneWrapUp], events: Iterable[AcceptedEvent]) -> tuple[EffectProblem, ...]:
    """``stale_wrapup`` (cites a revoked or vanished event) and ``incomplete_wrapup`` (misses one)."""
    events = list(events)
    by_id = {e.event_id: e for e in events}
    found = []
    for wrapup in wrapups:
        wid = wrapup.ref.wrapup_id
        for cite in wrapup.cites:
            event = by_id.get(cite)
            if event is None or not event.active:
                found.append(EffectProblem("stale_wrapup", cite, wrapup_id=wid,
                                           detail="missing" if event is None else "revoked"))
        for event in scene_events(events, wrapup.chat, wrapup.scene_id, wrapup.through):
            if event.event_id not in wrapup.cites:
                found.append(EffectProblem("incomplete_wrapup", event.event_id, wrapup_id=wid))
    return tuple(sorted(found, key=EffectProblem.sort_key))


# -- holdings ---------------------------------------------------------------------------------------

def holdings_receipt(event: AcceptedEvent) -> tuple[Operation, dict[InstanceRef, int]] | None:
    """The 023 operation an event's holdings receipt names, and each holder's seq of it.

    An active event: the forward operation at ``seqs``. A revoked one whose undo was applied: the inverse
    at ``holdings_seqs``. ``None`` with no holdings, or a revocation that skipped the undo.
    """
    if event.holdings is None:
        return None
    part, op_id = event.holdings.holdings, event.holdings.op_id
    if event.revocation is None:
        return part.operation(op_id, accepted_token(event.event_id)), dict(event.holdings.seqs)
    if event.revocation.holdings_skipped is not None:
        return None
    return part.inverse(op_id, revoked_token(event.event_id)), dict(event.revocation.holdings_seqs)


def entry_failure(holdings: Holdings, seq: int, op: Operation, *, strict: bool = False) -> str | None:
    """Why ``holdings`` does not log ``op`` at ``seq``, or ``None`` when it does.

    The log is bounded: an entry evicted at or before ``seq`` cannot contradict the receipt, so it counts
    (``strict=False``, for duplicates and ``continuity``). Undoing needs the entry itself (``strict``), since
    the entry evicted at that seq may be another operation's.
    """
    for entry in holdings.log:
        if entry.seq == seq:
            return None if entry.operation == op else "different operation"
    if holdings.seq < seq:
        return "holder restored"
    if holdings.evicted_through >= seq:
        return "evicted" if strict else None
    return "not logged"


def holdings_failures(event: AcceptedEvent, holders: Mapping[InstanceRef, Holdings | str], *,
                      strict: bool = False, undoing: bool = False) -> tuple[tuple[InstanceRef, str, bool], ...]:
    """``(holder, why, unreadable)`` for every holder whose log disagrees with the event's holdings receipt.

    ``holders`` maps each holder to its decoded holdings, or to why its file cannot be read (``unreadable``
    is then true: damage is named, never called a mismatch). A holder left out of ``holders`` is unreadable.
    An active event fails ``undone`` where a holder logs its inverse (a revocation that landed on the holders but
    not on the event file), except while ``undoing``, when that inverse is the one to adopt. A revocation that
    skipped the undo as ``never applied`` fails ``applied since`` where a holder logs the forward change after all
    (a holder file restored from after the effect): it is in force, so it is a mismatch and holds the attempt.
    """
    if never_applied(event):
        assert event.holdings is not None
        return tuple((holder, held, True) if isinstance(held, str) else (holder, "applied since", False)
                     for holder in sorted(event.holdings.holdings.holders, key=str)
                     for held in (holders.get(holder, "not read"),)
                     if isinstance(held, str) or any(entry.op == event.holdings.op_id for entry in held.log))
    found = holdings_receipt(event)
    if found is None:
        return ()
    op, seqs = found
    failures = []
    for holder in sorted(seqs, key=str):
        held = holders.get(holder, "not read")
        if isinstance(held, str):
            failures.append((holder, held, True))
            continue
        why = entry_failure(held, seqs[holder], op, strict=strict)
        if why is None and event.active and not undoing and _undone(held, event.holdings.op_id):  # type: ignore[union-attr]
            why = "undone"  # its revocation's inverse landed without the event file: never "in force"
        if why is not None:
            failures.append((holder, why, False))
    return tuple(failures)


def never_applied(event: AcceptedEvent) -> bool:
    """A revoked event with holdings whose undo was skipped because no holder logged the forward change."""
    skipped = None if event.revocation is None else event.revocation.holdings_skipped
    return event.holdings is not None and skipped is not None and skipped.startswith(NEVER_APPLIED)


def attempt_refusal(event: AcceptedEvent, fingerprint: str) -> str | None:
    """Why ``event`` refuses a new acceptance of the effect ``fingerprint`` that shares one of its attempts, as
    far as the event itself says (021): ``attempt_reused`` while it is in force; ``attempt_revoked`` when it was
    revoked but the new one retries the very same effect or its revocation left its holdings change in force;
    ``None`` once it is undone and the new effect is a revision. ``undo_unconfirmed`` is the holder files' half."""
    if event.active:
        return "attempt_reused"
    assert event.revocation is not None
    if event.fingerprint == fingerprint or event.revocation.holdings_left:
        return "attempt_revoked"
    return None


def undo_unconfirmed(event: AcceptedEvent, holders: Mapping[InstanceRef, Holdings | str]) -> bool:
    """A revoked event whose undo the holder files do not confirm, so it still holds its attempts: an applied undo
    whose receipt some holder does not log (unless that holder was restored from before the forward change, so
    holds none of it either), or a ``never applied`` skip where a holder logs the forward change after all."""
    if event.holdings is None or event.revocation is None:
        return False
    failures = holdings_failures(event, holders)
    if never_applied(event):
        return bool(failures)
    if not event.revocation.holdings_seqs:
        return False
    forward = event.holdings.seqs
    return any(not (isinstance(held, Holdings) and held.seq < forward[holder])
               for holder, _, _ in failures for held in (holders.get(holder),))


def _undone(holdings: Holdings, op_id: str) -> bool:
    inverse = inverse_op_id(op_id)
    return any(entry.op == inverse for entry in holdings.log)


def holdings_problems(events: Iterable[AcceptedEvent], holders: Mapping[InstanceRef, Holdings | str]
                      ) -> tuple[EffectProblem, ...]:
    """``receipt_mismatch`` (``op=None``, ``detail="holdings: <holder>: <why>"``) or ``undecodable_holdings``."""
    found = []
    for event in events:
        for holder, why, unreadable in holdings_failures(event, holders):
            found.append(EffectProblem("undecodable_holdings" if unreadable else "receipt_mismatch",
                                       event.event_id, detail=f"holdings: {holder}: {why}"))
    return tuple(found)


def holdings_orphans(events: Iterable[AcceptedEvent], holders: Mapping[InstanceRef, Holdings]
                     ) -> tuple[EffectProblem, ...]:
    """Holder-log entries whose exact 021 cause names an event that does not record that operation.

    The event must touch that holder. ``effect:accepted/<id>``: it must hold holdings under that op id;
    ``effect:revoked/<id>``: it must be revoked and the entry be its inverse. Free-text causes are never interpreted.
    """
    by_id = {event.event_id: event for event in events}
    found = []
    for holder in sorted(holders, key=str):
        for entry in holders[holder].log:
            parsed = parse_token(entry.cause)
            if parsed is None:
                continue
            state, event_id = parsed
            event = by_id.get(event_id)
            recorded = event is not None and event.holdings is not None and (
                holder in event.holdings.holdings.holders) and (
                entry.op == event.holdings.op_id if state == "accepted"
                else not event.active and entry.op == inverse_op_id(event.holdings.op_id))
            if not recorded:
                found.append(EffectProblem("orphan_effect", event_id, detail=f"holdings {holder}"))
    return tuple(sorted(set(found), key=EffectProblem.sort_key))


def holdings_partials(events: Iterable[AcceptedEvent]) -> tuple[EffectProblem, ...]:
    """``partial_revocation`` (``op=None``) for a revocation that left its holdings undo to a human.

    021 cannot tell a human repair (a fresh 023 operation) from none, so it is reported until 030 can record it.
    """
    return tuple(EffectProblem("partial_revocation", event.event_id,
                               detail=f"holdings: {event.revocation.holdings_skipped}")
                 for event in events if event.revocation is not None and event.revocation.holdings_left)


def as_knowledge_event(event: AcceptedEvent) -> Event:
    """The 012 ``Event`` an accepted event corresponds to, for 020 to persist. Participants are description only.

    A revoked event is refused (``revoked_event``): an undone turn must never be stored as objective fact."""
    if not event.active:
        raise EffectError("revoked_event", (event.event_id,))
    people = {item.op.subject for item in event.operations} | {item.op.object for item in event.operations}
    people |= {item.op.agent for item in event.operations if item.op.agent is not None}
    if event.holdings is not None:  # holders and their agents, and every ability user, take part too
        people |= event.holdings.holdings.actors()
    people |= {attempt.actor for attempt in event.ability_attempts}
    return Event(event.ref.campaign_id, event.event_id, event.summary, event.moment, accepted_token(event.event_id),
                 tuple(sorted(people, key=str)))


def bridge_problems(events: Iterable[AcceptedEvent], stored: Mapping[str, Event | str]) -> tuple[EffectProblem, ...]:
    """Does a 020 event stored under an accepted event's id hold that event's bridge?

    ``stored`` maps a 020 event id to its decoded ``Event``, or to why its file cannot be read. 020's
    ``append`` refuses anything else under an accepted id, but sync can still deliver it, so this is the
    backstop: ``bridge_mismatch`` for an active event whose id holds a different (or unreadable) event, and
    ``revoked_but_bridged`` for a revoked event whose bridge is still stored (it outlives the revocation until
    030/054 own corrections). Ids only, never record text."""
    found = []
    for event in events:
        if event.event_id not in stored:
            continue
        held = stored[event.event_id]
        if not event.active:
            found.append(EffectProblem("revoked_but_bridged", event.event_id))
        elif held != as_knowledge_event(event):
            found.append(EffectProblem("bridge_mismatch", event.event_id,
                                       detail=held if isinstance(held, str) else "different event"))
    return tuple(sorted(found, key=EffectProblem.sort_key))
