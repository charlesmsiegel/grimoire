"""One campaign's whole ledger merge (028): events, wrap-ups, relations and holder files together.

The pieces depend on each other: holder operations are ordered by their events' merged positions, the
events' holdings receipts are rewritten from the replayed seqs, and the replay of the events reads the
merged relations. ``merge_ledger`` runs them in that order and then checks the result as 021 would: the
merged ledger may report no 021 problem that neither device reported (``new_problem``).

A review whose every reason is a policy one (``POLICY``) carries the merge it would otherwise have made
(``candidate``): whether that merge is acceptable is the owner's decision (028's review, later), not this
package's. Pure: no files, clocks or randomness.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.identity import InstanceRef
from grimoire.effects import (
    AcceptedEvent, EffectProblem, edited_effects, holdings_orphans, holdings_partials, holdings_problems,
    ledger_problems, orphans, partial_revocations, verify_receipts, wrapup_problems,
)
from grimoire.graph import GraphError, GraphIndex, Relation
from grimoire.holdings import MAX_FILE, Holdings

from . import events as _events
from .holdings import EnvelopeSizer, merge_holdings
from .model import Ledger, Merged, Review, ReviewItem, ranks

#: reasons that stop an automatic merge only by policy: the merge itself replays cleanly
POLICY = frozenset({"same_chat", "revoked_vs_extended"})


@dataclass(frozen=True)
class LedgerReview(Review):
    """A review; ``candidate`` is the merge that would be made if every reason here is accepted (policy only)."""

    candidate: Merged | None = None


def _blocking(items: Iterable[ReviewItem]) -> bool:
    return any(item.reason not in POLICY for item in items)


def merge_ledger(base: Ledger, sides: Mapping[str, Ledger], *, instances: Iterable[InstanceRef],
                 sizer: EnvelopeSizer, max_file: int = MAX_FILE) -> Merged | LedgerReview:
    """Merge one campaign's ledger: ``base`` as both devices last agreed, ``sides`` each device's, by id.

    ``instances`` are the campaign's instances in the merged library (what the planner and 023 check
    endpoints, agents and holders against). The result is symmetric in the two sides and idempotent: a
    merged ledger merged with itself, or with either side it came from, is unchanged.
    """
    rank = ranks(sides)
    if any(side.campaign_id != base.campaign_id for side in sides.values()):
        raise ValueError("every side must be the base's campaign")
    devices = sorted(sides, key=rank.__getitem__)
    instances = frozenset(instances)
    found = _events.order_events(base, sides)
    revoked = frozenset(ref.event_id for ref, (_, version) in found.chosen.items() if not version.value.active)
    relations, relation_items = _events.three_way(base.relations, {d: sides[d].relations for d in devices},
                                                  "relation", revoked)
    wrapups, wrapup_items = _events.three_way(base.wrapups, {d: sides[d].wrapups for d in devices}, "wrapup")
    items = list(found.items) + relation_items + wrapup_items
    if _blocking(items):
        return LedgerReview(tuple(items))
    held = merge_holdings(base.holdings, {d: sides[d].holdings for d in devices}, exists=instances.__contains__,
                          sizer=sizer, key=found.op_key, max_file=max_file)
    if isinstance(held, Review):
        return LedgerReview(tuple(items) + held.items)
    merged = _events.rewrite(found, held.seqs)
    by_id = {ref.event_id: event for ref, event in merged.items()}
    relation_values = _events.relation_values(relations, base, sides)
    items += _events.replay(base.campaign_id, by_id, (ref.event_id for ref in found.order), relation_values,
                            instances, found.numbering)
    wrapup_values = [(base.wrapups if out.source == "base" else sides[out.source].wrapups)[ref].value  # type: ignore[union-attr]
                     for ref, out in wrapups.items()]
    after = problems(base.campaign_id, merged.values(), relation_values, wrapup_values, held.values, instances)
    before: set[EffectProblem] = set()
    for device in devices:
        side = sides[device]
        before |= problems(base.campaign_id, (v.value for v in side.events.values()),
                           (v.value for v in side.relations.values()), (v.value for v in side.wrapups.values()),
                           {holder: v.value for holder, v in side.holdings.items()}, instances)
    for problem in sorted(after - before, key=EffectProblem.sort_key):
        subject = problem.event_id or problem.relation_id or problem.wrapup_id or base.campaign_id
        items.append(ReviewItem("new_problem", subject, f"{problem.code}: {problem.detail}"[:300]))
    files: dict = {}
    files.update(_events.event_outputs(found, merged, base, sides))
    files.update(relations)
    files.update(wrapups)
    files.update(held.files)
    result = Merged(files, tuple(ref.event_id for ref in found.order))
    if items:
        return LedgerReview(tuple(items), None if _blocking(items) else result)
    return result


def problems(campaign_id: str, events: Iterable[AcceptedEvent], relations: Iterable[Relation],
             wrapups: Iterable, holdings: Mapping[InstanceRef, Holdings],
             instances: frozenset[InstanceRef]) -> set[EffectProblem]:
    """The 021 problems ``continuity`` would report for this ledger (its 020 bridge and store problems aside)."""
    events, relations = list(events), list(relations)
    try:
        index = GraphIndex.build(campaign_id, relations, instances)
    except GraphError as error:
        return {EffectProblem("undecodable_relation", detail=error.reason)}
    holders: dict[InstanceRef, Holdings | str] = dict(holdings)
    for event in events:  # a holder an event names but whose file is gone reads as empty (EFFECTS)
        if event.holdings is not None:
            for holder in event.holdings.holdings.holders:
                holders.setdefault(holder, Holdings.empty(holder))
    found: set[EffectProblem] = set(ledger_problems(events))
    for event in events:
        found |= set(verify_receipts(event, index))
    found |= set(orphans(events, relations)) | set(edited_effects(events, index))
    found |= set(partial_revocations(events, index)) | set(holdings_problems(events, holders))
    found |= set(holdings_orphans(events, {h: v for h, v in holders.items() if isinstance(v, Holdings)}))
    found |= set(holdings_partials(events)) | set(wrapup_problems(wrapups, events))
    return found

