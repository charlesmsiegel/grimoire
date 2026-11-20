"""What each cited 020 objective event's support is now: the store-reading half of 077's fold.

A citation is ``supported`` only when the event is on the campaign's 020 ledger and, when it is a 021
accepted event's bridge (its provenance is ``effect:accepted/<id>``), that accepted event decodes, is still
active, and bridges to exactly that event. A revoked accepted event is a retcon (``revoked``). A bridge whose
accepted record is not there, or could not be listed, cannot be checked for a retcon: ``unreadable``. An event
that is not on the ledger is ``missing`` only when nothing could hide it: a quarantined record or a 020
ledger problem makes it ``unreadable`` instead, as 020's own store refuses to guess around damage. A damaged
accepted record taints only the event of its own id; a problem that names no id taints every bridge.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import AcceptedRef
from grimoire.core.knowledge import Event
from grimoire.effects import EffectCodecError, EffectError, accepted_from_payload, accepted_token, as_knowledge_event
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore

_ALL = None  # a problem that names no accepted event id


@dataclass(frozen=True)
class Support:
    """Per cited event id: its support, and the event itself when it is on record. Director-side."""
    values: Mapping[str, str]
    events: Mapping[str, Event]
    problems: tuple[str, ...]


def _damaged(campaign_id: str, problems) -> set[str | None]:
    """The accepted event ids a listing problem is about (``None``: one it cannot be tied to)."""
    ids: set[str | None] = set()
    for problem in problems:
        path = PurePosixPath(str(problem.path))
        parts = path.parts
        tied = path.suffix == ".json" and len(parts) >= 2 and parts[-2] == "accepted" \
            and (len(parts) < 4 or parts[-3] == campaign_id)
        ids.add(path.stem if tied else _ALL)
    return ids


def read_support(store: LibraryStore, campaign_id: str, event_ids: Iterable[str], *,
                 requested_by: str) -> Support:
    """Director-only read of 020 and 021 for ``event_ids`` (it returns event summaries). Writes nothing."""
    if requested_by != DIRECTOR:
        raise AuthorityError("citation support is read from the director's ledger")
    wanted = sorted(set(event_ids))
    ledger = EvidenceLibrary(store).read(campaign_id, requested_by=DIRECTOR)
    events = {event.event_id: event for event in ledger.knowledge.events}
    quarantined = {item.name for item in ledger.quarantined}
    accepted = store.campaign_accepted(campaign_id)
    damaged = _damaged(campaign_id, accepted.problems)
    problems = tuple(ledger.problems) + tuple(f"{p.path}:{p.code}" for p in accepted.problems)
    values: dict[str, str] = {}
    for event_id in wanted:
        event = events.get(event_id)
        if event is None:
            hidden = f"event:{event_id}" in quarantined or bool(ledger.problems)
            values[event_id] = "unreadable" if hidden else "missing"
            continue
        stored = accepted.records.get(AcceptedRef(campaign_id, event_id))
        bridge = event.provenance == accepted_token(event_id)
        if event_id in damaged or (bridge and _ALL in damaged):
            values[event_id] = "unreadable"
            continue
        if stored is None:
            # A bridge whose accepted event is not here cannot be checked for a retcon; any other event
            # stands alone (020 refuses a non-bridge under an accepted id, and 021 reports one that syncs in).
            values[event_id] = "unreadable" if bridge else "supported"
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
