"""Merging one campaign's 021 accepted events, wrap-ups and their 017 relations from two devices (028).

Every event is put in one order, the same for any pair of devices and whichever side holds it, and numbered
by it, the agreed events too (``causal_order``): never before an event it was accepted after (its ``after``),
and otherwise by ``merge_key`` (sequence, the accepting device's tag read from the id, the id's allocation
counter). Event ids are kept and nothing an endorsement covers changes: only ``ordinal``, ``holdings.seqs`` and
``revocation.holdings_seqs`` are rewritten, none of which the fingerprint covers (EFFECTS, Fingerprint).

Each new event is then replayed with 021's own planner against the merged relations as they stood just
before it, and must reproduce the receipts it recorded. Wrap-ups and relation files are merged three-way,
whole. Anything else stops the merge with a review value. Pure: no files, clocks or randomness.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from grimoire.core.identity import AcceptedRef, InstanceRef, RelationRef
from grimoire.core.json_value import freeze_object
from grimoire.effects import (
    AcceptedEvent, EffectError, EffectHoldings, EffectProposal, EndStep, accepted_schema, accepted_to_payload,
    inverse_op_id, parse_token, plan, predecessors, verify_receipts,
)
from grimoire.graph import GraphError, GraphIndex, Relation
from grimoire.holdings import Operation

from .model import BASE, Kept, Ledger, Output, ReviewItem, Rewrite, Version, choose, ranks

#: what a merge may rewrite in an event: outside its fingerprint, so every endorsement stays valid
REWRITTEN = ("ordinal", "holdings.seqs", "revocation.holdings_seqs")


def _core(event: AcceptedEvent) -> object:
    """The event's payload less what a merge rewrites: two versions that agree here are one event."""
    payload = accepted_to_payload(event)
    payload.pop("ordinal")
    if "holdings" in payload:
        payload["holdings"] = {k: v for k, v in payload["holdings"].items() if k != "seqs"}
    if "revocation" in payload:
        payload["revocation"] = {k: v for k, v in payload["revocation"].items() if k != "holdings_seqs"}
    return freeze_object(payload)


def same_event(a: Version, b: Version) -> bool:
    """One event: the same record but for what a merge renumbers, under the same envelope."""
    return a.meta == b.meta and _core(a.value) == _core(b.value)


def _same_file(a: Version, b: Version) -> bool:
    return a.etag == b.etag


@dataclass(frozen=True)
class EventOrder:
    """Which version of each event the merge keeps, and the merged order of those the base lacks."""

    chosen: Mapping[AcceptedRef, tuple[str, Version]]  # source (``base`` or a device) and its version
    order: tuple[AcceptedRef, ...]
    onesided: Mapping[AcceptedRef, str]  # events only one device holds, by device
    numbering: Mapping[str, int]  # every kept event's merged ordinal, by event id: its place in one global order
    items: tuple[ReviewItem, ...]

    @property
    def positions(self) -> dict[str, int]:
        return {ref.event_id: number for number, ref in enumerate(self.order, start=1)}

    def op_key(self, op: Operation) -> tuple:
        """Where an event's holdings operation goes among the other device's: its event's position."""
        found = parse_token(op.cause)
        if found is None:
            return (0, 0)
        return (self.positions.get(found[1], 0), 1 if found[0] == "revoked" else 0)


