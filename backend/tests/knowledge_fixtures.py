"""Synthetic knowledge fixtures shared by the 012 tests. No user content.

Distinctive tokens let substring checks prove absence without false hits:
the betrayal's event id is ``evt-q7``, its summary contains ``SUMMARY-TOKEN``
and the true proposition's text contains ``TRUTH-TOKEN``.
"""

from grimoire.core import (
    Belief, Disclosure, Event, InstanceRef, Knowledge, Moment, Perception, Proposition, Report,
    Ruling, Subject,
)

C = "campaign-a"
SUMMARY = "SUMMARY-TOKEN: the duke sold the guild's charter to its rivals"
TRUTH = "TRUTH-TOKEN the duke betrayed the guild"
LIE = "the duke was framed by the guild"


def actor(name: str, kind: str = "character") -> InstanceRef:
    return InstanceRef(C, kind, name)


INES, ORRIN, TAVI, WREN = actor("ines"), actor("orrin"), actor("tavi"), actor("wren")
GUILDMASTER, GUILD = actor("guildmaster"), actor("guild", "group")
DUKE = actor("duke")
BETRAYAL = Subject("event", "evt-q7")
ABOUT = (BETRAYAL, Subject("instance", str(DUKE)))


def at(sequence: int, scene: str | None = None, turn: str | None = None) -> Moment:
    return Moment(sequence, scene, turn)


def betrayal_event() -> Event:
    return Event(C, "evt-q7", SUMMARY, at(1), "director", participants=(GUILDMASTER, GUILD, DUKE))


def witness(holder: InstanceRef, evidence_id: str, percept: str, sequence: int = 1) -> Perception:
    return Perception(C, evidence_id, holder, "evt-q7", "witnessed", percept, at(sequence),
                      "director", confirmed=True)


def told(evidence_id: str, holder: InstanceRef, proposition_id: str, sequence: int, *,
         sender: InstanceRef | None = None, attributed_to: InstanceRef | None = None,
         addressed: bool = False, sent_at: int | None = None, confirmed: bool = True,
         channel: str = "told") -> Report:
    return Report(C, evidence_id, holder, proposition_id, channel, at(sequence), "director",
                  confirmed=confirmed, sender=sender, attributed_to=attributed_to,
                  addressed=addressed, sent_at=None if sent_at is None else at(sent_at))


def believes(belief_id: str, holder: InstanceRef, proposition_id: str, stance: str,
             sequence: int, evidence_ids, *, supersedes: str | None = None,
             authored_by: str = "director") -> Belief:
    return Belief(C, belief_id, holder, proposition_id, stance, at(sequence), authored_by,
                  tuple(evidence_ids), supersedes=supersedes)


def betrayal_records() -> dict:
    """Scenarios A-C on one timeline: witness (1), lie and rulings (2), correction (4)."""
    return dict(
        events=[betrayal_event()],
        propositions=[
            Proposition(C, "duke-betrayed", TRUTH, at(1), "director", about=ABOUT),
            Proposition(C, "duke-innocent", LIE, at(2), "director", about=ABOUT),
        ],
        rulings=[
            Ruling(C, "rule-lie", "duke-innocent", "refuted", at(2), "director", grounds=(BETRAYAL,)),
            Ruling(C, "rule-truth", "duke-betrayed", "established", at(2), "director",
                   grounds=(BETRAYAL,)),
        ],
        evidence=[
            witness(INES, "p-ines", "a sealed charter changing hands in the chapel"),
            witness(ORRIN, "p-orrin", "two cloaked figures and the clink of coin"),
            told("r-orrin-tavi", TAVI, "duke-innocent", 2, sender=ORRIN, attributed_to=ORRIN,
                 addressed=True, sent_at=2),
            told("r-ines-tavi", TAVI, "duke-betrayed", 4, sender=INES, attributed_to=INES,
                 addressed=True, sent_at=4),
        ],
        beliefs=[
            believes("b-tavi-1", TAVI, "duke-innocent", "believed", 2, ["r-orrin-tavi"]),
            believes("b-tavi-2", TAVI, "duke-innocent", "rejected", 4, ["r-ines-tavi"],
                     supersedes="b-tavi-1"),
            believes("b-tavi-3", TAVI, "duke-betrayed", "believed", 4, ["r-ines-tavi"]),
        ],
    )


def betrayal_knowledge(**changes) -> Knowledge:
    records = betrayal_records()
    for family, extra in changes.items():
        records[family] = list(records.get(family, [])) + list(extra)
    return Knowledge(C, **records)


def disclosure(disclosure_id: str, sequence: int, event_id: str = "evt-q7") -> Disclosure:
    return Disclosure(C, disclosure_id, event_id, at(sequence), "director")


def walk(value):
    """Every string reachable from a projection value (fields, tuples, refs)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, (tuple, list, frozenset, set)):
        for item in value:
            yield from walk(item)
    elif hasattr(value, "__dataclass_fields__"):
        yield str(value)
        for name in value.__dataclass_fields__:
            yield from walk(getattr(value, name))
    elif value is not None and not isinstance(value, (int, bool)):
        yield str(value)


def leaks(value, *tokens) -> list[str]:
    text = "\n".join([repr(value), *walk(value)])
    return [token for token in tokens if token in text]
