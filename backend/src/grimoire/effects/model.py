"""Accepted turn effects: proposals, accepted events, receipts and scene wrap-ups (Sprint 021).

The five stages are kept apart by type:

- **draft narration** has no type here: prose never changes state (035/036 store transcripts);
- an ``EffectProposal`` is a pure value proposed by narration, a player, the director or adjudication;
  it carries no authority and constructing one writes nothing;
- an ``AcceptedEvent`` is the durable record of an acceptance: what happened, under which keys, and
- its ``Receipt``s, the committed operations (017 relation files it ended or began);
- a ``SceneWrapUp`` is a retrospective director summary that cites accepted events and has no
  operation field, so it can never change state.

Errors carry a reason and ids, never record text, so they are safe to log.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import (
    SEGMENT_MAX, AcceptedRef, ChatRef, InstanceRef, RelationRef, WrapUpRef, validate_segment,
)
from grimoire.core.json_value import JsonObject
from grimoire.core.knowledge import Moment
from grimoire.graph import PREDICATES

KINDS = ("begin", "end")
PROPOSERS = ("narration", "player", "director", "adjudication")
DOMAINS = ("voluntary_action", "director")
MAX_OPERATIONS = 32
SUMMARY_MAX = 4000
WRAPUP_SUMMARY_MAX = 20_000
TEXT_MAX = 512  # principals, ids and attribution text
CONTRIBUTION_TEXT_MAX = 20_000
_TOKEN_RE = re.compile(r"effect:(accepted|revoked)/([a-z0-9][a-z0-9._-]*)")


class EffectError(ValueError):
    """A proposal, record or request that breaks a 021 rule. ``reason`` is machine-readable."""

    def __init__(self, reason: str, records: Iterable[str] = ()) -> None:
        self.reason = reason
        self.records: tuple[str, ...] = tuple(str(record) for record in records)  # never fails on a bad input
        super().__init__(f"{reason}: {', '.join(self.records)}" if self.records else reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.records))


def _invalid(*records: str) -> EffectError:
    return EffectError("invalid_field", records)


def _text(value: object, limit: int = TEXT_MAX) -> None:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise _invalid()


def _segment(value: object, reason: str = "invalid_field") -> None:
    try:
        validate_segment(value, "id")
    except ValueError:
        raise EffectError(reason) from None


def _int(value: object, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise _invalid()


# -- provenance tokens --------------------------------------------------------------------------

def accepted_token(event_id: str) -> str:
    """The exact provenance 021 writes on relation changes made by accepting ``event_id``."""
    _segment(event_id)
    return f"effect:accepted/{event_id}"


def revoked_token(event_id: str) -> str:
    """The exact provenance 021 writes on relation changes made by revoking ``event_id``."""
    _segment(event_id)
    return f"effect:revoked/{event_id}"


def parse_token(text: object) -> tuple[str, str] | None:
    """``("accepted"|"revoked", event_id)`` for an exact 021 token; anything else (free text) is ``None``."""
    if type(text) is not str:
        return None
    match = _TOKEN_RE.fullmatch(text)
    if match is None or len(match.group(2)) > SEGMENT_MAX:
        return None
    return match.group(1), match.group(2)


# -- operations and proposals -------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Op:
    """One state change on a 017 relation fact.

    ``begin``: the fact starts holding at the event's sequence. ``end``: the single walkable
    relation recording it stops holding there. ``agent`` is the individual whose voluntary action
    causes it (``None`` for an external effect or world fact); ``backed_by`` names the 003
    contribution behind a player-controlled agent's action.
    """

    kind: str
    predicate: str
    subject: InstanceRef
    object: InstanceRef
    agent: InstanceRef | None = None
    backed_by: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in KINDS:
            raise _invalid()
        if type(self.predicate) is not str or self.predicate not in PREDICATES:
            raise EffectError("unknown_predicate")
        for ref in (self.subject, self.object):
            if type(ref) is not InstanceRef:
                raise _invalid()
        if self.subject == self.object:
            raise EffectError("self_relation")
        if self.agent is not None and type(self.agent) is not InstanceRef:
            raise _invalid()
        if self.backed_by is not None:
            _text(self.backed_by)

    @property
    def symmetric(self) -> bool:
        return PREDICATES[self.predicate].symmetric

    def fact(self) -> tuple:
        """Predicate and endpoints; endpoint order is meaningless for a symmetric predicate."""
        ends = sorted((self.subject, self.object), key=str) if self.symmetric else [self.subject, self.object]
        return (self.predicate, *ends)

    def campaigns(self) -> set[str]:
        refs = [self.subject, self.object] + ([self.agent] if self.agent is not None else [])
        return {ref.campaign_id for ref in refs}


def _moment(moment: object) -> Moment:
    if type(moment) is not Moment:
        raise EffectError("invalid_moment")
    for label in (moment.scene_id, moment.turn_id):
        _segment(label, "invalid_moment")
    return moment


def fingerprint(chat: ChatRef, moment: Moment, operations: Iterable[Op]) -> str:
    """What makes two acceptances the same effect: turn and operations, not wording, proposer or backing.

    A hand-rolled canonical text (every token is a validated identifier or reference, so none can
    contain ``|`` or a newline), hashed with sha256. Stored endorsements pin it, so it never changes.
    """
    lines = [f"chat={chat}", f"sequence={moment.sequence}", f"scene={moment.scene_id}", f"turn={moment.turn_id}"]
    ops = []
    for op in operations:
        predicate, first, second = op.fact()
        ops.append("|".join((op.kind, predicate, str(first), str(second), "" if op.agent is None else str(op.agent))))
    lines += sorted(ops)  # the same operations in another order are the same effect
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def revocation_fingerprint(ref: AcceptedRef, event_fingerprint: str) -> str:
    """What a player endorses to undo ``ref``: bound to that event, and distinct from accepting it.

    An acceptance endorsement can therefore never be replayed as consent to undo, and the record
    shows which decision each endorsement was.
    """
    if type(ref) is not AcceptedRef:
        raise _invalid()
    return "sha256:" + hashlib.sha256(f"revoke\n{ref}\n{event_fingerprint}".encode("utf-8")).hexdigest()


def _check_operations(campaign_id: str, operations: object) -> tuple[Op, ...]:
    if type(operations) is not tuple or not all(type(op) is Op for op in operations):
        raise _invalid()
    if not operations:
        raise EffectError("no_operations")
    if len(operations) > MAX_OPERATIONS:
        raise EffectError("too_many_operations")
    if any(op.campaigns() != {campaign_id} for op in operations):
        raise EffectError("campaign_mismatch")
    facts = [op.fact() for op in operations]
    if len(set(facts)) != len(facts):
        raise EffectError("conflicting_operations")
    return operations


@dataclass(frozen=True, slots=True)
class EffectProposal:
    """A proposed effect for one turn. Pure; no authority; ``proposed_by`` is attribution only."""

    campaign_id: str
    chat: ChatRef
    moment: Moment
    summary: str
    operations: tuple[Op, ...]
    proposed_by: str

    def __post_init__(self) -> None:
        _segment(self.campaign_id)
        if type(self.chat) is not ChatRef:
            raise _invalid()
        if self.chat.campaign_id != self.campaign_id:
            raise EffectError("campaign_mismatch")
        _moment(self.moment)
        _text(self.summary, SUMMARY_MAX)
        object.__setattr__(self, "operations", tuple(self.operations) if isinstance(self.operations, list)
                           else self.operations)
        _check_operations(self.campaign_id, self.operations)
        if type(self.proposed_by) is not str or self.proposed_by not in PROPOSERS:
            raise _invalid()

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.chat, self.moment, self.operations)


# -- the accepted-event record ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ContributionSnapshot:
    """The 003 contribution that backed a PC's voluntary operation, kept so the record can be audited."""

    contribution_id: str
    actor_id: str
    submitted_by: str
    kind: str
    scene_id: str
    turn_id: str
    text: str

    def __post_init__(self) -> None:
        for value in (self.contribution_id, self.submitted_by, self.kind, self.scene_id, self.turn_id):
            _text(value)
        _segment(self.actor_id)
        _text(self.text, CONTRIBUTION_TEXT_MAX)


