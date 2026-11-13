"""051's ephemeral coordination values. The full plan is director-private, never actor memory."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field

from grimoire.core import InstanceRef
from grimoire.core.inference import TurnInferencePlan
from grimoire.generation import GenerationError

KINDS = frozenset({'answer', 'interject', 'aside', 'silence', 'nonverbal'})
SPEAKING = frozenset({'answer', 'interject', 'aside'})
GROUNDINGS = frozenset({'addressed', 'heard', 'intention', 'listening'})
_SEAL = object()


def refs(values, label):
    if isinstance(values, (str, bytes, Mapping)):
        raise GenerationError('bad_refs', f'{label} must be actor references')
    try:
        result = tuple(values)
    except TypeError:
        raise GenerationError('bad_refs', f'{label} must be actor references') from None
    if not all(isinstance(ref, InstanceRef) for ref in result) or len(set(result)) != len(result):
        raise GenerationError('bad_refs', f'{label} must contain distinct actor references')
    return result


def text(value, label, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise GenerationError('bad_bid', f'{label} must be bounded nonblank text')


@dataclass(frozen=True, slots=True)
class ConversationBid:
    actor: InstanceRef
    kind: str
    addressees: tuple[InstanceRef, ...]
    audience: tuple[InstanceRef, ...]
    reason: str = field(repr=False)
    grounding: str
    heard_index: int | None = None
    nonverbal: str | None = field(default=None, repr=False)
    role: str | None = field(default=None, repr=False)
    attention: tuple[InstanceRef, ...] = ()

    def __post_init__(self):
        if not isinstance(self.actor, InstanceRef):
            raise GenerationError('bad_bid', 'a bid needs its actor reference')
        if not isinstance(self.kind, str) or self.kind not in KINDS:
            raise GenerationError('bad_bid', 'unknown contribution kind')
        if not isinstance(self.grounding, str) or self.grounding not in GROUNDINGS:
            raise GenerationError('bad_grounding', 'unknown grounding kind')
        for name in ('addressees', 'audience', 'attention'):
            object.__setattr__(self, name, refs(getattr(self, name), name))
        text(self.reason, 'reason')
        if self.role is not None:
            text(self.role, 'role', 80)
        if self.kind == 'nonverbal':
            text(self.nonverbal, 'nonverbal')
        elif self.nonverbal is not None:
            raise GenerationError('bad_bid', 'only a nonverbal bid carries a proposed beat')
        if self.grounding == 'heard':
            if type(self.heard_index) is not int or self.heard_index < 0:
                raise GenerationError('bad_grounding', 'heard grounding requires a nonnegative index')
        elif self.heard_index is not None:
            raise GenerationError('bad_grounding', 'only heard grounding takes an index')
        if self.kind == 'interject' and self.grounding not in ('heard', 'intention'):
            raise GenerationError('bad_grounding', 'interjections need heard evidence or an intention')
        if self.kind in SPEAKING and self.grounding == 'listening':
            raise GenerationError('bad_grounding', 'listening is a quiet contribution')
        if self.actor not in self.audience or not set(self.addressees) <= set(self.audience):
            raise GenerationError('bad_audience', 'speaker and addressees must be in the audience')


@dataclass(frozen=True, slots=True)
class VisibleDecision:
    actor: InstanceRef
    kind: str
    addressees: tuple[InstanceRef, ...]
    held: bool
    reason: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ConversationPlan:
    inference: TurnInferencePlan
    present: tuple[InstanceRef, ...]
    owners: tuple[tuple[InstanceRef, str], ...]
    initiator: InstanceRef
    addressees: tuple[InstanceRef, ...]
    floor: InstanceRef | None
    player_speaking: bool
    bids: tuple[ConversationBid, ...] = field(repr=False)
    speakers: tuple[InstanceRef, ...]
    _seal: InitVar[object] = None

    def __post_init__(self, _seal):
        if _seal is not _SEAL:
            raise GenerationError('unsealed_plan', 'build a conversation with schedule()')

    def for_actor(self, actor: InstanceRef) -> tuple[VisibleDecision, ...]:
        """Own private reason; others' public coordination only, never their internal rationale."""
        if actor not in self.present:
            raise GenerationError('absent_actor', 'actor is outside this exchange')
        return tuple(VisibleDecision(bid.actor, bid.kind, bid.addressees, self.player_speaking,
                                     bid.reason if bid.actor == actor else None)
                     for bid in self.bids if bid.actor == actor or (
                         not self.player_speaking and actor in bid.audience and bid.kind != 'silence'))
