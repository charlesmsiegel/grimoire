"""World authoring proposals (087): bounded suggested records a human reviews, accepts in part or discards.

A ``Proposal`` is a *suggestion*, never canon. It names a few candidate records (places and groups)
for one world source and the links between them: roads joining two places, and a place lying
within another. Candidates and links live only in memory. Nothing here reads or writes a library:
``grimoire.authoringstore`` reviews a proposal against the world, surfaces duplicates, and writes
exactly the accepted subset through a World Change Set (043). A rejected candidate, a disposable
brainstorm and a link to either are never stored (see docs/AUTHORING.md).

Values are pure and immutable, so what a reviewer reads is what is accepted. Every refusal is an
``AuthoringError`` with a stable ``reason`` that retains no authored text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from grimoire.core.identity import SourceRef, validate_segment, validate_source_id

#: Bounds on one proposal. A reviewer reads every candidate and link; past these, ask again.
MAX_CANDIDATES = 16
MAX_LINKS = 48
MAX_CONSTRAINTS = 16
NAME_MAX = 256
TEXT_MAX = 4000
BRIEF_MAX = 4000
#: Keys, link ids and proposal ids stay short so the ids derived from them (``authoring-<proposal>``,
#: ``<proposal>-<link>``) fit one 128-character segment without truncation or collision.
ID_MAX = 60

#: The record kinds a proposal may suggest, and whether a record of that kind is a place a road or a
#: containment may join. Other kinds are later work (the sprint's open question on which generated
#: record types are worth formalizing).
KINDS = {"location": True, "organization": False}
#: What a candidate is for. ``individual`` and ``template`` become world definitions of that
#: nature (009) when accepted. ``brainstorm`` is a disposable idea: shown, never storable.
ROLES = ("individual", "template", "brainstorm")
#: ``road``: a bidirectional way between two places. ``within``: the subject place lies inside the
#: object place (one parent per place, acyclic). Both are written to the world's topology (059), so
#: their candidate endpoints are individual places (or brainstorms, whose links are always dropped).
RELATIONS = ("road", "within")

#: Words a namesake check ignores, so "The Lamplighters' Guild" and "Lamplighters Guild" compare equal.
_STOP = frozenset({"the", "a", "an", "of"})
_POSSESSIVE = re.compile(r"['’]s\b")
_WORD = re.compile(r"\w+")


class AuthoringError(ValueError):
    """A proposal, decision or reply that cannot be represented or accepted; ``reason`` is stable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason,))


def _segment(value: object, reason: str, limit: int | None = None) -> str:
    try:
        validate_segment(value, "segment")
    except (ValueError, TypeError):
        raise AuthoringError(reason) from None
    if limit is not None and len(value) > limit:  # type: ignore[arg-type]
        raise AuthoringError(reason)
    return value  # type: ignore[return-value]


def _text(value: object, limit: int, *, empty: bool = False) -> str:
    if type(value) is not str or len(value) > limit or (not empty and not value.strip()):
        raise AuthoringError("invalid_text")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):  # not encodable as UTF-8
        raise AuthoringError("invalid_text")
    return value


def _fold(name: str) -> str:
    """NFKC, casefolded, with accents removed (so "Café" and "Cafe" compare equal)."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name).casefold())
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if not unicodedata.combining(c)))


def _stem(word: str) -> str:
    """A trailing plural ``s`` dropped from a longer word, so "Lamplighter's" meets "Lamplighters"."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def name_key(name: str) -> tuple[str, ...]:
    """What two names share when a reviewer should be told they may be one record.

    Folded (NFKC, case, accents), possessive ``'s``, punctuation and a plural ``s`` dropped, the words
    ``the``, ``a``, ``an`` and ``of`` ignored, the rest as a sorted set. This deliberately leans to
    flagging: a false match costs the reviewer one ``distinct`` mark, while a missed one would store
    a second record. It only *suggests* a duplicate; names never establish identity."""
    folded = _POSSESSIVE.sub("", _fold(name))
    words = {_stem(word) for word in _WORD.findall(folded)}
    kept = words - _STOP
    return tuple(sorted(kept or words)) or (folded.strip(),)


@dataclass(frozen=True)
class AuthoringRequest:
    """What a human asked for: a brief, explicit constraints and a bound on how much to suggest."""

    request_id: str
    source: str
    brief: str
    constraints: tuple[str, ...] = ()
    max_candidates: int = 8

    def __post_init__(self) -> None:
        _segment(self.request_id, "invalid_request_id")
        try:
            validate_source_id(self.source)
        except (ValueError, TypeError):
            raise AuthoringError("invalid_source") from None
        if not self.source.startswith("world:"):  # a shared character or add-on would carry the towns everywhere
            raise AuthoringError("invalid_source")
        _text(self.brief, BRIEF_MAX)
        if type(self.constraints) is not tuple or len(self.constraints) > MAX_CONSTRAINTS:
            raise AuthoringError("invalid_constraints")
        for constraint in self.constraints:
            _text(constraint, TEXT_MAX)
        if len(set(self.constraints)) != len(self.constraints):
            raise AuthoringError("duplicate_constraint")
        if type(self.max_candidates) is not int or not 1 <= self.max_candidates <= MAX_CANDIDATES:
            raise AuthoringError("invalid_bound")