@dataclass(frozen=True, slots=True)
class EndorsementRecord:
    """A player confirmed that the operations with this fingerprint express their contribution."""

    contribution_id: str
    principal: str
    fingerprint: str

    def __post_init__(self) -> None:
        _text(self.contribution_id)
        _text(self.principal)
        if type(self.fingerprint) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.fingerprint):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    """Who controlled an agent when the event was accepted."""

    actor_id: str
    controller_id: str

    def __post_init__(self) -> None:
        _segment(self.actor_id)
        _text(self.controller_id)


@dataclass(frozen=True, slots=True)
class AcceptedOperation:
    """An operation as accepted, with its derived authority domain."""

    op: Op
    domain: str

    def __post_init__(self) -> None:
        if type(self.op) is not Op or type(self.domain) is not str or self.domain not in DOMAINS:
            raise _invalid()
        if (self.domain == "voluntary_action") != (self.op.backed_by is not None):
            raise EffectError("invalid_domain")
        if self.domain == "voluntary_action" and self.op.agent is None:
            raise EffectError("invalid_domain")


@dataclass(frozen=True, slots=True)
class Receipt:
    """Operation ``op`` ended or began ``relation``."""

    op: int
    relation: RelationRef

    def __post_init__(self) -> None:
        _int(self.op, 0)
        if type(self.relation) is not RelationRef:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class Skipped:
    """A partial revocation left operation ``op`` as a human left it, for this reason."""

    op: int
    why: str

    def __post_init__(self) -> None:
        _int(self.op, 0)
        _text(self.why)


