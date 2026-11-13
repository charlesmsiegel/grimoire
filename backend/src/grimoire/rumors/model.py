"""Authored communications; ticks are independent of knowledge order."""
from dataclasses import dataclass
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment


@dataclass(frozen=True, slots=True)
class DeliveryConditions:
    proximity: bool
    affiliation: bool
    channel: bool
    attention: bool

    def __post_init__(self):
        if any(type(getattr(self, f)) is not bool for f in ('proximity', 'affiliation', 'channel', 'attention')):
            raise ValueError('invalid_conditions')

    def allows(self, required):
        return all(not getattr(required, f) or getattr(self, f) for f in ('proximity', 'affiliation', 'channel', 'attention'))


@dataclass(frozen=True, slots=True)
class Rumor:
    communication_id: str
    sender: InstanceRef
    recipient: InstanceRef
    source_evidence_id: str
    text: str
    channel: str
    attributed_to: InstanceRef | None
    confidence: int
    distortion: str
    provenance: str
    authored_at: Moment
    clock_id: str
    clock_unit: str
    sent_tick: int
    due_tick: int
    route: DeliveryConditions

    def __post_init__(self):
        for f in ('communication_id', 'source_evidence_id', 'channel', 'clock_id', 'clock_unit'):
            validate_segment(getattr(self, f), f)
        for f in ('text', 'distortion', 'provenance'):
            if type(getattr(self, f)) is not str or not getattr(self, f).strip():
                raise ValueError('invalid_text')
        if type(self.sender) is not InstanceRef or type(self.recipient) is not InstanceRef or self.sender.campaign_id != self.recipient.campaign_id:
            raise ValueError('invalid_actor')
        if self.attributed_to is not None and (type(self.attributed_to) is not InstanceRef or self.attributed_to.campaign_id != self.sender.campaign_id):
            raise ValueError('invalid_actor')
        if type(self.authored_at) is not Moment or type(self.route) is not DeliveryConditions:
            raise ValueError('invalid_record')
        if type(self.confidence) is not int or not 0 <= self.confidence <= 100:
            raise ValueError('invalid_confidence')
        if any(type(x) is not int or x < 0 for x in (self.sent_tick, self.due_tick)) or self.due_tick < self.sent_tick:
            raise ValueError('invalid_tick')


@dataclass(frozen=True, slots=True)
class AuthoredSend:
    """Sender-safe details, with no route, delivery or director audit data."""
    communication_id: str
    recipient: InstanceRef
    source_evidence_id: str
    text: str
    channel: str
    attributed_to: InstanceRef | None
    confidence: int
    authored_at: Moment
