"""What each cited 020 objective event's support is now: the store-reading half of 077's fold.

A citation is ``supported`` only when the event is on the campaign's 020 ledger and, when an accepted (021)
event shares its id (its bridge), that accepted event decodes, is still active, and bridges to exactly that
event. A revoked accepted event is a retcon (``revoked``). An event that is not there is ``missing`` only
when nothing could hide it: a quarantined record, a ledger problem or an unreadable accepted-event family
makes it ``unreadable`` instead, as 020's own store refuses to guess around damage.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef
from grimoire.core.knowledge import Event
from grimoire.effects import EffectCodecError, EffectError, accepted_from_payload, as_knowledge_event
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore


@dataclass(frozen=True)
class Support:
    """Per cited event id: its support, and the event itself when it is on record."""
    values: Mapping[str, str]
    events: Mapping[str, Event]
    problems: tuple[str, ...]


def read_support(store: LibraryStore, campaign_id: str, event_ids: Iterable[str]) -> Support:
    """Director-side read of 020 and 021 for ``event_ids``. Writes nothing and calls no model."""
    wanted = sorted(set(event_ids))
    ledger = EvidenceLibrary(store).read(campaign_id, requested_by=DIRECTOR)
    events = {event.event_id: event for event in ledger.knowledge.events}
    quarantined = {item.name for item in ledger.quarantined}
    accepted = store.campaign_accepted(campaign_id)
    problems = tuple(ledger.problems) + tuple(f"{p.path}:{p.code}" for p in accepted.problems)
    values: dict[str, str] = {}
    for event_id in wanted:
        event = events.get(event_id)
        if event is None:
            hidden = f"event:{event_id}" in quarantined or bool(ledger.problems) or bool(accepted.problems)
            values[event_id] = "unreadable" if hidden else "missing"
            continue
        stored = accepted.records.get(AcceptedRef(campaign_id, event_id))
        if stored is None:
            # No accepted event of that id: unless the family could not be listed, the event stands alone.
            values[event_id] = "unreadable" if accepted.problems else "supported"
            continue
        try:
            found = accepted_from_payload(AcceptedRef(campaign_id, event_id), stored.record.schema,  # type: ignore[union-attr]
                                          stored.record.payload)
        except EffectCodecError:
            values[event_id] = "unreadable"
            continue
        if not found.active:
            values[event_id] = "revoked"
            continue
        try:
            bridged = as_knowledge_event(found)
        except EffectError:
            bridged = None
        values[event_id] = "supported" if bridged == event else "unreadable"
    return Support(values, {key: events[key] for key in wanted if key in events}, tuple(sorted(set(problems))))
