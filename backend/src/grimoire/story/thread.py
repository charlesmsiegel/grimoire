"""One proposition's thread through the director's ledger (081), and the search that finds it.

Pure and read-only. What belongs to a thread is what the ledger links to the proposition explicitly, never what its
text suggests:

- the proposition itself, at the moment it was recorded (``stated``);
- every report of it, received or knowingly sent (``report``);
- every ruling on its standing (``ruling``) and every belief about it (``belief``);
- the events those records name: a ruling's event grounds, the events the proposition is overtly about, and the
  events whose perception a belief about it cites (``event``).

So "find an old promise and its eventual outcome" is: the promise is a proposition ("Mara will bring the key
back before the thaw"), and its outcome is canon's standing, the latest ruling, with the events it rests on and
the scene each happened in. A character who still believes what canon refuted, a ruling that reversed an earlier
one and a ruling resting on a revoked event are the thread's contradictions.

Every read is ``as_of`` a campaign sequence (020's clock): nothing later is shown, so a thread can be read as it
stood at any point of the story. ``origins`` is 020's map from a record's name to the chat it was recorded from;
``revoked`` the ids of events whose 021 accepted effect was revoked (their 020 bridge outlives the revocation).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import pairwise
from types import MappingProxyType

from grimoire.core import DIRECTOR
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.knowledge import Belief, Event, Knowledge, KnowledgeError, Perception, Report
from grimoire.core.perspective import director_knowledge

from .model import KINDS, Contradiction, Entry, Outcome, Scene, Summary, Thread, _actors, summarize

EMPTY: Mapping[str, ChatRef] = MappingProxyType({})
#: how many threads one search lists at most
MAX_LISTED = 200
#: the longest search text: longer is refused, never cut
MAX_QUERY = 200
_AGAINST = {"refuted": ("believed", "suspected"), "established": ("rejected",)}


def order(entry: Entry) -> tuple[int, int, str]:
    return (entry.moment.sequence, KINDS.index(entry.kind), entry.record)


def scenes_of(entries: Iterable[Entry]) -> tuple[Scene, ...]:
    """The places ``entries`` pass through, in the order the thread first reaches each."""
    found: dict[tuple, list[Entry]] = {}
    for entry in entries:
        key = ("chat", str(entry.chat)) if entry.chat is not None else ("scene", entry.moment.scene_id)
        found.setdefault(key, []).append(entry)
    scenes = []
    for group in found.values():
        turns: list[str] = []
        for entry in group:
            if entry.moment.turn_id is not None and entry.moment.turn_id not in turns:
                turns.append(entry.moment.turn_id)
        first = group[0]
        scenes.append(Scene(first.chat, first.moment.scene_id, group[0].moment.sequence, group[-1].moment.sequence,
                            tuple(turns), tuple(entry.record for entry in group),
                            _actors(entry.actors for entry in group)))
    return tuple(scenes)


class _Ledger:
    """One director read's indexes: built once, so a search builds every thread in one pass over the ledger."""

    def __init__(self, knowledge: Knowledge, as_of: int, origins: Mapping[str, ChatRef],
                 revoked: frozenset[str]) -> None:
        if not isinstance(knowledge, Knowledge):
            raise KnowledgeError("invalid_record")
        truth = director_knowledge(knowledge, as_of=as_of, requested_by=DIRECTOR)  # checks as_of
        self.knowledge, self.as_of, self.origins, self.revoked = knowledge, as_of, origins, revoked
        self.standings = {item.proposition.proposition_id: (item.standing, item.ruling_id)
                          for item in truth.propositions}
        self.propositions = [item.proposition for item in truth.propositions]
        self.events = {event.event_id: event for event in truth.events}
        self.perceptions = {item.evidence_id: item for item in truth.evidence if isinstance(item, Perception)}
        self.reports: dict[str, list[Report]] = {}
        for item in truth.evidence:
            if isinstance(item, Report):
                self.reports.setdefault(item.proposition_id, []).append(item)
        self.beliefs: dict[str, list[Belief]] = {}
        for belief in truth.beliefs:
            self.beliefs.setdefault(belief.proposition_id, []).append(belief)
        self.rulings = {}
        for ruling in truth.rulings:
            self.rulings.setdefault(ruling.proposition_id, []).append(ruling)

    def _chat(self, family: str, record_id: str) -> ChatRef | None:
        return self.origins.get(f"{family}:{record_id}")

    def _event(self, event: Event) -> Entry:
        grade = "revoked" if event.event_id in self.revoked else "confirmed"
        return Entry("event", f"event:{event.event_id}", grade, event.moment, event.summary, event.participants,
                     self._chat("event", event.event_id))

    def _current(self, belief: Belief) -> bool:
        successor = self.knowledge.successor_of(belief.belief_id)
        return successor is None or successor.moment.sequence > self.as_of

    def thread(self, proposition_id: str) -> Thread:
        if proposition_id not in self.standings:  # unknown, malformed or not yet recorded as of this read
            raise KnowledgeError("unknown_proposition")
        proposition = self.knowledge.proposition(proposition_id)
        about = tuple(InstanceRef.parse(item.ref) for item in proposition.about if item.kind == "instance")
        entries = [Entry("stated", f"proposition:{proposition_id}", "proposal", proposition.moment, proposition.text,
                         about, self._chat("proposition", proposition_id))]
        events = {item.ref for item in proposition.about if item.kind == "event"}
        for report in self.reports.get(proposition_id, ()):
            people = tuple(actor for actor in (report.holder, report.sender, report.attributed_to) if actor is not None)
            # sent by now but received later: shown as the send, at the moment it was sent (020's director read)
            received = report.moment.sequence <= self.as_of
            entries.append(Entry("report" if received else "sent", f"evidence:{report.evidence_id}", "proposal",
                                 report.moment if received else report.sent_at,
                                 proposition.text, people, self._chat("evidence", report.evidence_id),
                                 holder=report.holder, sender=report.sender, attributed_to=report.attributed_to,
                                 sent_at=report.sent_at, confirmed=report.confirmed))
        rulings = sorted(self.rulings.get(proposition_id, ()), key=lambda r: (r.moment.sequence, r.ruling_id))
        for ruling in rulings:
            grounds = tuple(str(item) for item in ruling.grounds)
            events |= {item.ref for item in ruling.grounds if item.kind == "event"}
            entries.append(Entry("ruling", f"ruling:{ruling.ruling_id}", "confirmed", ruling.moment, proposition.text,
                                 chat=self._chat("ruling", ruling.ruling_id), standing=ruling.standing,
                                 grounds=grounds))
        beliefs = self.beliefs.get(proposition_id, ())
        for belief in beliefs:
            for cited in belief.evidence_ids:
                perception = self.perceptions.get(cited)
                if perception is not None:
                    events.add(perception.event_id)
            entries.append(Entry("belief", f"belief:{belief.belief_id}", "belief", belief.moment, proposition.text,
                                 (belief.holder,), self._chat("belief", belief.belief_id), holder=belief.holder,
                                 stance=belief.stance, current=self._current(belief), supersedes=belief.supersedes))
        entries += [self._event(self.events[event_id]) for event_id in sorted(events) if event_id in self.events]
        entries.sort(key=order)

        standing, ruling_id = self.standings[proposition_id]
        decided = next((ruling for ruling in rulings if ruling.ruling_id == ruling_id), None)
        if decided is None:
            outcome = Outcome(standing)
        else:
            rests = tuple(item.ref for item in decided.grounds if item.kind == "event")
            outcome = Outcome(standing, ruling_id, decided.moment, tuple(str(item) for item in decided.grounds),
                              tuple(event_id for event_id in rests if event_id in self.revoked))
        contradictions = []
        ruled = [ruling for ruling in rulings if ruling.standing != "unknown"]
        for before, after in pairwise(ruled):
            if before.standing != after.standing:
                contradictions.append(Contradiction("reversed_ruling", (f"ruling:{before.ruling_id}",
                                                                        f"ruling:{after.ruling_id}"), after.moment))
        for belief in beliefs:
            if self._current(belief) and belief.stance in _AGAINST.get(standing, ()):
                contradictions.append(Contradiction("belief_against_canon", (f"ruling:{ruling_id}",
                                                                             f"belief:{belief.belief_id}"),
                                                    belief.moment))
        if decided is not None and outcome.revoked_grounds:
            contradictions.append(Contradiction(
                "revoked_ground", (f"ruling:{ruling_id}", *(f"event:{item}" for item in outcome.revoked_grounds)),
                decided.moment))
        contradictions.sort(key=lambda item: (item.moment.sequence, item.kind, item.records))
        return Thread(proposition, self.as_of, tuple(entries), outcome, tuple(contradictions), scenes_of(entries))


