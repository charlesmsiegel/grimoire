"""Events, propositions, evidence and beliefs: what happened versus who may know it.

An accepted event is director truth. A proposition is something that can be
asserted or believed; only a director/adjudication ruling gives it canonical
standing. Evidence is one actor's perception of an event or receipt of a
report; a belief is that actor's own conclusion. Access is not truth: receiving,
repeating or believing a proposition never makes it established, and being a
participant, a group member or graph-connected never grants evidence.

``Knowledge`` is one campaign's validated, immutable ledger. It has no file
writer (020 persists it) and no projections (``perspective`` reads it). Errors
carry a reason and record names, never record text, so they are safe to log.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import MappingProxyType

from .identity import InstanceRef, SourceRef, validate_segment
from .model import _nonempty

STANCES = ("believed", "suspected", "rejected")
RULED_STANDINGS = ("established", "refuted", "unknown")
STANDINGS = RULED_STANDINGS + ("reported", "unasserted")
SUBJECT_KINDS = ("event", "instance", "relation", "definition")


class KnowledgeError(ValueError):
    """A record or ledger that would break an epistemic invariant: fail closed.

    ``records`` names the offending records as ``"<family>:<id>"`` so a store
    can quarantine them; families are event, proposition, ruling, evidence,
    belief and disclosure.
    """

    def __init__(self, reason: str, records: Iterable[str] = ()) -> None:
        self.reason = reason
        self.records: tuple[str, ...] = tuple(records)
        super().__init__(f"{reason}: {', '.join(self.records)}" if self.records else reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.records))


def _is_segment(value: object) -> bool:
    try:
        validate_segment(value, "id")
    except ValueError:
        return False
    return True


def _name(family: str, record_id: object) -> tuple[str, ...]:
    return (f"{family}:{record_id}",) if _is_segment(record_id) else ()


def _guarded(record: object, family: str, record_id: object, check) -> None:
    """Run field checks; a plain ValueError becomes a text-free ``invalid_record``.

    The replacement is raised outside the ``except`` block so neither its
    cause nor its context can carry the rejected value.
    """
    try:
        check()
    except KnowledgeError:
        raise
    except (ValueError, TypeError):
        pass
    else:
        return
    raise KnowledgeError("invalid_record", _name(family, record_id))


def _strict_bool(value: object) -> None:
    if not isinstance(value, bool):
        raise ValueError("expected a bool")  # "false" must never read as true


def _choice(value: object, options: tuple[str, ...]) -> None:
    if value not in options:
        raise ValueError("unknown option")


def _optional_text(value: object) -> None:
    if value is not None:
        _nonempty(value, "label")


def _moment(value: object) -> None:
    if not isinstance(value, Moment):
        raise ValueError("expected a Moment")


def _actor(value: object, campaign_id: str, name: tuple[str, ...]) -> None:
    if not isinstance(value, InstanceRef):
        raise ValueError("expected an InstanceRef")
    if value.campaign_id != campaign_id:
        raise KnowledgeError("campaign_mismatch", name)


def _segments(owner: object, label: str) -> None:
    if isinstance(getattr(owner, label), str):
        raise ValueError(f"{label} must be a collection of ids, not one string")
    values = tuple(getattr(owner, label))
    for value in values:
        validate_segment(value, label)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} repeats an id")
    object.__setattr__(owner, label, values)


@dataclass(frozen=True, slots=True)
class Moment:
    """A position in campaign order; only ``sequence`` orders, labels describe."""

    sequence: int
    scene_id: str | None = None
    turn_id: str | None = None

    def __post_init__(self) -> None:
        def check() -> None:
            if type(self.sequence) is not int or self.sequence < 0:
                raise ValueError("sequence must be a non-negative int")
            _optional_text(self.scene_id)
            _optional_text(self.turn_id)
        _guarded(self, "moment", None, check)


@dataclass(frozen=True, slots=True)
class Subject:
    """A typed canonical thing: what a proposition overtly refers to, or a ruling rests on.

    ``event`` and ``relation`` refs are ID segments (relations belong to the
    typed graph, 017); ``instance`` and ``definition`` refs are exact
    ``InstanceRef``/``SourceRef`` text.
    """

    kind: str
    ref: str

    def __post_init__(self) -> None:
        def check() -> None:
            _choice(self.kind, SUBJECT_KINDS)
            if self.kind == "instance":
                InstanceRef.parse(self.ref)
            elif self.kind == "definition":
                SourceRef.parse(self.ref)
            else:
                validate_segment(self.ref, "ref")
        _guarded(self, "subject", None, check)

    def __str__(self) -> str:
        return f"{self.kind}:{self.ref}"

    @property
    def campaign_id(self) -> str | None:
        return InstanceRef.parse(self.ref).campaign_id if self.kind == "instance" else None


def _subjects(owner: object, label: str, campaign_id: str, name: tuple[str, ...]) -> None:
    values = tuple(getattr(owner, label))
    for value in values:
        if not isinstance(value, Subject):
            raise ValueError("expected a Subject")
        if value.campaign_id not in (None, campaign_id):
            raise KnowledgeError("campaign_mismatch", name)
    object.__setattr__(owner, label, values)


def _common(record: object, family: str, record_id: str) -> tuple[str, ...]:
    validate_segment(record.campaign_id, "campaign_id")
    validate_segment(record_id, "id")
    _moment(record.moment)
    _nonempty(record.provenance, "provenance")
    return _name(family, record_id)


@dataclass(frozen=True, slots=True)
class Event:
    """An accepted objective happening. Participants are description, not access."""

    campaign_id: str
    event_id: str
    summary: str
    moment: Moment
    provenance: str
    participants: tuple[InstanceRef, ...] = ()

    def __post_init__(self) -> None:
        def check() -> None:
            name = _common(self, "event", self.event_id)
            _nonempty(self.summary, "summary")
            object.__setattr__(self, "participants", tuple(self.participants))
            for participant in self.participants:
                _actor(participant, self.campaign_id, name)
        _guarded(self, "event", self.event_id, check)


@dataclass(frozen=True, slots=True)
class Proposition:
    """Something that may be asserted or believed; it carries no truth value.

    ``about`` lists only what the text *overtly* refers to, recognisably to
    anyone who holds it ("the masked man" is not ``instance:duke``): it decides
    what a holder's items are about and which evidence is related support.
    """

    campaign_id: str
    proposition_id: str
    text: str
    moment: Moment
    provenance: str
    about: tuple[Subject, ...] = ()

    def __post_init__(self) -> None:
        def check() -> None:
            name = _common(self, "proposition", self.proposition_id)
            _nonempty(self.text, "text")
            _subjects(self, "about", self.campaign_id, name)
        _guarded(self, "proposition", self.proposition_id, check)


@dataclass(frozen=True, slots=True)
class Ruling:
    """A director/adjudication decision on a proposition's canonical standing."""

    campaign_id: str
    ruling_id: str
    proposition_id: str
    standing: str
    moment: Moment
    provenance: str
    grounds: tuple[Subject, ...] = ()

    def __post_init__(self) -> None:
        def check() -> None:
            name = _common(self, "ruling", self.ruling_id)
            validate_segment(self.proposition_id, "proposition_id")
            _choice(self.standing, RULED_STANDINGS)
            _subjects(self, "grounds", self.campaign_id, name)
            if self.standing == "established" and not self.grounds:
                raise KnowledgeError("ungrounded_ruling", name)
        _guarded(self, "ruling", self.ruling_id, check)


