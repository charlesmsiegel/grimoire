"""The holdings and ability parts of an accepted turn effect (Sprint 021, next slice).

An effect may now carry, besides its 017 relation operations:

- an ``EffectHoldings``: the lines of **one** 023 holdings operation (a payment, a trade, a consumed fuel
  cell, a find), each line with its own agent, which 021 applies in the same store transaction as the
  accepted-event record, under an operation id derived from the effect and a ``cause`` naming the event;
  it belongs to the effect's ``attempt_id``;
- ability uses: the caller's 025 ``AbilityAssessment``s, one per use, whose ``charges`` are recorded once
  each as an ``AbilityCharge`` on the event; each belongs to its own attempt.

One attempt is charged and applied at most once while an event holds it. Pure: nothing here reads files,
clocks or randomness.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.ability_use import APPROVABLE, DECLARED, AbilityAssessment, AbilityAttempt
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.holdings import HoldingsError, Line, Operation

from .errors import EffectError, invalid, text

#: what an accepted holdings part's or ability charge's authority domain may be (as for an ``Op``)
PART_DOMAINS = ("voluntary_action", "director")
MAX_ABILITIES = 8  # ability uses in one effect: one per actor is the usual case
MAX_CHARGES = 512  # 64 abilities x 8 is far more than any one use can draw on; bounds a stored record
_DIGEST = 32  # hex digits of the holdings operation id's digest
_INVERSE = "-r"


def _segment(value: object) -> None:
    try:
        validate_segment(value, "id")
    except ValueError:
        raise invalid() from None


def _probe(lines: tuple[Line, ...], note: str | None) -> Operation:
    """Validate lines and note exactly as 023 will (shape, size, one campaign, note rules)."""
    try:
        return Operation("probe", lines, note)
    except HoldingsError as error:
        raise EffectError("campaign_mismatch" if error.reason == "cross_campaign" else
                          "too_large" if error.reason == "too_large" else "invalid_field") from None


@dataclass(frozen=True, slots=True)
class ActedLine:
    """One 023 line and whose act it is: ``agent`` and ``backed_by`` mean what they mean on an ``Op``.

    ``agent`` is the individual whose voluntary act the line is (``None`` for a find or an external
    effect); ``backed_by`` is the 003 contribution behind a player-controlled agent's act. A trade
    between two players' characters is two lines with two agents, each backed and endorsed.
    """

    line: Line
    agent: InstanceRef | None = None
    backed_by: str | None = None

    def __post_init__(self) -> None:
        if type(self.line) is not Line:
            raise invalid()
        if self.agent is not None and type(self.agent) is not InstanceRef:
            raise invalid()
        if self.backed_by is not None:
            text(self.backed_by)


@dataclass(frozen=True, slots=True)
class EffectHoldings:
    """The lines of the one 023 operation an effect applies, in order, all-or-nothing.

    A bare ``Line`` is taken as an ``ActedLine`` with no agent. ``note`` is 023's audit note. The
    operation id and cause are 021's.
    """

    lines: tuple[ActedLine, ...]
    note: str | None = None

    def __post_init__(self) -> None:
        lines = self.lines
        if isinstance(lines, list):
            lines = tuple(lines)
        if type(lines) is not tuple:
            raise invalid()
        lines = tuple(ActedLine(item) if type(item) is Line else item for item in lines)
        if not all(type(item) is ActedLine for item in lines):
            raise invalid()
        object.__setattr__(self, "lines", lines)
        _probe(self.raw_lines, self.note)

    @property
    def raw_lines(self) -> tuple[Line, ...]:
        """The 023 lines, in order."""
        return tuple(item.line for item in self.lines)

    @property
    def holders(self) -> tuple[InstanceRef, ...]:
        return _probe(self.raw_lines, self.note).holders

    @property
    def campaign_id(self) -> str:
        return self.lines[0].line.holder.campaign_id

    def agents(self) -> frozenset[InstanceRef]:
        return frozenset(item.agent for item in self.lines if item.agent is not None)

    def campaigns(self) -> set[str]:
        return {ref.campaign_id for ref in (*self.holders, *self.agents())}

    def actors(self) -> frozenset[InstanceRef]:
        """Every holder and every agent: the individuals this part names."""
        return frozenset(self.holders) | self.agents()

    def operation(self, op_id: str, cause: str) -> Operation:
        """The 023 operation 021 applies for this part."""
        try:
            return Operation(op_id, self.raw_lines, self.note, cause)
        except HoldingsError:
            raise invalid() from None

    def inverse(self, op_id: str, cause: str) -> Operation:
        """The operation that undoes this part: every line inverted, in reverse order, no note."""
        try:
            return Operation(inverse_op_id(op_id), inverse_lines(self.raw_lines), None, cause)
        except HoldingsError:
            raise invalid() from None


def inverse_lines(lines: Iterable[Line]) -> tuple[Line, ...]:
    """add n <-> consume n; move s->t n -> move t->s n; adjust seen->to -> adjust to->seen; reversed."""
    undone = []
    for line in reversed(tuple(lines)):
        if line.kind == "add":
            undone.append(Line.consume(line.holder, line.item, line.amount))  # type: ignore[arg-type]
        elif line.kind == "consume":
            undone.append(Line.add(line.holder, line.item, line.amount))  # type: ignore[arg-type]
        elif line.kind == "move":
            undone.append(Line.move(line.target, line.holder, line.item, line.amount))  # type: ignore[arg-type]
        else:
            undone.append(Line.adjust(line.holder, line.item, line.to, line.seen))  # type: ignore[arg-type]
    return tuple(undone)


def holdings_op_id(campaign_id: str, effect_fingerprint: str) -> str:
    """The 023 operation id of an effect's holdings part: derived from the effect, never chosen.

    The fingerprint covers the chat, turn, attempt and lines, so a retry of the same effect derives the
    same id (and is recognised in the holders' logs), and a revision of the attempt derives another.
    """
    digest = hashlib.sha256(f"{campaign_id}\0{effect_fingerprint}".encode("utf-8")).hexdigest()
    return f"h-{digest[:_DIGEST]}"


def inverse_op_id(op_id: str) -> str:
    return op_id + _INVERSE


def _seqs(value: object, holders: tuple[InstanceRef, ...]) -> Mapping[InstanceRef, int]:
    if not isinstance(value, Mapping) or set(value) != set(holders):
        raise EffectError("invalid_receipts")
    for seq in value.values():
        if type(seq) is not int or seq < 1:
            raise EffectError("invalid_receipts")
    return MappingProxyType(dict(sorted(value.items(), key=lambda pair: str(pair[0]))))


def _empty_seqs() -> Mapping[InstanceRef, int]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class AcceptedHoldings:
    """An accepted holdings part and its receipt: each line's authority domain, the 023 ``op_id`` used and
    each holder's logged seq (also the ``basis`` a later operation on those holders passes to 023)."""

    holdings: EffectHoldings
    domains: tuple[str, ...]
    op_id: str
    seqs: Mapping[InstanceRef, int] = field(default_factory=_empty_seqs)

    def __post_init__(self) -> None:
        if type(self.holdings) is not EffectHoldings:
            raise invalid()
        if isinstance(self.domains, list):
            object.__setattr__(self, "domains", tuple(self.domains))
        if type(self.domains) is not tuple or len(self.domains) != len(self.holdings.lines):
            raise invalid()
        for domain, item in zip(self.domains, self.holdings.lines):
            if type(domain) is not str or domain not in PART_DOMAINS:
                raise invalid()
            if (domain == "voluntary_action") != (item.backed_by is not None):
                raise EffectError("invalid_domain")
            if domain == "voluntary_action" and item.agent is None:
                raise EffectError("invalid_domain")
        _segment(self.op_id)
        if self.op_id.endswith(_INVERSE):
            raise invalid()  # an inverse id is never an applied part's id
        object.__setattr__(self, "seqs", _seqs(self.seqs, self.holdings.holders))


def charges_of(assessment: AbilityAssessment) -> tuple[tuple[str, str], ...]:
    """``(ability id, cost id)`` for every cost the assessment charges, sorted and unique."""
    return tuple(sorted({(ability, cost.id) for ability, cost in assessment.charges}))


@dataclass(frozen=True, slots=True)
class AbilityCharge:
    """What an accepted ability use charged: the labelled attempt, how it was classified, and its costs.

    ``ruling_id`` names the approving ruling of an extrapolation, new ability or combination (``None``
    for a declared use). ``charges`` are ``(ability id, cost id)``; costs are prose, so a charge is a
    record of the price paid, and a counted resource is consumed by the same effect's holdings part.
    ``domain`` is ``voluntary_action`` when the actor is player-controlled: the backing contribution is
    the one whose id is the attempt id.
    """

    attempt: AbilityAttempt
    classification: str
    ruling_id: str | None
    charges: tuple[tuple[str, str], ...]
    domain: str

    def __post_init__(self) -> None:
        if type(self.attempt) is not AbilityAttempt or type(self.domain) is not str \
                or self.domain not in PART_DOMAINS:
            raise invalid()
        if type(self.classification) is not str or self.classification not in DECLARED | APPROVABLE:
            raise invalid()  # only a permitted use is ever charged
        if (self.ruling_id is None) != (self.classification in DECLARED):
            raise invalid()  # an approvable use is charged only under the ruling that approved it
        if self.ruling_id is not None:
            _segment(self.ruling_id)
        if isinstance(self.charges, list):
            object.__setattr__(self, "charges", tuple(self.charges))
        if type(self.charges) is not tuple or len(self.charges) > MAX_CHARGES:
            raise invalid()
        for pair in self.charges:
            if type(pair) is not tuple or len(pair) != 2:
                raise invalid()
            for part in pair:
                _segment(part)
        if list(self.charges) != sorted(set(self.charges)):
            raise invalid()

    @classmethod
    def of(cls, assessment: AbilityAssessment, domain: str) -> AbilityCharge:
        """The charge of a permitted assessment; ``ability_not_permitted`` otherwise."""
        if type(assessment) is not AbilityAssessment:
            raise invalid()
        if not assessment.permitted:
            raise EffectError("ability_not_permitted", (assessment.classification,))
        return cls(assessment.attempt, assessment.classification, assessment.ruling_id, charges_of(assessment),
                   domain)


def _digest(attempt_id: str) -> str:
    return hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()


def ability_lines(attempts: Iterable[AbilityAttempt]) -> list[str]:
    """The fingerprint lines of the ability uses, sorted (uses are a set): the attempt's digest (attempt ids
    are free text) and its labels (every other token a validated id, ref or tag)."""
    return sorted("ability=" + "|".join((_digest(attempt.attempt_id), str(attempt.actor), attempt.ability_id,
                                         attempt.effect_id or "", ",".join(sorted(attempt.needs)),
                                         ",".join(sorted(attempt.conditions_met))))
                  for attempt in attempts)


def holdings_lines(attempt_id: str, holdings: EffectHoldings) -> list[str]:
    """The fingerprint lines of a holdings part: its attempt's digest, then every line in order with its agent."""
    found = ["attempt=" + _digest(attempt_id)]
    for item in holdings.lines:
        line = item.line
        found.append("hline=" + "|".join((line.kind, str(line.holder), line.item, _number(line.amount),
                                          _number(line.seen), _number(line.to),
                                          "" if line.target is None else str(line.target),
                                          "" if item.agent is None else str(item.agent))))
    return found


def _number(value: int | None) -> str:
    return "" if value is None else str(value)