def thread(knowledge: Knowledge, proposition_id: str, *, as_of: int, origins: Mapping[str, ChatRef] = EMPTY,
           revoked: Iterable[str] = ()) -> Thread:
    """The director's thread of one proposition as of ``as_of``. A proposition the ledger does not hold, or not
    yet at ``as_of``, is 012's ``unknown_proposition``."""
    return _Ledger(knowledge, as_of, origins, frozenset(revoked)).thread(proposition_id)


def _query(text: object) -> str | None:
    if text is None:
        return None
    if type(text) is not str or len(text) > MAX_QUERY:
        raise ValueError("invalid_query")
    return text.strip().casefold() or None


def matches(item: Thread, query: str | None, actor: InstanceRef | None) -> bool:
    """Whether ``query`` is in the proposition's text or in the summary of an event of its thread (so a search
    for how a promise ended finds the promise), and ``actor`` is involved in it."""
    if actor is not None and not item.involves(actor):
        return False
    if query is None:
        return True
    texts = [item.proposition.text, *(entry.text for entry in item.entries if entry.kind == "event")]
    return any(query in text.casefold() for text in texts)


def ranked(found: Iterable[Thread], query: object, actor: InstanceRef | None,
           limit: int) -> tuple[tuple[Summary, ...], int]:
    """The threads that match, oldest first (by when each first entered the story), at most ``limit`` of them,
    and how many matched."""
    if type(limit) is not int or not 1 <= limit <= MAX_LISTED:
        raise ValueError("invalid_limit")
    wanted = _query(query)
    hits = [summarize(item) for item in found if matches(item, wanted, actor)]
    hits.sort(key=lambda item: (item.first.sequence, item.proposition.proposition_id))
    return tuple(hits[:limit]), len(hits)


def search(knowledge: Knowledge, *, as_of: int, query: object = None, actor: InstanceRef | None = None,
           origins: Mapping[str, ChatRef] = EMPTY, revoked: Iterable[str] = (),
           limit: int = 50) -> tuple[tuple[Summary, ...], int]:
    """Every thread of the director's ledger as of ``as_of`` that matches: see ``matches`` and ``ranked``."""
    ledger = _Ledger(knowledge, as_of, origins, frozenset(revoked))
    return ranked((ledger.thread(item.proposition_id) for item in ledger.propositions), query, actor, limit)


__all__ = ["EMPTY", "MAX_LISTED", "MAX_QUERY", "matches", "order", "ranked", "scenes_of", "search", "thread"]