@dataclass(frozen=True, slots=True)
class Revocation:
    """The event was revoked: its began relations retracted and its ended ones reopened (minus ``skipped``)."""

    reason: str
    at: str
    committed_by: str
    endorsements: tuple[EndorsementRecord, ...] = ()
    skipped: tuple[Skipped, ...] = ()

    def __post_init__(self) -> None:
        _text(self.reason, SUMMARY_MAX)
        _text(self.at)
        _text(self.committed_by)
        for name, kind in (("endorsements", EndorsementRecord), ("skipped", Skipped)):
            value = getattr(self, name)
            if type(value) is not tuple or not all(type(item) is kind for item in value):
                raise _invalid()
        ops = [item.op for item in self.skipped]
        if ops != sorted(set(ops)):
            raise _invalid()


def _empty() -> JsonObject:
    return JsonObject({})


@dataclass(frozen=True, slots=True)
class AcceptedEvent:
    """A durable acceptance: playable truth from ``moment`` on, committed once with its receipts."""

    ref: AcceptedRef
    chat: ChatRef
    moment: Moment
    summary: str
    proposed_by: str
    operations: tuple[AcceptedOperation, ...]
    contributions: tuple[ContributionSnapshot, ...]
    endorsements: tuple[EndorsementRecord, ...]
    controls: tuple[ControlSnapshot, ...]
    committed_by: str
    accepted_at: str
    ordinal: int
    receipts: tuple[Receipt, ...]
    revocation: Revocation | None = None
    extras: JsonObject = field(default_factory=_empty)

    def __post_init__(self) -> None:
        if type(self.ref) is not AcceptedRef or type(self.chat) is not ChatRef:
            raise _invalid()
        campaign = self.ref.campaign_id
        if self.chat.campaign_id != campaign:
            raise EffectError("campaign_mismatch")
        _moment(self.moment)
        _text(self.summary, SUMMARY_MAX)
        if type(self.proposed_by) is not str or self.proposed_by not in PROPOSERS:
            raise _invalid()
        for name, kind in (("operations", AcceptedOperation), ("contributions", ContributionSnapshot),
                           ("endorsements", EndorsementRecord), ("controls", ControlSnapshot),
                           ("receipts", Receipt)):
            value = getattr(self, name)
            if type(value) is not tuple or not all(type(item) is kind for item in value):
                raise _invalid()
        _check_operations(campaign, tuple(item.op for item in self.operations))
        _text(self.committed_by)
        _text(self.accepted_at)
        _int(self.ordinal, 1)
        if [r.op for r in self.receipts] != list(range(len(self.operations))):
            raise EffectError("invalid_receipts")
        if any(r.relation.campaign_id != campaign for r in self.receipts):
            raise EffectError("campaign_mismatch")
        if len({r.relation for r in self.receipts}) != len(self.receipts):
            raise EffectError("invalid_receipts")
        self._check_authority()
        if self.revocation is not None:
            if type(self.revocation) is not Revocation:
                raise _invalid()
            if any(item.op >= len(self.operations) for item in self.revocation.skipped):
                raise EffectError("invalid_receipts")
            self._check_endorsements(self.revocation.endorsements,
                                     revocation_fingerprint(self.ref, self.fingerprint))
        if type(self.extras) is not JsonObject:
            raise _invalid()

    def _check_endorsements(self, endorsements: tuple[EndorsementRecord, ...], expected: str) -> None:
        by_id = {c.contribution_id: c for c in self.contributions}
        ids = [e.contribution_id for e in endorsements]
        if sorted(ids) != sorted(by_id) or len(set(ids)) != len(ids):
            raise EffectError("invalid_endorsement")
        for item in endorsements:
            if item.fingerprint != expected or item.principal != by_id[item.contribution_id].submitted_by:
                raise EffectError("invalid_endorsement")

    def _check_authority(self) -> None:
        """The structural shape of two-key authority; the service checks the keys themselves."""
        by_id = {c.contribution_id: c for c in self.contributions}
        if len(by_id) != len(self.contributions):
            raise EffectError("invalid_expression")
        controls = {c.actor_id: c.controller_id for c in self.controls}
        if len(controls) != len(self.controls):
            raise _invalid()
        used = set()
        for item in self.operations:
            op = item.op
            if item.domain != "voluntary_action":
                if op.agent is not None and controls.get(op.agent.instance_id, DIRECTOR) != DIRECTOR:
                    raise EffectError("invalid_domain")  # a player-controlled agent's act is never director-domain
                continue
            contribution = by_id.get(op.backed_by)  # type: ignore[arg-type]
            if contribution is None:
                raise EffectError("pc_authorship")
            if (contribution.actor_id, contribution.scene_id, contribution.turn_id, contribution.kind) != (
                    op.agent.instance_id, self.moment.scene_id, self.moment.turn_id, "action"):  # type: ignore[union-attr]
                raise EffectError("invalid_expression")
            if controls.get(op.agent.instance_id) != contribution.submitted_by:  # type: ignore[union-attr]
                raise EffectError("invalid_expression")
            used.add(contribution.contribution_id)
        if used != set(by_id):
            raise EffectError("invalid_expression")
        self._check_endorsements(self.endorsements, self.fingerprint)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.chat, self.moment, (item.op for item in self.operations))

    @property
    def active(self) -> bool:
        return self.revocation is None

    @property
    def event_id(self) -> str:
        return self.ref.event_id