def _evidence_fields(record: object) -> tuple[str, ...]:
    name = _common(record, "evidence", record.evidence_id)
    _actor(record.holder, record.campaign_id, name)
    validate_segment(record.channel, "channel")
    _strict_bool(record.confirmed)
    return name


@dataclass(frozen=True, slots=True)
class Perception:
    """The holder sensed part of an event. ``percept`` is sensory register only."""

    campaign_id: str
    evidence_id: str
    holder: InstanceRef
    event_id: str
    channel: str
    percept: str
    moment: Moment
    provenance: str
    confirmed: bool

    def __post_init__(self) -> None:
        def check() -> None:
            _evidence_fields(self)
            validate_segment(self.event_id, "event_id")
            _nonempty(self.percept, "percept")
        _guarded(self, "evidence", self.evidence_id, check)


@dataclass(frozen=True, slots=True)
class Report:
    """The holder received an assertion at ``moment``; receipt is not truth.

    ``sender`` is the actual originator (director-only); ``attributed_to`` is
    who the holder takes the source to be (``None``: anonymous). ``addressed``
    means the sender knowingly sent it to this holder, at ``sent_at``.
    """

    campaign_id: str
    evidence_id: str
    holder: InstanceRef
    proposition_id: str
    channel: str
    moment: Moment
    provenance: str
    confirmed: bool
    sender: InstanceRef | None = None
    attributed_to: InstanceRef | None = None
    addressed: bool = False
    sent_at: Moment | None = None

    def __post_init__(self) -> None:
        def check() -> None:
            name = _evidence_fields(self)
            validate_segment(self.proposition_id, "proposition_id")
            for label in ("sender", "attributed_to"):
                if getattr(self, label) is not None:
                    _actor(getattr(self, label), self.campaign_id, name)
            _strict_bool(self.addressed)
            if self.sent_at is not None:
                _moment(self.sent_at)
            if self.addressed:
                valid = (self.sender is not None and self.sent_at is not None
                         and self.sent_at.sequence <= self.moment.sequence)
            else:
                valid = self.sent_at is None
            if not valid:
                raise KnowledgeError("invalid_sent_at", name)
        _guarded(self, "evidence", self.evidence_id, check)


