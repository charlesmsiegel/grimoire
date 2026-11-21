"""The story explorer's values (081): one proposition's thread through the story, in story order.

A thread is read from 020's ledger and nothing else: it is a disposable view, never a record, and nothing here
writes. Every entry is one stored record, named as 020 names it (``"<family>:<id>"``), with the moment it carries
and the chat it was recorded from when the ledger says (020's ``origins``), so each one leads back to its source.

Each entry is graded, so a reader never takes an interpretation for truth:

``confirmed``
    canon: an accepted event (012's objective happening) or a ruling on the proposition's standing;
``belief``
    one character's own conclusion: never the standing, whatever its stance;
``proposal``
    an assertion only: the proposition as first recorded, a report of it some character received (or sent), or
    what a character observed that their belief cites (their own percept, never the event as canon has it).
    Receiving, repeating, seeing or believing it never makes it established;
``revoked``
    an event whose accepted effect (021) was revoked: it no longer stands as world truth, though its 020 bridge
    is still stored (021 reports it as ``revoked_but_bridged``), and a ruling that rests on it says so.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.knowledge import Moment, Proposition

GRADES = ("confirmed", "belief", "proposal", "revoked")
#: the order of entries that share a moment: what is stated, what happened, who saw or heard it, how canon ruled on
#: it, and what each character concluded from all that
KINDS = ("stated", "event", "observation", "report", "sent", "ruling", "belief")
CONTRADICTIONS = ("reversed_ruling", "belief_against_canon", "revoked_ground")


@dataclass(frozen=True, slots=True)
class Entry:
    """One stored record of a thread.

    ``actors`` are those it involves: a report's holder (and, for the director, its sender and the one it is
    attributed to), a belief's or an observation's holder, an event's participants, the instances a proposition is
    overtly about. ``text`` is what the record itself says: the proposition's text, an event's summary, a percept.
    ``holder`` is who holds the record: a report's or a ``sent`` entry's recipient, a belief's or an observation's
    holder; ``sender`` who passed a report on (in a table view, only for the table's own ``sent`` entries).
    ``chat`` is the chat 020 recorded it from (provenance only); ``moment`` carries the scene and turn labels.
    """

    kind: str
    record: str
    grade: str
    moment: Moment
    text: str
    actors: tuple[InstanceRef, ...] = ()
    chat: ChatRef | None = None
    holder: InstanceRef | None = None
    sender: InstanceRef | None = None
    attributed_to: InstanceRef | None = None
    sent_at: Moment | None = None
    confirmed: bool | None = None
    stance: str | None = None
    current: bool | None = None
    inherited: bool | None = None
    supersedes: str | None = None
    standing: str | None = None
    grounds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Outcome:
    """Canon's standing of the proposition as of the read (012: the latest ruling; else ``reported`` once any
    report was received; else ``unasserted``), with the ruling that decided it, its moment and what it rests on.
    ``revoked_grounds`` are the events it rests on whose accepted effect was since revoked."""

    standing: str
    ruling: str | None = None
    moment: Moment | None = None
    grounds: tuple[str, ...] = ()
    revoked_grounds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Contradiction:
    """Where the story disagrees with itself, named by the records that disagree (in story order).

    ``reversed_ruling``: canon ruled the proposition established, then refuted (or the other way round).
    ``belief_against_canon``: a character's current belief goes against canon's standing now (believed or
    suspected while refuted, rejected while established).
    ``revoked_ground``: the ruling that decides the standing rests on an event whose effect was revoked."""

    kind: str
    records: tuple[str, ...]
    moment: Moment


@dataclass(frozen=True, slots=True)
class Scene:
    """One place the thread passes through: a chat (from 020's origins) or, for a record with no chat, the scene
    label its moment carries (``None``: the record names no scene). ``turns`` are the turn labels its entries
    carry, in story order; ``records`` its entries; ``actors`` everyone they involve."""

    chat: ChatRef | None
    scene: str | None
    first: int
    last: int
    turns: tuple[str, ...]
    records: tuple[str, ...]
    actors: tuple[InstanceRef, ...]


@dataclass(frozen=True, slots=True)
class Thread:
    """One proposition through the story as of ``as_of``. ``outcome`` is ``None`` in a table view: canon's
    standing is the director's, and so are rulings, events, senders and contradictions with canon."""

    proposition: Proposition
    as_of: int
    entries: tuple[Entry, ...]
    outcome: Outcome | None
    contradictions: tuple[Contradiction, ...]
    scenes: tuple[Scene, ...]

    @property
    def actors(self) -> tuple[InstanceRef, ...]:
        return _actors(entry.actors for entry in self.entries)

    def involves(self, actor: InstanceRef) -> bool:
        return any(actor in entry.actors for entry in self.entries)

    def scenes_with(self, actor: InstanceRef) -> tuple[Scene, ...]:
        """Every scene where this thread involves ``actor`` (the first milestone: one character, one thread)."""
        return tuple(scene for scene in self.scenes if actor in scene.actors)


@dataclass(frozen=True, slots=True)
class Summary:
    """One thread as a search lists it."""

    proposition: Proposition
    first: Moment
    last: int
    standing: str | None
    decided: Moment | None
    entries: int
    scenes: int
    contradictions: int
    actors: tuple[InstanceRef, ...]


def _actors(groups) -> tuple[InstanceRef, ...]:  # type: ignore[no-untyped-def]
    found: dict[str, InstanceRef] = {}
    for group in groups:
        for actor in group:
            found.setdefault(str(actor), actor)
    return tuple(found[key] for key in sorted(found))


def summarize(thread: Thread) -> Summary:
    outcome = thread.outcome
    return Summary(thread.proposition, thread.entries[0].moment if thread.entries else thread.proposition.moment,
                   max((entry.moment.sequence for entry in thread.entries), default=thread.proposition.moment.sequence),
                   None if outcome is None else outcome.standing, None if outcome is None else outcome.moment,
                   len(thread.entries), len(thread.scenes), len(thread.contradictions), thread.actors)


__all__ = [
    "CONTRADICTIONS",
    "GRADES",
    "KINDS",
    "Contradiction",
    "Entry",
    "Outcome",
    "Scene",
    "Summary",
    "Thread",
    "summarize",
]