def order_events(base: Ledger, sides: Mapping[str, Ledger]) -> EventOrder:
    """Pick each event's version three-way and order the ones the base lacks; review what cannot merge."""
    rank = ranks(sides)
    devices = sorted(sides, key=rank.__getitem__)
    items: list[ReviewItem] = []
    chosen: dict[AcceptedRef, tuple[str, Version]] = {}
    onesided: dict[AcceptedRef, str] = {}
    refs = set(base.events).union(*(sides[device].events for device in devices))
    for ref in sorted(refs, key=str):
        agreed = base.events.get(ref)
        versions = {device: sides[device].events[ref] for device in devices if ref in sides[device].events}
        if agreed is not None:
            missing = [device for device in devices if device not in versions]
            items += [ReviewItem("deleted", str(ref), device) for device in missing]
            if missing:
                continue
            pick = choose(agreed, versions, rank, same_event)
            if pick is None:
                items.append(ReviewItem("both_changed", str(ref), "an agreed event changed on both devices"))
                continue
            chosen[ref] = (pick, agreed if pick == BASE else versions[pick])
            continue
        holding = list(versions)
        if len(holding) == 2 and not same_event(versions[holding[0]], versions[holding[1]]):
            items.append(ReviewItem("id_collision", str(ref), "one event id with two contents"))
            continue
        chosen[ref] = (holding[0], versions[holding[0]])
        if len(holding) == 1:
            onesided[ref] = holding[0]
    # Every event is renumbered, the agreed ones too: three paired devices merge pair by pair, each pair from its
    # own agreed state, so numbering only what a pair's base lacks would number the same events differently in
    # each pair, and the pairs would rewrite each other's ordinals forever (028).
    by_id = {ref.event_id: ref for ref in chosen}
    ordered, stuck = causal_order({event_id: chosen[ref][1].value for event_id, ref in by_id.items()})
    if stuck:
        items.append(ReviewItem("order_cycle", stuck[0], f"events accepted after each other: {', '.join(stuck)}"[:300]))
    everything = [by_id[event_id] for event_id in ordered + stuck]
    numbering = {ref.event_id: number for number, ref in enumerate(everything, start=1)}
    fresh = [ref for ref in everything if ref not in base.events]
    found = EventOrder(chosen, tuple(fresh), onesided, numbering, ())
    return replace(found, items=tuple(items + _policy(base, sides, found, devices)))


def _split(event_id: str) -> tuple[str, str]:
    """An id's allocation part and its device tag: ``e-<n>-<8 hex>`` gives ``("e-<n>", tag)``; an untagged
    id gives ``(id, "")``."""
    head, _, tail = event_id.rpartition("-")
    if "-" not in head or len(tail) != 8 or any(c not in "0123456789abcdef" for c in tail):
        return event_id, ""
    return head, tail


