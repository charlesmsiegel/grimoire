"""Authored communications, sent and due on the campaign's 055 calendar, independent of knowledge order."""
from dataclasses import dataclass
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment

MINUTES_PER_DAY = 1440
#: 055's supported range: fixed day 1 is 0001-01-01 and 3652059 is 9999-12-31 on the Rata Die axis
MAX_DAY = 3652059


@dataclass(frozen=True, order=True, slots=True)
class CampaignTime:
    """A point on the campaign's 055 calendar axis: its ``Moment``'s fixed day and minute.

    Plain values here, so this package stays free of the calendar's date adapters; ``rumorstore`` reads the
    campaign's calendar and compares its current time with these. Ordered by day, then minute.
    """
    fixed_day: int
    minute: int = 0

    def __post_init__(self):
        if type(self.fixed_day) is not int or not 1 <= self.fixed_day <= MAX_DAY:
            raise ValueError('invalid_time')
        if type(self.minute) is not int or not 0 <= self.minute < MINUTES_PER_DAY:
            raise ValueError('invalid_time')

    def advance(self, minutes):
        if type(minutes) is not int or minutes < 0:
            raise ValueError('invalid_time')
        days, minute = divmod(self.minute + minutes, MINUTES_PER_DAY)
        return CampaignTime(self.fixed_day + days, minute)


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
    #: campaign time (055) the message left the sender, and when it can first arrive
    sent_on: CampaignTime
    due_on: CampaignTime
    route: DeliveryConditions

    def __post_init__(self):
        for f in ('communication_id', 'source_evidence_id', 'channel'):
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
        if type(self.sent_on) is not CampaignTime or type(self.due_on) is not CampaignTime or self.due_on < self.sent_on:
            raise ValueError('invalid_time')


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