# -- scene wrap-up --------------------------------------------------------------------------------

def wrapup_id(chat: ChatRef, scene_id: str) -> str:
    """The one wrap-up id for a chat's scene: deterministic and unambiguous."""
    if type(chat) is not ChatRef:
        raise _invalid()
    _segment(scene_id)
    digest = hashlib.sha256(f"{chat.chat_id}\0{scene_id}".encode()).hexdigest()
    return f"w-{digest[:24]}"


@dataclass(frozen=True, slots=True)
class SceneWrapUp:
    """A retrospective director summary of one chat scene. It cites accepted events and changes nothing.

    Director truth, never actor memory: it may mention what no actor witnessed.
    """

    ref: WrapUpRef
    chat: ChatRef
    scene_id: str
    through: Moment
    summary: str
    cites: tuple[str, ...]
    written_by: str
    written_at: str

    def __post_init__(self) -> None:
        if type(self.ref) is not WrapUpRef or type(self.chat) is not ChatRef:
            raise _invalid()
        if self.chat.campaign_id != self.ref.campaign_id:
            raise EffectError("campaign_mismatch")
        _segment(self.scene_id)
        if self.ref.wrapup_id != wrapup_id(self.chat, self.scene_id):
            raise EffectError("wrapup_mismatch")
        if type(self.through) is not Moment:
            raise _invalid()
        if self.through.scene_id is not None and self.through.scene_id != self.scene_id:
            raise EffectError("invalid_moment")  # only its sequence counts; its labels may not name another scene
        _text(self.summary, WRAPUP_SUMMARY_MAX)
        if type(self.cites) is not tuple:
            raise _invalid()
        for cite in self.cites:
            _segment(cite)
        if list(self.cites) != sorted(set(self.cites)):
            raise _invalid()
        _text(self.written_by)
        _text(self.written_at)
