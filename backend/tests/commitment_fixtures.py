"""A synthetic campaign for the 077 commitment tests: 021's cast, a 055 calendar and 020 objective events.

Campaign ``c`` from ``effects_fixtures``: Wren is the player character (``human:ana``), Tam is
director-controlled and Ines has no controller. The calendar starts on 2024-02-20 at noon. Every name and
event is synthetic; nothing calls a model.
"""

from __future__ import annotations

from grimoire.calendars import CalendarDefinition, Duration
from grimoire.calendarstore import CalendarLibrary
from grimoire.commitments import Commitment, transition_fingerprint, void_fingerprint
from grimoire.commitmentstore import CommitmentLibrary
from grimoire.core import Event, Moment
from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import ScopeRef
from grimoire.effectstore import Endorsement
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore

from effects_fixtures import ANA, C, CONTROLS, INES, TAM, WREN, Handles, seed
from store_helpers import OWNER

SCOPE = ScopeRef(C)


def campaign(store: LibraryStore, handles: Handles, *, calendar: bool = True) -> CommitmentLibrary:
    """The seeded campaign, with its calendar started (unless ``calendar`` is false), and its commitments."""
    seed(store, handles)
    if calendar:
        clock = CalendarLibrary(store, handles.app)
        clock.initialize(SCOPE, "start", CalendarDefinition(), "2024-02-20", minute=720,
                         expected=clock.read(SCOPE).etag).commit()
    return commitments(store, handles)


def commitments(store: LibraryStore, handles: Handles) -> CommitmentLibrary:
    return CommitmentLibrary(store, handles.app, issued=handles.authorizer.issued)


def event(store: LibraryStore, handles: Handles, event_id: str, sequence: int, *, scene: str = "s1",
          turn: str | None = None, summary: str = "Something happened at the harbor.") -> Event:
    """Record one 020 objective event at ``sequence`` in ``scene`` and turn ``turn`` (default ``t<sequence>``)."""
    found = Event(C, event_id, summary, Moment(sequence, scene, turn or f"t{sequence}"), "director")
    EvidenceLibrary(store, handles.app, owner=OWNER).append(C, [found]).commit()
    return found


def advance(store: LibraryStore, handles: Handles, operation_id: str, minutes: int) -> None:
    clock = CalendarLibrary(store, handles.app)
    clock.advance(SCOPE, operation_id, Duration(minutes), expected=clock.read(SCOPE).etag).commit()


def mission(commitment_id: str = "m-tower", *, owed_by=(WREN,), owed_to=(TAM,), deadline_id: str | None = "dl-tower",
            kind: str = "mission", summary: str = "Clear the old signal tower before the new moon.") -> Commitment:
    return Commitment(C, commitment_id, kind, summary, tuple(owed_by), tuple(owed_to), deadline_id)


def etag(library: CommitmentLibrary) -> str:
    return library.read(SCOPE, requested_by="director").etag


def say(cid: str = "c-wren", *, scene: str = "s1", turn: str = "t2", actor_id: str = "wren", by: str = ANA,
        kind: str = "commitment", text: str = "I will not take the tower job.") -> PlayerContribution:
    """Wren's player's own words, covering the commitment domain."""
    return PlayerContribution(C, cid, scene, turn, actor_id, by, kind, text)


def endorse(handles: Handles, commitment_id: str, status: str, cites, *, cid: str = "c-wren", note: str = "",
            principal: str = ANA) -> Endorsement:
    return Endorsement(cid, handles.player(principal), transition_fingerprint(C, commitment_id, status, cites, note))


def endorse_void(handles: Handles, target: str, reason: str, *, cid: str = "c-wren",
                 principal: str = ANA) -> Endorsement:
    return Endorsement(cid, handles.player(principal), void_fingerprint(C, target, reason))