Evidence = Perception | Report


@dataclass(frozen=True, slots=True)
class Belief:
    """The holder's own conclusion about a proposition; never promoted to truth.

    ``authored_by`` is a controller id; a PC belief also names the player
    contribution that backs it. See ``perspective.admit_belief``.
    """

    campaign_id: str
    belief_id: str
    holder: InstanceRef
    proposition_id: str
    stance: str
    moment: Moment
    authored_by: str
    evidence_ids: tuple[str, ...]
    supersedes: str | None = None
    contribution_id: str | None = None

    def __post_init__(self) -> None:
        def check() -> None:
            validate_segment(self.campaign_id, "campaign_id")
            validate_segment(self.belief_id, "id")
            _moment(self.moment)
            _actor(self.holder, self.campaign_id, _name("belief", self.belief_id))
            validate_segment(self.proposition_id, "proposition_id")
            _choice(self.stance, STANCES)
            _nonempty(self.authored_by, "authored_by")
            _segments(self, "evidence_ids")
            if self.supersedes is not None:
                validate_segment(self.supersedes, "supersedes")
                if self.supersedes == self.belief_id:
                    raise ValueError("a belief cannot supersede itself")
            _optional_text(self.contribution_id)
        _guarded(self, "belief", self.belief_id, check)


@dataclass(frozen=True, slots=True)
class Disclosure:
    """From ``moment`` the table may read an event out of character; no actor learns it."""

    campaign_id: str
    disclosure_id: str
    event_id: str
    moment: Moment
    provenance: str

    def __post_init__(self) -> None:
        def check() -> None:
            _common(self, "disclosure", self.disclosure_id)
            validate_segment(self.event_id, "event_id")
        _guarded(self, "disclosure", self.disclosure_id, check)


def _proxy(groups: dict) -> MappingProxyType:
    return MappingProxyType({key: tuple(value) for key, value in groups.items()})


