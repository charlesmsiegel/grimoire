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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.ability_use import AbilityAssessment, AbilityAttempt
from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import (
    SEGMENT_MAX, AcceptedRef, ChatRef, InstanceRef, RelationRef, WrapUpRef, validate_segment,
)
from grimoire.core.json_value import JsonObject
from grimoire.core.knowledge import Moment
from grimoire.graph import PREDICATES

from .errors import TEXT_MAX as TEXT_MAX  # re-exported: authorize and callers read it here
from .errors import EffectError, invalid, text
from .parts import (
    MAX_ABILITIES, AbilityCharge, AcceptedHoldings, EffectHoldings, ability_lines, charges_of, holdings_lines,
    holdings_op_id,
)

KINDS = ("begin", "end")
PROPOSERS = ("narration", "player", "director", "adjudication")
DOMAINS = ("voluntary_action", "director")
MAX_OPERATIONS = 32
SUMMARY_MAX = 4000
WRAPUP_SUMMARY_MAX = 20_000
CONTRIBUTION_TEXT_MAX = 20_000
_TOKEN_RE = re.compile(r"effect:(accepted|revoked)/([a-z0-9][a-z0-9._-]*)")


_invalid = invalid
_text = text
#: the start of a ``holdings_skipped`` reason when no touched holder logged the forward change
NEVER_APPLIED = "never applied"


def _segment(value: object, reason: str = "invalid_field") -> None:
    try:
        validate_segment(value, "id")
    except ValueError:
        raise EffectError(reason) from None


