"""Bounded conjecture history, deliberately separate from the Knowledge ledger (060).

A hypothesis says what its owner imagines, not what the target actually believes.
These pure values have no persistence or provider dependency. Admission checks
live roster, authority and evidence; constructors validate structural history.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
from math import isfinite

from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment

MAX_RECORDS = 64
MAX_RECALL = 8
MAX_EVIDENCE = 8
TEXT_CHARS = 400


class HypothesisError(ValueError):
    """Stable refusal reason, never authored text or private evidence."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def segment(value: object) -> None:
    try:
        validate_segment(value, 'id')
    except (ValueError, TypeError):
        raise HypothesisError('invalid_id') from None


def text(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value) > TEXT_CHARS:
        raise HypothesisError('invalid_text')


def probability(value: object) -> None:
    if type(value) not in (int, float) or not 0 <= value <= 1 or not isfinite(value):
        raise HypothesisError('invalid_confidence')


def bounded(values: object, maximum: int) -> tuple:
    if isinstance(values, (str, bytes, Mapping)):
        raise HypothesisError('invalid_collection')
    try:
        result = tuple(islice(iter(values), maximum + 1))
    except TypeError:
        raise HypothesisError('invalid_collection') from None
    if len(result) > maximum:
        raise HypothesisError('budget_exceeded')
    return result


def sequence(value: object) -> None:
    if type(value) is not int or value < 0:
        raise HypothesisError('invalid_sequence')


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    owner: InstanceRef
    inferred_actors: tuple[InstanceRef, ...]
    claim: str
    confidence: float
    evidence_ids: tuple[str, ...]
    moment: Moment
    provenance: str
    supersedes: str | None = None

    def __post_init__(self):
        segment(self.hypothesis_id)
        if type(self.owner) is not InstanceRef or type(self.moment) is not Moment:
            raise HypothesisError('invalid_identity_or_moment')
        actors = bounded(self.inferred_actors, 2)
        if not actors or any(type(a) is not InstanceRef for a in actors):
            raise HypothesisError('invalid_depth_or_actor')
        if any(a.campaign_id != self.owner.campaign_id for a in actors):
            raise HypothesisError('campaign_mismatch')
        evidence = bounded(self.evidence_ids, MAX_EVIDENCE)
        if not evidence:
            raise HypothesisError('unsupported_hypothesis')
        for item in evidence:
            segment(item)
        if len(set(evidence)) != len(evidence):
            raise HypothesisError('duplicate_evidence')
        text(self.claim)
        text(self.provenance)
        probability(self.confidence)
        if self.supersedes is not None:
            segment(self.supersedes)
        object.__setattr__(self, 'inferred_actors', actors)
        object.__setattr__(self, 'evidence_ids', evidence)


@dataclass(frozen=True, slots=True)
class HypothesisLedger:
    """Append-only in-memory history. Revision equals retained admission count.

    Sequence order is per revision chain; independent hypotheses may be authored
    out of chronological order. The record index is also its original expected
    revision, which makes retries distinguish a replay from a fresh request.
    """
    campaign_id: str
    records: tuple[Hypothesis, ...] = ()

    def __post_init__(self):
        segment(self.campaign_id)
        records = bounded(self.records, MAX_RECORDS)
        seen, superseded = {}, set()
        for record in records:
            if type(record) is not Hypothesis:
                raise HypothesisError('invalid_record')
            if record.owner.campaign_id != self.campaign_id:
                raise HypothesisError('campaign_mismatch')
            if record.hypothesis_id in seen:
                raise HypothesisError('duplicate_id')
            if record.supersedes is not None:
                previous = seen.get(record.supersedes)
                if previous is None or record.supersedes in superseded:
                    raise HypothesisError('stale_supersession')
                if (record.owner, record.inferred_actors) != (previous.owner, previous.inferred_actors):
                    raise HypothesisError('different_chain')
                if record.moment.sequence <= previous.moment.sequence:
                    raise HypothesisError('nonmonotonic_revision')
                superseded.add(record.supersedes)
            seen[record.hypothesis_id] = record
        object.__setattr__(self, 'records', records)

    @property
    def revision(self) -> int:
        return len(self.records)


@dataclass(frozen=True, slots=True)
class TacticChoice:
    owner: InstanceRef
    hypothesis_id: str
    confidence: float
    evidence_ids: tuple[str, ...]
    tactic: str
    credited: bool