@dataclass(frozen=True)
class WorldRecord:
    """One existing record of the world, as offered to a proposer: its head pinned, kind and name."""

    ref: SourceRef
    kind: str
    name: str

    def __post_init__(self) -> None:
        if type(self.ref) is not SourceRef or not self.ref.pinned or self.ref.kind != self.kind:
            raise AuthoringError("invalid_record")
        _text(self.name, NAME_MAX)


@dataclass(frozen=True)
class AuthoringContext:
    """Source-aware retrieval for one request: the world's title and a bounded list of its records.

    ``truncated`` says the world has more records than were offered. ``basis`` is the source
    manifest's etag when it was read; a review is judged against the library as it is then, not this."""

    source: str
    title: str | None
    records: tuple[WorldRecord, ...]
    truncated: bool
    basis: str

    def __post_init__(self) -> None:
        if type(self.records) is not tuple or any(type(r) is not WorldRecord for r in self.records):
            raise AuthoringError("invalid_context")
        if any(record.ref.source != self.source for record in self.records):
            raise AuthoringError("foreign_record")
        if self.title is not None:
            _text(self.title, NAME_MAX)

    def record(self, text: str) -> WorldRecord:
        """The offered record whose pinned reference reads exactly ``text``."""
        for record in self.records:
            if str(record.ref) == text:
                return record
        raise AuthoringError("unknown_record")


@dataclass(frozen=True)
class Candidate:
    """One suggested record. ``key`` names it inside its proposal only; the stored entity id is
    allocated when it is accepted, so a key never claims an identity in the world."""

    key: str
    kind: str
    name: str
    role: str
    description: str = ""

    def __post_init__(self) -> None:
        _segment(self.key, "invalid_key", ID_MAX)
        if self.kind not in KINDS:
            raise AuthoringError("unsupported_kind")
        _text(self.name, NAME_MAX)
        if self.role not in ROLES:
            raise AuthoringError("invalid_role")
        _text(self.description, TEXT_MAX, empty=True)

    @property
    def is_place(self) -> bool:
        return KINDS[self.kind]

    @property
    def storable(self) -> bool:
        return self.role != "brainstorm"


@dataclass(frozen=True)
class Link:
    """A suggested road or containment. ``subject`` is a candidate key. ``object`` is a candidate key
    or a pinned reference to an existing record of the proposal's own world."""

    link_id: str
    relation: str
    subject: str
    object: str | SourceRef

    def __post_init__(self) -> None:
        _segment(self.link_id, "invalid_link_id", ID_MAX)
        if self.relation not in RELATIONS:
            raise AuthoringError("unsupported_relation")
        _segment(self.subject, "invalid_endpoint")
        if type(self.object) is SourceRef:
            if not self.object.pinned:
                raise AuthoringError("unpinned_endpoint")
        else:
            _segment(self.object, "invalid_endpoint")
            if self.object == self.subject:
                raise AuthoringError("self_link")

    @property
    def keys(self) -> tuple[str, ...]:
        """The candidate keys this link joins."""
        return (self.subject,) if type(self.object) is SourceRef else (self.subject, self.object)  # type: ignore[return-value]


@dataclass(frozen=True)
class Proposal:
    """A bounded set of suggested records and links for one world; never canon until accepted.

    ``origin`` is attribution text (``model:planning``, ``human:owner``): it says who suggested the
    proposal, and grants nothing."""

    proposal_id: str
    request: AuthoringRequest
    candidates: tuple[Candidate, ...]
    links: tuple[Link, ...] = ()
    rationale: str = ""
    origin: str = "unattributed"

    def __post_init__(self) -> None:
        _segment(self.proposal_id, "invalid_proposal_id", ID_MAX)
        if type(self.request) is not AuthoringRequest:
            raise AuthoringError("invalid_request")
        if type(self.candidates) is not tuple or any(type(c) is not Candidate for c in self.candidates):
            raise AuthoringError("invalid_candidates")
        if not 1 <= len(self.candidates) <= self.request.max_candidates:
            raise AuthoringError("candidate_bound")
        if type(self.links) is not tuple or any(type(link) is not Link for link in self.links):
            raise AuthoringError("invalid_links")
        if len(self.links) > MAX_LINKS:
            raise AuthoringError("link_bound")
        _text(self.rationale, TEXT_MAX, empty=True)
        _text(self.origin, NAME_MAX)
        by_key = {candidate.key: candidate for candidate in self.candidates}
        if len(by_key) != len(self.candidates):
            raise AuthoringError("duplicate_key")
        if len({link.link_id for link in self.links}) != len(self.links):
            raise AuthoringError("duplicate_link_id")
        roads: set[frozenset[str]] = set()
        parents: dict[str, str | SourceRef] = {}
        for link in self.links:
            for key in link.keys:
                candidate = by_key.get(key)
                if candidate is None:
                    raise AuthoringError("unknown_endpoint")
                if not candidate.is_place:
                    raise AuthoringError("endpoint_not_place")
                if candidate.role == "template":  # a generic kind of place is on no map
                    raise AuthoringError("template_endpoint")
            if type(link.object) is SourceRef and link.object.source != self.request.source:
                raise AuthoringError("foreign_endpoint")
            if link.relation == "road":
                pair = frozenset((link.subject, str(link.object)))
                if pair in roads:
                    raise AuthoringError("duplicate_road")
                roads.add(pair)
            else:
                if link.subject in parents:
                    raise AuthoringError("two_parents")
                parents[link.subject] = link.object
        for start in parents:  # containment among candidates must not loop
            seen, at = {start}, parents.get(start)
            while type(at) is str:
                if at in seen:
                    raise AuthoringError("containment_cycle")
                seen.add(at)
                at = parents.get(at)

    def candidate(self, key: str) -> Candidate:
        for candidate in self.candidates:
            if candidate.key == key:
                return candidate
        raise AuthoringError("unknown_candidate")