def _int(value: object, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise _invalid()


#: at most this many events an acceptance names as ``after`` (one per device's concurrent line of play)
AFTER_MAX = 256


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


def _check_attempt(campaign_id: str, attempt_id: object, holdings: object,
                   abilities: tuple[AbilityAttempt, ...]) -> None:
    """The attempt rules shared by a proposal and an accepted event (``abilities``: each use's attempt).

    ``attempt_id`` is the holdings part's attempt, given exactly when there is one. Each ability use is its
    own attempt; their ids are distinct, and the holdings attempt may be one of them (a use that consumes
    the resource it burns).
    """
    if holdings is not None:
        if type(holdings) is not EffectHoldings:
            raise _invalid()
        if holdings.campaigns() != {campaign_id}:
            raise EffectError("campaign_mismatch")
        if attempt_id is None:
            raise EffectError("missing_attempt")  # one attempt is applied at most once: it needs a name
    if attempt_id is not None:
        _text(attempt_id)
        if holdings is None:
            raise _invalid()  # the attempt id names the holdings part
    if len(abilities) > MAX_ABILITIES:
        raise EffectError("too_many_operations")
    if any(attempt.actor.campaign_id != campaign_id for attempt in abilities):
        raise EffectError("campaign_mismatch")
    ids = [attempt.attempt_id for attempt in abilities]
    for item in ids:  # 025 takes any nonblank text; an attempt id is stored, fingerprinted and compared
        _text(item)
    for attempt in abilities:  # 025 bounds the provenance; it must also be storable UTF-8, refused here by name
        try:
            attempt.provenance.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid() from None
    if len(set(ids)) != len(ids):
        raise EffectError("duplicate_attempt")


def _attempts(attempt_id: str | None, abilities: Iterable[AbilityAttempt]) -> frozenset[str]:
    return frozenset(([] if attempt_id is None else [attempt_id]) + [attempt.attempt_id for attempt in abilities])


def _moment(moment: object) -> Moment:
    if type(moment) is not Moment:
        raise EffectError("invalid_moment")
    for label in (moment.scene_id, moment.turn_id):
        _segment(label, "invalid_moment")
    return moment


def fingerprint(chat: ChatRef, moment: Moment, operations: Iterable[Op], *, attempt_id: str | None = None,
                holdings: EffectHoldings | None = None, abilities: Iterable[AbilityAttempt] = ()) -> str:
    """What makes two acceptances the same effect: turn and operations, not wording, proposer or backing.

    A hand-rolled canonical text (every token is a validated identifier or reference, so none can
    contain ``|`` or a newline), hashed with sha256. Stored endorsements pin it, so it never changes:
    an effect with no holdings or ability use hashes exactly as before the next slice. Those parts
    append lines in a fixed order: the holdings attempt's digest (attempt ids are free text) and every
    holdings line **in order** with its agent, then each ability use's attempt digest and labels,
    sorted. The holdings note, backings, provenance, classification, rulings and charges are not
    covered: they are wording or the director's decision, not the effect.
    """
    lines = [f"chat={chat}", f"sequence={moment.sequence}", f"scene={moment.scene_id}", f"turn={moment.turn_id}"]
    ops = []
    for op in operations:
        predicate, first, second = op.fact()
        ops.append("|".join((op.kind, predicate, str(first), str(second), "" if op.agent is None else str(op.agent))))
    lines += sorted(ops)  # the same operations in another order are the same effect
    if holdings is not None:
        lines += holdings_lines(attempt_id, holdings)  # type: ignore[arg-type]
    lines += ability_lines(abilities)
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def revocation_fingerprint(ref: AcceptedRef, event_fingerprint: str) -> str:
    """What a player endorses to undo ``ref``: bound to that event, and distinct from accepting it.

    An acceptance endorsement can therefore never be replayed as consent to undo, and the record
    shows which decision each endorsement was.
    """
    if type(ref) is not AcceptedRef:
        raise _invalid()
    return "sha256:" + hashlib.sha256(f"revoke\n{ref}\n{event_fingerprint}".encode("utf-8")).hexdigest()


def _check_operations(campaign_id: str, operations: object, *, parts: bool = False) -> tuple[Op, ...]:
    """``parts``: the effect also changes holdings or charges an ability, so it may have no relation op."""
    if type(operations) is not tuple or not all(type(op) is Op for op in operations):
        raise _invalid()
    if not operations and not parts:
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
    #: the holdings part's attempt (its idempotency key), given exactly when ``holdings`` is
    attempt_id: str | None = None
    holdings: EffectHoldings | None = None  # one 023 operation's lines, applied with the event
    #: the caller's 025 assessments, one per ability use; each is its own attempt (a player's is the
    #: backing contribution's id). ``permitted`` is checked when accepting, after the duplicate check.
    abilities: tuple[AbilityAssessment, ...] = ()

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
        if isinstance(self.abilities, list):
            object.__setattr__(self, "abilities", tuple(self.abilities))
        if type(self.abilities) is not tuple or not all(type(a) is AbilityAssessment for a in self.abilities):
            raise _invalid()
        for assessment in self.abilities:  # the caller's assessment (029 will compute it): 025's shape, or refused
            if type(assessment.attempt) is not AbilityAttempt or type(assessment.classification) is not str \
                    or type(assessment.approval) is not str:
                raise _invalid()
            try:
                charges_of(assessment)
            except (TypeError, AttributeError, ValueError):
                raise _invalid() from None
        _check_operations(self.campaign_id, self.operations,
                          parts=self.holdings is not None or bool(self.abilities))
        _check_attempt(self.campaign_id, self.attempt_id, self.holdings, self.ability_attempts)
        if type(self.proposed_by) is not str or self.proposed_by not in PROPOSERS:
            raise _invalid()

    @property
    def ability_attempts(self) -> tuple[AbilityAttempt, ...]:
        return tuple(assessment.attempt for assessment in self.abilities)

    @property
    def attempts(self) -> frozenset[str]:
        """Every attempt this effect charges or applies: the holdings attempt and each ability use's."""
        return _attempts(self.attempt_id, self.ability_attempts)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.chat, self.moment, self.operations, attempt_id=self.attempt_id,
                           holdings=self.holdings, abilities=self.ability_attempts)


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
    #: why the holdings undo was left to a human (``partial=True``), or ``None``
    holdings_skipped: str | None = None
    #: each holder's seq of the inverse holdings operation, when it was applied (the undo's receipt)
    holdings_seqs: Mapping[InstanceRef, int] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def holdings_left(self) -> bool:
        """The holdings undo was skipped while the forward change may still be in force (a human's to repair).

        A skip whose reason starts ``never applied`` found no touched holder logging the forward change: nothing
        is in force, so it holds no attempt and is not reported.
        """
        return self.holdings_skipped is not None and not self.holdings_skipped.startswith(NEVER_APPLIED)

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
        if self.holdings_skipped is not None:
            _text(self.holdings_skipped)
        if not isinstance(self.holdings_seqs, Mapping) or not all(
                type(holder) is InstanceRef and type(seq) is int and seq >= 1
                for holder, seq in self.holdings_seqs.items()):
            raise _invalid()
        if self.holdings_skipped is not None and self.holdings_seqs:
            raise EffectError("invalid_receipts")  # an undo is either applied or skipped
        object.__setattr__(self, "holdings_seqs", MappingProxyType(
            dict(sorted(self.holdings_seqs.items(), key=lambda pair: str(pair[0])))))


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
    attempt_id: str | None = None
    holdings: AcceptedHoldings | None = None
    abilities: tuple[AbilityCharge, ...] = ()  # by attempt id
    #: the events this one was accepted after: the frontier of the device's ledger when it was accepted, sorted
    #: (028). ``None`` for an event accepted by an unidentified library or a build before 028's merge, whose
    #: place is its ordinal. Outside the fingerprint, and never rewritten, so a merge can keep causal order.
    after: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if type(self.ref) is not AcceptedRef or type(self.chat) is not ChatRef:
            raise _invalid()
        if isinstance(self.after, list):
            object.__setattr__(self, "after", tuple(self.after))
        if self.after is not None:
            if type(self.after) is not tuple or len(self.after) > AFTER_MAX:
                raise _invalid()
            for event_id in self.after:
                _segment(event_id)
            if list(self.after) != sorted(set(self.after)) or self.ref.event_id in self.after:
                raise _invalid()  # one canonical form, and never after itself
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
        if self.holdings is not None and type(self.holdings) is not AcceptedHoldings:
            raise _invalid()
        if isinstance(self.abilities, list):
            object.__setattr__(self, "abilities", tuple(self.abilities))
        if type(self.abilities) is not tuple or not all(type(a) is AbilityCharge for a in self.abilities):
            raise _invalid()
        object.__setattr__(self, "abilities", tuple(sorted(self.abilities, key=lambda a: a.attempt.attempt_id)))
        _check_operations(campaign, tuple(item.op for item in self.operations),
                          parts=self.holdings is not None or bool(self.abilities))
        _check_attempt(campaign, self.attempt_id, None if self.holdings is None else self.holdings.holdings,
                       self.ability_attempts)
        if self.holdings is not None and self.holdings.op_id != holdings_op_id(campaign, self.fingerprint):
            raise EffectError("invalid_receipts")  # a hand-edited op id: the receipt names another operation
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
            self._check_holdings_undo(self.revocation)
            self._check_endorsements(self.revocation.endorsements,
                                     revocation_fingerprint(self.ref, self.fingerprint))
        if type(self.extras) is not JsonObject:
            raise _invalid()

    def _check_holdings_undo(self, revocation: Revocation) -> None:
        """A revoked event with holdings either applied the inverse (its seqs) or left it to a human."""
        if self.holdings is None:
            if revocation.holdings_skipped is not None or revocation.holdings_seqs:
                raise EffectError("invalid_receipts")
            return
        if revocation.holdings_skipped is None and set(revocation.holdings_seqs) != set(self.holdings.holdings.holders):
            raise EffectError("invalid_receipts")

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
        acts = [(item.domain, item.op.agent, item.op.backed_by) for item in self.operations]
        if self.holdings is not None:
            for domain, item in zip(self.holdings.domains, self.holdings.holdings.lines):
                acts.append((domain, item.agent, item.backed_by))
        for charge in self.abilities:  # a player's ability use is backed by the contribution named by its attempt
            backing = charge.attempt.attempt_id if charge.domain == "voluntary_action" else None
            acts.append((charge.domain, charge.attempt.actor, backing))
        used = set()
        for domain, agent, backed_by in acts:
            if domain != "voluntary_action":
                if agent is not None and controls.get(agent.instance_id, DIRECTOR) != DIRECTOR:
                    raise EffectError("invalid_domain")  # a player-controlled agent's act is never director-domain
                continue
            contribution = by_id.get(backed_by)  # type: ignore[arg-type]
            if contribution is None:
                raise EffectError("pc_authorship")
            if (contribution.actor_id, contribution.scene_id, contribution.turn_id, contribution.kind) != (
                    agent.instance_id, self.moment.scene_id, self.moment.turn_id, "action"):  # type: ignore[union-attr]
                raise EffectError("invalid_expression")
            if controls.get(agent.instance_id) != contribution.submitted_by:  # type: ignore[union-attr]
                raise EffectError("invalid_expression")
            used.add(contribution.contribution_id)
        if used != set(by_id):
            raise EffectError("invalid_expression")
        self._check_endorsements(self.endorsements, self.fingerprint)

    @property
    def ability_attempts(self) -> tuple[AbilityAttempt, ...]:
        return tuple(charge.attempt for charge in self.abilities)

    @property
    def attempts(self) -> frozenset[str]:
        """Every attempt this event charged or applied: the holdings attempt and each ability use's."""
        return _attempts(self.attempt_id, self.ability_attempts)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.chat, self.moment, (item.op for item in self.operations), attempt_id=self.attempt_id,
                           holdings=None if self.holdings is None else self.holdings.holdings,
                           abilities=self.ability_attempts)

    @property
    def has_parts(self) -> bool:
        """Whether the event uses the next slice's fields (and is therefore stored as schema /1)."""
        return self.holdings is not None or bool(self.abilities)

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