def causal_order(events: Mapping[str, AcceptedEvent]) -> tuple[list[str], list[str]]:
    """The merged order of ``events``: never an event before one it was accepted after, and otherwise the
    smallest ``merge_key`` first (028).

    Kahn's algorithm, always taking the ready event with the smallest key. Why that key and nothing else: an
    event's predecessors are all held wherever it is (a device held them when it accepted it, and a merge keeps
    every event), so what two devices hold is closed under predecessors, and on such a set this order is the
    same as on any larger one restricted to it. Every pair of devices therefore orders the same events the same
    way, whichever pair is merging and whichever side holds them. Predecessors nobody here holds are ignored.
    The second list is what a cycle left unplaced (only a hand edit makes one), by key.
    """
    before = {event_id: [p for p in after if p in events and p != event_id]
              for event_id, after in predecessors(events.values()).items()}
    waiting = {event_id: len(set(before[event_id])) for event_id in events}
    following: dict[str, list[str]] = {}
    for event_id, earlier in before.items():
        for previous in set(earlier):
            following.setdefault(previous, []).append(event_id)

    def key(event_id: str) -> tuple:
        return merge_key(event_id, events[event_id].moment.sequence)

    ready = [(key(event_id), event_id) for event_id, count in waiting.items() if count == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, event_id = heapq.heappop(ready)
        ordered.append(event_id)
        for later in following.get(event_id, ()):
            waiting[later] -= 1
            if waiting[later] == 0:
                heapq.heappush(ready, (key(later), later))
    placed = set(ordered)
    return ordered, sorted((event_id for event_id in events if event_id not in placed), key=key)


def merge_key(event_id: str, sequence: int) -> tuple:
    """How ``causal_order`` breaks a tie between events neither was accepted after: the same for every pair of
    devices and whichever side holds them.

    By sequence; then by the accepting device's tag, read from the id (untagged ids, from libraries never
    identified, first); then by the id's allocation counter (``e-<n>``), which keeps one device's own accept
    order at one sequence; then the id. Why nothing else: ordinals are rewritten by every merge and which
    devices are merging changes from pair to pair, so either would let two pairs order the same events
    differently and renumber them back and forth (028).
    """
    head, tag = _split(event_id)
    prefix, _, number = head.rpartition("-")
    counter = int(number) if prefix and number.isascii() and number.isdigit() else None
    return (sequence, tag, counter is None, counter or 0, event_id)


def _policy(base: Ledger, sides: Mapping[str, Ledger], found: EventOrder, devices: list[str]) -> list[ReviewItem]:
    """What both devices did offline that a merge must not settle alone (the owner's policy decides)."""
    items = []
    by_device: dict[str, list[AcceptedEvent]] = {device: [] for device in devices}
    for ref, device in found.onesided.items():
        by_device[device].append(found.chosen[ref][1].value)
    one, other = (by_device[device] for device in devices)
    chats = sorted({str(e.chat) for e in one} & {str(e.chat) for e in other})
    items += [ReviewItem("same_chat", chat, "new events on both devices") for chat in chats]
    for ref, (source, version) in found.chosen.items():
        agreed = base.events.get(ref)
        if agreed is None or not agreed.value.active or version.value.active:
            continue
        # only a device that still holds it in force: one that already has the revocation (synced from a third
        # device, say) did not extend past an agreed event the other took back
        others = [device for device in devices if device != source and sides[device].events[ref].value.active]
        if any(by_device[device] for device in others):
            items.append(ReviewItem("revoked_vs_extended", str(ref), f"revoked on {source}; the other device "
                                    "accepted new events"))
    prints = {e.fingerprint: e.event_id for e in other}
    for event in one:
        if event.fingerprint in prints:
            items.append(ReviewItem("same_effect", event.event_id, prints[event.fingerprint]))
    spent: dict[str, str] = {}
    for event in other:
        for attempt in event.attempts:
            spent.setdefault(attempt, event.event_id)
    for event in one:
        for attempt in sorted(event.attempts):
            if attempt in spent:
                items.append(ReviewItem("attempt_reused", event.event_id, spent[attempt]))
    return items


def rewrite(found: EventOrder, holder_seqs: Mapping[tuple[InstanceRef, str], int]) -> dict[AcceptedRef, AcceptedEvent]:
    """Every kept event as the merge stores it: new ordinals, and holdings receipts at the merged seqs."""
    merged = {}
    for ref, (_, version) in found.chosen.items():
        event = version.value
        changes: dict[str, object] = {"ordinal": found.numbering[ref.event_id]}
        if event.holdings is not None:
            op_id = event.holdings.op_id
            seqs = {holder: holder_seqs.get((holder, op_id), seq) for holder, seq in event.holdings.seqs.items()}
            changes["holdings"] = replace(event.holdings, seqs=seqs)
            revocation = event.revocation
            if revocation is not None and revocation.holdings_seqs:
                undo = inverse_op_id(op_id)
                changes["revocation"] = replace(revocation, holdings_seqs={
                    holder: holder_seqs.get((holder, undo), seq) for holder, seq in revocation.holdings_seqs.items()})
        merged[ref] = replace(event, **changes)
    return merged


def event_outputs(found: EventOrder, merged: Mapping[AcceptedRef, AcceptedEvent], base: Ledger,
                  sides: Mapping[str, Ledger]) -> dict[AcceptedRef, Output]:
    """An existing version where one already is the merged event (the agreed one first), else a rewrite."""
    rank = ranks(sides)
    devices = sorted(sides, key=rank.__getitem__)
    outputs: dict[AcceptedRef, Output] = {}
    for ref, event in merged.items():
        _, version = found.chosen[ref]
        payload = accepted_to_payload(event)
        wanted = freeze_object(payload)
        candidates = [(BASE, base.events.get(ref))] + [(device, sides[device].events.get(ref)) for device in devices]
        kept = next((Kept(v.etag, source) for source, v in candidates if v is not None and v.meta == version.meta
                     and freeze_object(accepted_to_payload(v.value)) == wanted), None)
        if kept is not None:
            outputs[ref] = kept
            continue
        agreed = base.events.get(ref)
        previous = agreed.etag if agreed is not None else next(
            v.etag for _, v in candidates[1:] if v is not None)
        outputs[ref] = Rewrite(accepted_schema(event), payload, version.owner, version.extra, previous)
    return outputs


def three_way(base: Mapping, sides: Mapping[str, Mapping], label: str
              ) -> tuple[dict, list[ReviewItem]]:
    """Wrap-ups and relation files merge whole: a file one device changed is that device's file.

    Two different changes of one file, or two different files written under one new id, stop the merge
    (a wrap-up's id is derived from its chat scene, so two devices wrapping one scene up meet here).
    """
    rank = ranks(sides)
    devices = sorted(sides, key=rank.__getitem__)
    outputs, items = {}, []
    for ref in sorted(set(base).union(*(sides[device] for device in devices)), key=str):
        agreed = base.get(ref)
        versions = {device: sides[device][ref] for device in devices if ref in sides[device]}
        missing = [device for device in devices if device not in versions]
        if agreed is not None and missing:
            items += [ReviewItem("deleted", str(ref), device) for device in missing]
            continue
        pick = choose(agreed, versions, rank, _same_file)
        if pick is None:
            reason = "both_changed" if agreed is not None or label == "wrapup" else "id_collision"
            items.append(ReviewItem(reason, str(ref), f"a {label} changed on both devices"))
            continue
        version = agreed if pick == BASE else versions[pick]
        outputs[ref] = Kept(version.etag, pick)  # type: ignore[union-attr]
    return outputs, items


# -- replay ----------------------------------------------------------------------------------------

def _before(relations: Iterable[Relation], later: frozenset[str], undone: frozenset[str]) -> list[Relation]:
    """The relations as they stood before the events ``later`` (and with the cleanly ``undone`` new ones gone).

    A relation an event in ``later`` began did not exist yet, and an ending one made had not happened. A
    new event revoked on its device left nothing in force (a revocation restores in place), so its begun
    relations, retracted, are left out too. Everything else (the agreed relations, earlier events' changes,
    hand edits) stands as merged.
    """
    found = []
    for relation in relations:
        began = parse_token(relation.provenance)
        if began is not None and began[0] == "accepted" and (began[1] in later or began[1] in undone):
            continue
        if relation.ending is not None:
            ended = parse_token(relation.ending.provenance)
            if ended is not None and ended[0] == "accepted" and ended[1] in later:
                relation = replace(relation, ending=None)
        found.append(relation)
    return found


def _proposal(event: AcceptedEvent) -> EffectProposal | None:
    """The proposal 021's planner plans, rebuilt from the event (``None``: it has nothing to plan)."""
    if not event.operations and event.holdings is None:
        return None
    holdings: EffectHoldings | None = None if event.holdings is None else event.holdings.holdings
    return EffectProposal(event.ref.campaign_id, event.chat, event.moment, event.summary,
                          tuple(item.op for item in event.operations), event.proposed_by,
                          attempt_id=event.attempt_id, holdings=holdings)


def replay(campaign_id: str, events: Mapping[str, AcceptedEvent], order: Iterable[str],
           relations: Iterable[Relation], instances: frozenset[InstanceRef]) -> list[ReviewItem]:
    """Replay each new event, in merged order, with 021's planner; every one must reproduce its receipts."""
    order, relations = list(order), list(relations)
    items: list[ReviewItem] = []
    undone = frozenset(event_id for event_id in order if not events[event_id].active)
    for event_id in sorted(undone):
        revocation = events[event_id].revocation
        assert revocation is not None
        if revocation.skipped:
            items.append(ReviewItem("partial_revocation", event_id, "some relation changes were left in force"))
        if revocation.holdings_left:
            items.append(ReviewItem("partial_revocation", event_id, "its holdings change was left in force"))
    try:
        final = GraphIndex.build(campaign_id, relations, instances)
    except GraphError as error:
        return items + [ReviewItem("receipt_mismatch", campaign_id, f"the merged relations: {error.reason}")]
    for number, event_id in enumerate(order):
        event = events[event_id]
        if not event.active:
            continue
        later = frozenset(other for other in order[number:] if events[other].active)
        try:  # the graph as it stood before this event; rolling a hand-edited relation back can break it
            index = GraphIndex.build(campaign_id, _before(relations, later, undone), instances)
        except GraphError as error:
            items.append(ReviewItem("receipt_mismatch", event_id, f"the relations before it: {error.reason}"))
            continue
        proposal = _proposal(event)
        try:
            steps = () if proposal is None else plan(proposal, index)
        except EffectError as error:
            items.append(ReviewItem(error.reason, event_id, ",".join(error.records)))
            continue
        for step in steps:
            if isinstance(step, EndStep) and step.relation != event.receipts[step.op].relation:
                items.append(ReviewItem("receipt_mismatch", event_id, f"operation {step.op} now ends "
                                        f"{step.relation.relation_id}"))
        for attempt in event.ability_attempts:
            if attempt.actor not in instances:
                items.append(ReviewItem("missing_agent", event_id, attempt.ability_id))
        for problem in verify_receipts(event, final):
            items.append(ReviewItem("receipt_mismatch", event_id, f"operation {problem.op}: {problem.detail}"))
    return items


def relation_values(relations: Mapping[RelationRef, Output], base: Ledger, sides: Mapping[str, Ledger]
                    ) -> list[Relation]:
    """The merged relation files, decoded: each the version its output keeps."""
    found = []
    for ref, output in relations.items():
        source = output.source  # type: ignore[union-attr]
        files = base.relations if source == BASE else sides[source].relations
        found.append(files[ref].value)
    return found