@dataclass(frozen=True)
class Duplicate:
    """A candidate whose name matches another record of the same kind: an existing world record
    (``existing``) or another candidate of the same proposal (``other``). It is surfaced, not merged."""

    candidate: str
    existing: SourceRef | None = None
    other: str | None = None
    existing_name: str = ""


@dataclass(frozen=True)
class LinkProblem:
    """A link the world can no longer carry: its existing endpoint is gone (``missing_endpoint``), has
    moved to another revision (``endpoint_moved``), is not a place (``not_a_place``) or is a generic
    template place (``template_endpoint``)."""

    link_id: str
    reason: str


@dataclass(frozen=True)
class Decision:
    """A human's choice on one reviewed proposal: which candidates become records.

    ``distinct`` names flagged duplicates the reviewer deliberately keeps as separate records (a
    namesake is not an identity). ``decided_by`` is attribution of the human who chose."""

    accept: tuple[str, ...]
    decided_by: str
    distinct: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for keys in (self.accept, self.distinct):
            if type(keys) is not tuple:
                raise AuthoringError("invalid_decision")
            for key in keys:
                _segment(key, "invalid_decision")
            if len(set(keys)) != len(keys):
                raise AuthoringError("invalid_decision")
        if not self.accept:
            raise AuthoringError("nothing_accepted")
        if not set(self.distinct) <= set(self.accept):
            raise AuthoringError("distinct_not_accepted")
        _text(self.decided_by, NAME_MAX)


@dataclass(frozen=True)
class Selection:
    """What a decision accepts, keeps and drops, before any identity is allocated.

    ``dropped`` pairs each link that will not be written with why: ``rejected_endpoint`` (a candidate
    it joins was not accepted, or is a brainstorm) or a ``LinkProblem`` reason. ``surfaced`` are the
    duplicates of candidates that were not accepted: what a reviewer was shown instead of a record."""

    accepted: tuple[Candidate, ...]
    links: tuple[Link, ...]
    dropped: tuple[tuple[Link, str], ...]
    surfaced: tuple[Duplicate, ...]
    discarded: tuple[Candidate, ...]


def select(proposal: Proposal, decision: Decision, duplicates: tuple[Duplicate, ...],
           problems: tuple[LinkProblem, ...]) -> Selection:
    """Apply a decision to a reviewed proposal. Refuses an unknown candidate, a brainstorm
    (``brainstorm_not_storable``), a flagged duplicate the reviewer did not mark ``distinct``
    (``duplicate_unacknowledged``) and a ``distinct`` mark on a candidate nothing flagged
    (``distinct_not_flagged``), so every acceptance of a namesake is a deliberate choice."""
    if type(proposal) is not Proposal or type(decision) is not Decision:
        raise AuthoringError("invalid_decision")
    accepted = tuple(proposal.candidate(key) for key in decision.accept)
    if any(not candidate.storable for candidate in accepted):
        raise AuthoringError("brainstorm_not_storable")
    flagged = {duplicate.candidate for duplicate in duplicates}
    for key in decision.accept:
        if key in flagged and key not in decision.distinct:
            raise AuthoringError("duplicate_unacknowledged")
    if not set(decision.distinct) <= flagged:
        raise AuthoringError("distinct_not_flagged")
    keys = set(decision.accept)
    broken = {problem.link_id: problem.reason for problem in problems}
    kept, dropped = [], []
    for link in proposal.links:
        if link.link_id in broken:
            dropped.append((link, broken[link.link_id]))
        elif not set(link.keys) <= keys:
            dropped.append((link, "rejected_endpoint"))
        else:
            kept.append(link)
    order = {key: index for index, key in enumerate(decision.accept)}
    return Selection(
        accepted=tuple(sorted(accepted, key=lambda candidate: order[candidate.key])),
        links=tuple(kept), dropped=tuple(dropped),
        surfaced=tuple(d for d in duplicates if d.candidate not in keys),
        discarded=tuple(c for c in proposal.candidates if c.key not in keys))