@dataclass(frozen=True, slots=True)
class _Index:
    events: MappingProxyType
    propositions: MappingProxyType
    evidence: MappingProxyType
    beliefs: MappingProxyType
    evidence_by_holder: MappingProxyType
    beliefs_by_holder: MappingProxyType
    sent_by: MappingProxyType
    rulings_by_proposition: MappingProxyType
    reports_by_proposition: MappingProxyType
    superseded_by: MappingProxyType


_FAMILIES = (
    ("events", Event, "event_id"),
    ("propositions", Proposition, "proposition_id"),
    ("rulings", Ruling, "ruling_id"),
    ("evidence", (Perception, Report), "evidence_id"),
    ("beliefs", Belief, "belief_id"),
    ("disclosures", Disclosure, "disclosure_id"),
)


@dataclass(frozen=True, slots=True)
class Knowledge:
    """One campaign's validated, immutable epistemic ledger.

    Fields are tuples sorted by ID, so equal record sets compare equal however
    they were supplied. Validation runs family by family (events,
    propositions, rulings, evidence, beliefs, disclosures): an identity pass
    (campaign, duplicates), then each record's rules in ID order, then the
    belief chain checks closing the belief family. A type check of every input
    precedes all of it. The first failure is raised, independent of input order.
    """

    campaign_id: str
    events: tuple[Event, ...] = ()
    propositions: tuple[Proposition, ...] = ()
    rulings: tuple[Ruling, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    beliefs: tuple[Belief, ...] = ()
    disclosures: tuple[Disclosure, ...] = ()
    _index: _Index | None = field(default=None, init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not _is_segment(self.campaign_id):
            raise KnowledgeError("invalid_record")
        for attribute, kinds, id_field in _FAMILIES:
            supplied = getattr(self, attribute)
            if isinstance(supplied, (str, bytes)) or not isinstance(supplied, Iterable):
                raise KnowledgeError("invalid_record")
            records = tuple(supplied)
            if not all(isinstance(record, kinds) for record in records):
                raise KnowledgeError("invalid_record")
            object.__setattr__(self, attribute,
                               tuple(sorted(records, key=lambda r, f=id_field: getattr(r, f))))
        object.__setattr__(self, "_index", _validate(self))

    def __reduce__(self) -> tuple[object, ...]:
        return (Knowledge, (self.campaign_id, self.events, self.propositions, self.rulings,
                            self.evidence, self.beliefs, self.disclosures))

    # Lookups for ``perspective``. Actor-facing consumers read projections, not these.
    def event(self, event_id: str) -> Event:
        return self._lookup(self._index.events, event_id, "unknown_event", "event")

    def proposition(self, proposition_id: str) -> Proposition:
        return self._lookup(self._index.propositions, proposition_id, "unknown_proposition",
                            "proposition")

    def subjects_of(self, item: Evidence) -> frozenset[Subject]:
        if self._index.evidence.get(getattr(item, "evidence_id", None)) is not item:
            raise KnowledgeError("unknown_evidence", _name("evidence", getattr(item, "evidence_id", None)))
        return _subjects_of(item, self._index.propositions)

    def evidence_held_by(self, holder: InstanceRef) -> tuple[Evidence, ...]:
        return self._index.evidence_by_holder.get(holder, ())

    def beliefs_held_by(self, holder: InstanceRef) -> tuple[Belief, ...]:
        return self._index.beliefs_by_holder.get(holder, ())

    def reports_sent_by(self, sender: InstanceRef) -> tuple[Report, ...]:
        """Addressed reports only: what the sender knowingly sent."""
        return self._index.sent_by.get(sender, ())

    def rulings_on(self, proposition_id: str) -> tuple[Ruling, ...]:
        return self._index.rulings_by_proposition.get(proposition_id, ())

    def reports_of(self, proposition_id: str) -> tuple[Report, ...]:
        return self._index.reports_by_proposition.get(proposition_id, ())

    def successor_of(self, belief_id: str) -> Belief | None:
        return self._index.superseded_by.get(belief_id)

    @staticmethod
    def _lookup(table, record_id: object, reason: str, family: str):
        if not _is_segment(record_id) or record_id not in table:
            raise KnowledgeError(reason, _name(family, record_id))
        return table[record_id]


def _subjects_of(item: Evidence, propositions) -> frozenset[Subject]:
    """What an evidence item is about: a perception's event, a report's proposition's ``about``."""
    if isinstance(item, Perception):
        return frozenset({Subject("event", item.event_id)})
    return frozenset(propositions[item.proposition_id].about)


def _identity_pass(knowledge: Knowledge, attribute: str, family: str, id_field: str) -> dict:
    seen: dict = {}
    for record in getattr(knowledge, attribute):
        record_id = getattr(record, id_field)
        name = (f"{family}:{record_id}",)
        if record.campaign_id != knowledge.campaign_id:
            raise KnowledgeError("campaign_mismatch", name)
        if record_id in seen:
            raise KnowledgeError("duplicate_id", name)
        seen[record_id] = record
    return seen


def _require(found: bool, reason: str, *records: str) -> None:
    if not found:
        raise KnowledgeError(reason, records)


def _event_subjects(subjects: Iterable[Subject], events: dict, reason_name: str) -> None:
    for subject in subjects:
        if subject.kind == "event":
            _require(subject.ref in events, "unknown_event", reason_name)


def _validate(knowledge: Knowledge) -> _Index:
    events = _identity_pass(knowledge, "events", "event", "event_id")

    propositions = _identity_pass(knowledge, "propositions", "proposition", "proposition_id")
    for proposition in knowledge.propositions:
        name = f"proposition:{proposition.proposition_id}"
        _event_subjects(proposition.about, events, name)
        _require(all(events[s.ref].moment.sequence <= proposition.moment.sequence
                     for s in proposition.about if s.kind == "event"), "about_future_event", name)

    _identity_pass(knowledge, "rulings", "ruling", "ruling_id")
    rulings_by_proposition: dict = defaultdict(list)
    for ruling in knowledge.rulings:
        name = f"ruling:{ruling.ruling_id}"
        proposition = propositions.get(ruling.proposition_id)
        _require(proposition is not None, "unknown_proposition", name)
        _require(ruling.moment.sequence >= proposition.moment.sequence, "before_proposition", name)
        _event_subjects(ruling.grounds, events, name)
        _require(all(events[s.ref].moment.sequence <= ruling.moment.sequence
                     for s in ruling.grounds if s.kind == "event"), "ruling_cites_future", name)
        for earlier in rulings_by_proposition[ruling.proposition_id]:
            _require(earlier.moment.sequence != ruling.moment.sequence, "conflicting_rulings",
                     *sorted((f"ruling:{earlier.ruling_id}", name)))
        rulings_by_proposition[ruling.proposition_id].append(ruling)

    evidence = _identity_pass(knowledge, "evidence", "evidence", "evidence_id")
    evidence_by_holder: dict = defaultdict(list)
    sent_by: dict = defaultdict(list)
    reports_by_proposition: dict = defaultdict(list)
    for item in knowledge.evidence:
        name = f"evidence:{item.evidence_id}"
        if isinstance(item, Perception):
            event = events.get(item.event_id)
            _require(event is not None, "unknown_event", name)
            _require(item.moment.sequence >= event.moment.sequence, "before_event", name)
        else:
            proposition = propositions.get(item.proposition_id)
            _require(proposition is not None, "unknown_proposition", name)
            earliest = (item.sent_at or item.moment).sequence
            _require(earliest >= proposition.moment.sequence, "before_proposition", name)
            reports_by_proposition[item.proposition_id].append(item)
            if item.addressed:
                sent_by[item.sender].append(item)
        evidence_by_holder[item.holder].append(item)

    beliefs = _identity_pass(knowledge, "beliefs", "belief", "belief_id")
    contributions: set = set()
    for belief in knowledge.beliefs:
        _check_belief(belief, propositions, evidence, beliefs, contributions)
    superseded_by = _check_chains(knowledge.beliefs)
    beliefs_by_holder: dict = defaultdict(list)
    for belief in knowledge.beliefs:
        beliefs_by_holder[belief.holder].append(belief)

    _identity_pass(knowledge, "disclosures", "disclosure", "disclosure_id")
    for disclosure in knowledge.disclosures:
        name = f"disclosure:{disclosure.disclosure_id}"
        event = events.get(disclosure.event_id)
        _require(event is not None, "unknown_event", name)
        _require(disclosure.moment.sequence >= event.moment.sequence, "before_event", name)

    return _Index(
        events=MappingProxyType(events), propositions=MappingProxyType(propositions),
        evidence=MappingProxyType(evidence), beliefs=MappingProxyType(beliefs),
        evidence_by_holder=_proxy(evidence_by_holder), beliefs_by_holder=_proxy(beliefs_by_holder),
        sent_by=_proxy(sent_by), rulings_by_proposition=_proxy(rulings_by_proposition),
        reports_by_proposition=_proxy(reports_by_proposition),
        superseded_by=MappingProxyType(superseded_by),
    )


def _check_belief(belief: Belief, propositions: dict, evidence: dict,
                  beliefs: dict, contributions: set) -> None:
    name = f"belief:{belief.belief_id}"
    proposition = propositions.get(belief.proposition_id)
    _require(proposition is not None, "unknown_proposition", name)
    _require(belief.moment.sequence >= proposition.moment.sequence, "before_proposition", name)
    _require(bool(belief.evidence_ids), "unsupported_belief", name)
    cited = []
    for evidence_id in belief.evidence_ids:
        item = evidence.get(evidence_id)
        _require(item is not None, "unknown_evidence", name, f"evidence:{evidence_id}")
        cited.append(item)
    for item in cited:
        _require(item.holder == belief.holder, "foreign_evidence", name, f"evidence:{item.evidence_id}")
    for item in cited:
        _require(item.confirmed, "unconfirmed_support", name, f"evidence:{item.evidence_id}")
    for item in cited:
        _require(item.moment.sequence <= belief.moment.sequence, "future_evidence", name,
                 f"evidence:{item.evidence_id}")
    about = set(proposition.about)
    _require(any((isinstance(item, Report) and item.proposition_id == belief.proposition_id)
                 or _subjects_of(item, propositions) & about
                 for item in cited), "unrelated_support", name)
    if belief.supersedes is not None:
        target = beliefs.get(belief.supersedes)
        _require(target is not None, "unknown_belief", name, f"belief:{belief.supersedes}")
        _require(target.holder == belief.holder and target.proposition_id == belief.proposition_id
                 and target.moment.sequence < belief.moment.sequence,
                 "invalid_supersession", name, f"belief:{target.belief_id}")
    if belief.contribution_id is not None:
        key = (belief.holder, belief.contribution_id)
        _require(key not in contributions, "reused_contribution", name)
        contributions.add(key)


def _check_chains(beliefs: tuple[Belief, ...]) -> dict:
    """Per (holder, proposition): one root and no forks, so exactly one current belief."""
    superseded_by: dict = {}
    for belief in beliefs:
        if belief.supersedes is None:
            continue
        earlier = superseded_by.get(belief.supersedes)
        if earlier is not None:
            raise KnowledgeError("forked_supersession",
                                 sorted((f"belief:{earlier.belief_id}", f"belief:{belief.belief_id}")))
        superseded_by[belief.supersedes] = belief
    roots: dict = defaultdict(list)
    for belief in beliefs:
        if belief.supersedes is None:
            roots[(belief.holder, belief.proposition_id)].append(belief)
    for group in roots.values():
        _require(len(group) == 1, "unsuperseded_conflict",
                 *sorted(f"belief:{b.belief_id}" for b in group))
    return superseded_by
