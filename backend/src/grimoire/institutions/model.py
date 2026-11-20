"""Bounded director-authored institutional decisions (057), separate from knowledge.

Membership remains a canonical graph relation; these immutable values never copy
it into another authority. Receipts retain agreeing preferences too, because a
retry of a different request must not masquerade as the original decision.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice

from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment


class InstitutionError(ValueError):
    """A text-free refusal, safe to display without exposing private preferences."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def sequence(value, maximum):
    if isinstance(value, (str, bytes, Mapping)):
        raise InstitutionError('invalid_field')
    try:
        result = tuple(islice(iter(value), maximum + 1))
    except TypeError:
        raise InstitutionError('invalid_field') from None
    if len(result) > maximum:
        raise InstitutionError('limit')
    return result


def text(value):
    if type(value) is not str or not value.strip() or len(value) > 400 or any(ord(c) < 32 for c in value):
        raise InstitutionError('invalid_field')


def segment(value):
    try:
        validate_segment(value, 'id')
    except (ValueError, TypeError):
        raise InstitutionError('invalid_field') from None


def reference(value):
    if type(value) is not InstanceRef:
        raise InstitutionError('identity')


@dataclass(frozen=True, slots=True)
class Institution:
    group: InstanceRef
    leader: InstanceRef
    goals: tuple[str, ...]
    policy: str

    def __post_init__(self):
        reference(self.group); reference(self.leader)
        if self.group.kind != 'group' or self.group.campaign_id != self.leader.campaign_id:
            raise InstitutionError('identity')
        goals = sequence(self.goals, 16)
        if not goals:
            raise InstitutionError('invalid_field')
        for goal in goals:
            text(goal)
        text(self.policy)
        object.__setattr__(self, 'goals', goals)


@dataclass(frozen=True, slots=True)
class MemberPreference:
    actor: InstanceRef
    proposal_id: str
    choice: str
    provenance: str

    def __post_init__(self):
        reference(self.actor); segment(self.proposal_id)
        text(self.choice); text(self.provenance)


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    institution: Institution
    operation_id: str
    proposal_id: str
    choice: str
    preferences: tuple[MemberPreference, ...]
    expected_revision: int
    moment: Moment
    audience: tuple[InstanceRef, ...]
    channel: str
    provenance: str
    authored_by: str

    def __post_init__(self):
        if type(self.institution) is not Institution or type(self.moment) is not Moment:
            raise InstitutionError('invalid_field')
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise InstitutionError('invalid_field')
        for value in (self.operation_id, self.proposal_id, self.channel):
            segment(value)
        for value in (self.choice, self.provenance, self.authored_by):
            text(value)
        preferences = sequence(self.preferences, 64)
        audience = sequence(self.audience, 64)
        if not all(type(p) is MemberPreference for p in preferences):
            raise InstitutionError('invalid_field')
        if len({p.actor for p in preferences}) != len(preferences):
            raise InstitutionError('duplicate_preference')
        for actor in audience:
            reference(actor)
        if len(set(audience)) != len(audience):
            raise InstitutionError('duplicate_audience')
        object.__setattr__(self, 'preferences', preferences)
        object.__setattr__(self, 'audience', audience)


@dataclass(frozen=True, slots=True)
class CollectiveDecision:
    request: DecisionRequest

    def __post_init__(self):
        if type(self.request) is not DecisionRequest:
            raise InstitutionError('invalid_field')

    @property
    def operation_id(self):
        return self.request.operation_id

    @property
    def choice(self):
        return self.request.choice

    @property
    def audience(self):
        return self.request.audience

    @property
    def dissent(self):
        return tuple(p for p in self.request.preferences if p.choice != self.choice)


@dataclass(frozen=True, slots=True)
class InstitutionState:
    institution: Institution
    revision: int = 0
    decisions: tuple[CollectiveDecision, ...] = ()

    def __post_init__(self):
        if type(self.institution) is not Institution or type(self.revision) is not int:
            raise InstitutionError('invalid_field')
        decisions = sequence(self.decisions, 64)
        if self.revision != len(decisions) or not all(type(d) is CollectiveDecision for d in decisions):
            raise InstitutionError('invalid_history')
        if len({d.operation_id for d in decisions}) != len(decisions):
            raise InstitutionError('invalid_history')
        for i, decision in enumerate(decisions):
            request = decision.request
            if request.expected_revision != i or request.institution.group != self.institution.group:
                raise InstitutionError('invalid_history')
            if i and request.moment.sequence < decisions[i - 1].request.moment.sequence:
                raise InstitutionError('invalid_history')
        object.__setattr__(self, 'decisions', decisions)
