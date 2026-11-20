"""Private, asymmetric attitudes one actor holds toward another (078).

An ``Attitude`` is what its holder feels or concludes about one other actor or group on one
dimension: the envoy's resentment of the newcomer is not the newcomer's view of the envoy, and is
not the guild's. These pure values have no persistence or provider dependency. Admission
(``policy.admit``) checks the live roster, authority and the holder's own grounds; constructors
validate shape and chain history only.
"""
from __future__ import annotations

from collections.abc import Mapping, Set as AbstractSet
from dataclasses import dataclass
from itertools import islice

from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment

#: The first-class dimensions. Anything subtler is narrative, in ``account``.
DIMENSIONS = ('trust', 'affection', 'fear', 'rivalry', 'resentment', 'debt')
#: Dimensions whose optional degree runs from -3 to 3; the rest are intensities from 0 to 3.
BIPOLAR = frozenset({'trust', 'affection'})
MAX_DEGREE = 3
MAX_RECORDS = 256
MAX_GROUNDS = 8
TEXT_CHARS = 400


class RelationshipError(ValueError):
    """Stable refusal reason, never authored text or private evidence."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def segment(value: object) -> None:
    try:
        validate_segment(value, 'id')
    except (ValueError, TypeError):
        raise RelationshipError('invalid_id') from None


def text(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value) > TEXT_CHARS:
        raise RelationshipError('invalid_text')


def sequence(value: object) -> None:
    if type(value) is not int or value < 0:
        raise RelationshipError('invalid_sequence')


def bounded(values: object, maximum: int) -> tuple:
    """An ordered bounded collection; sets are refused, since their order depends on the hash seed."""
    if isinstance(values, (str, bytes, Mapping, AbstractSet)):
        raise RelationshipError('invalid_collection')
    try:
        result = tuple(islice(iter(values), maximum + 1))
    except TypeError:
        raise RelationshipError('invalid_collection') from None
    if len(result) > maximum:
        raise RelationshipError('budget_exceeded')
    return result


def _ids(values: object) -> tuple[str, ...]:
    """Grounds are a set in meaning: stored sorted, so equal grounds compare equal however supplied."""
    if isinstance(values, AbstractSet):  # order is irrelevant here: the result is sorted
        values = tuple(islice(values, MAX_GROUNDS + 1))
    result = bounded(values, MAX_GROUNDS)
    for item in result:
        segment(item)
    if len(set(result)) != len(result):
        raise RelationshipError('duplicate_ground')
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class Attitude:
    """One holder's situated attitude toward ``toward`` on one dimension, as of ``moment``.

    ``account`` is the holder's own narrative reading ("resents being called a lackey in front of
    the dockhands"); ``degree`` is an optional numeric interpretation, never required.
    ``evidence_ids`` and ``belief_ids`` name the holder's own 012 evidence and beliefs the change
    rests on (1 to 8 in all, stored sorted): its episode provenance. ``supersedes`` names the previous head of
    the same (holder, toward, dimension) chain.
    """

    attitude_id: str
    holder: InstanceRef
    toward: InstanceRef
    dimension: str
    account: str
    moment: Moment
    provenance: str
    evidence_ids: tuple[str, ...] = ()
    belief_ids: tuple[str, ...] = ()
    degree: int | None = None
    supersedes: str | None = None

    def __post_init__(self):
        segment(self.attitude_id)
        if type(self.holder) is not InstanceRef or type(self.toward) is not InstanceRef:
            raise RelationshipError('invalid_actor')
        if type(self.moment) is not Moment:
            raise RelationshipError('invalid_moment')
        if self.toward.campaign_id != self.holder.campaign_id:
            raise RelationshipError('campaign_mismatch')
        if self.toward == self.holder:
            raise RelationshipError('self_attitude')
        if type(self.dimension) is not str or self.dimension not in DIMENSIONS:
            raise RelationshipError('unknown_dimension')
        text(self.account)
        text(self.provenance)
        if self.degree is not None:
            low = -MAX_DEGREE if self.dimension in BIPOLAR else 0
            if type(self.degree) is not int or not low <= self.degree <= MAX_DEGREE:
                raise RelationshipError('invalid_degree')
        evidence, beliefs = _ids(self.evidence_ids), _ids(self.belief_ids)
        if not evidence and not beliefs:
            raise RelationshipError('ungrounded_attitude')
        if len(evidence) + len(beliefs) > MAX_GROUNDS:
            raise RelationshipError('budget_exceeded')
        if self.supersedes is not None:
            segment(self.supersedes)
        object.__setattr__(self, 'evidence_ids', evidence)
        object.__setattr__(self, 'belief_ids', beliefs)

    @property
    def key(self) -> tuple[InstanceRef, InstanceRef, str]:
        return self.holder, self.toward, self.dimension


@dataclass(frozen=True, slots=True)
class AttitudeLedger:
    """Append-only in-memory history; revision equals the retained admission count.

    Each (holder, toward, dimension) is one chain with exactly one current head: a record for a
    key that already has one must supersede that head, at a strictly later moment. Independent
    chains may be authored in any order. A record's index is its original expected revision,
    which lets a retry tell a replay from a fresh request.
    """

    campaign_id: str
    records: tuple[Attitude, ...] = ()

    def __post_init__(self):
        segment(self.campaign_id)
        records = bounded(self.records, MAX_RECORDS)
        seen: dict[str, Attitude] = {}
        heads: dict[tuple, str] = {}
        for record in records:
            if type(record) is not Attitude:
                raise RelationshipError('invalid_record')
            if record.holder.campaign_id != self.campaign_id:
                raise RelationshipError('campaign_mismatch')
            if record.attitude_id in seen:
                raise RelationshipError('duplicate_id')
            head = heads.get(record.key)
            if record.supersedes is None:
                if head is not None:
                    raise RelationshipError('unsuperseded_conflict')
            else:
                previous = seen.get(record.supersedes)
                if previous is None:
                    raise RelationshipError('stale_supersession')
                if previous.key != record.key:
                    raise RelationshipError('different_chain')
                if record.supersedes != head:
                    raise RelationshipError('stale_supersession')
                if record.moment.sequence <= previous.moment.sequence:
                    raise RelationshipError('nonmonotonic_revision')
            heads[record.key] = record.attitude_id
            seen[record.attitude_id] = record
        object.__setattr__(self, 'records', records)

    @property
    def revision(self) -> int:
        return len(self.records)


@dataclass(frozen=True, slots=True)
class Disposition:
    """A current attitude as its holder has it: no grounds, provenance, authorship or scene labels.

    ``toward`` is still a reference: a renderer (027's brief, a director panel) must show it as a
    name the holder knows it by, never as the reference text, because IDs never reach a prompt
    (012). ``since`` is the change's campaign sequence.
    """

    toward: InstanceRef
    dimension: str
    account: str
    degree: int | None
    since: int
