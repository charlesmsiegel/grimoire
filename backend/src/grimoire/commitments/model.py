"""Pure commitment values: what was offered, each recorded step, and the ledger's structure (077).

A commitment is a promise, mission or objective someone in the campaign owes someone else. Its life is an
append-only list of operations: one ``Offer`` and later ``Transition`` steps (accepted, refused, partly or
wholly fulfilled, abandoned). Every operation cites the 020 objective events it rests on, so a step is never
the narrator's or a labeller's say-so. Whether a step still holds after a retcon is decided when the ledger
is read (``fold``), from the citations' current support; nothing here reads files or calls a model.

Statuses are not a progress bar: there is no percentage, and a commitment can end refused or abandoned with
its deadline still on the record (the sprint's proving scenario).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment

KINDS = ("promise", "mission", "objective")
STATUSES = ("offered", "accepted", "refused", "partially_fulfilled", "fulfilled", "abandoned", "lapsed")
#: Statuses a commitment can still move on from. ``overdue`` is only ever one of these past its deadline.
OPEN = frozenset({"offered", "accepted", "partially_fulfilled"})
#: The obligors' own decisions. Every obligor a player controls must back one with their own 003
#: contribution and endorsement; the director decides for the rest. An offer binds nobody until accepted.
DECISIONS = frozenset({"accepted", "refused", "abandoned"})
#: Adjudicated outcomes: the cited fiction shows it happened (or that the commitment fell through without
#: anyone deciding to drop it: ``lapsed``). They are the director's and carry no player backing.
OUTCOMES = frozenset({"partially_fulfilled", "fulfilled", "lapsed"})
#: Which step may follow which. ``refused``, ``fulfilled``, ``abandoned`` and ``lapsed`` are final.
NEXT = {
    "offered": frozenset({"accepted", "refused", "lapsed"}),
    "accepted": frozenset({"partially_fulfilled", "fulfilled", "abandoned", "lapsed"}),
    "partially_fulfilled": frozenset({"partially_fulfilled", "fulfilled", "abandoned", "lapsed"}),
    "refused": frozenset(), "fulfilled": frozenset(), "abandoned": frozenset(), "lapsed": frozenset(),
}
MAX_CITES = 16
MAX_PARTIES = 16
TEXT_MAX = 2000
CONTRIBUTION_MAX = 8000


class CommitmentError(ValueError):
    """A refused or malformed commitment record or request, with a machine ``reason``."""

    def __init__(self, message: str, reason: str = "invalid_record") -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def segment(value: object, label: str) -> str:
    if type(value) is not str:
        raise CommitmentError(f"{label} must be an id segment")
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise CommitmentError(str(error)) from None
    return value


def text(value: object, label: str, *, maximum: int = TEXT_MAX, empty: bool = False) -> str:
    # Storable UTF-8 only: a lone surrogate would fail later inside the store, after planning.
    if (type(value) is not str or len(value) > maximum or (not empty and not value.strip())
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise CommitmentError(f"{label} must be {'' if empty else 'nonempty '}text of at most {maximum} characters")
    return value


def _refs(value: object, campaign_id: str, label: str) -> tuple[InstanceRef, ...]:
    if type(value) not in (tuple, list) or not 1 <= len(value) <= MAX_PARTIES:
        raise CommitmentError(f"{label} must list 1 to {MAX_PARTIES} campaign instances")
    if not all(type(ref) is InstanceRef for ref in value):
        raise CommitmentError(f"{label} must hold InstanceRef values")
    if any(ref.campaign_id != campaign_id for ref in value):
        raise CommitmentError(f"{label} names another campaign's instance", "campaign_mismatch")
    if len(set(value)) != len(value):
        raise CommitmentError(f"{label} lists an instance twice")
    return tuple(sorted(value, key=str))  # one canonical form: the same parties in another order are equal


def cites_of(value: object) -> tuple[str, ...]:
    """1 to ``MAX_CITES`` distinct 020 event ids, sorted (a citation's order means nothing)."""
    if type(value) not in (tuple, list) or not 1 <= len(value) <= MAX_CITES:
        raise CommitmentError(f"a step must cite 1 to {MAX_CITES} objective events", "no_citation")
    for event_id in value:
        segment(event_id, "cited event id")
    if len(set(value)) != len(value):
        raise CommitmentError("a step cites an event twice")
    return tuple(sorted(value))


def _moment(value: object) -> Moment:
    if type(value) is not Moment:
        raise CommitmentError("a step's moment must be a campaign Moment")
    return value


@dataclass(frozen=True, order=True)
class ClockTime:
    """A 055 campaign-clock time (fixed day and minute), kept here so this package stays pure.

    The store converts it to and from ``grimoire.calendars.Moment``, which owns calendar arithmetic.
    """
    fixed_day: int
    minute: int = 0

    def __post_init__(self) -> None:
        if type(self.fixed_day) is not int or self.fixed_day < 1:
            raise CommitmentError("a clock time's fixed day must be a positive integer")
        if type(self.minute) is not int or not 0 <= self.minute <= 1439:
            raise CommitmentError("a clock time's minute must be 0 to 1439")


def _clock(value: object) -> ClockTime | None:
    if value is not None and type(value) is not ClockTime:
        raise CommitmentError("a step's clock time must be a ClockTime or None")
    return value


OPERATION_MAX = 100  # so the 055 schedule an offer makes, ``commitment-<operation_id>``, is still an id


def operation_id(value: object) -> str:
    segment(value, "operation id")
    if len(value) > OPERATION_MAX:  # type: ignore[arg-type]
        raise CommitmentError(f"an operation id has at most {OPERATION_MAX} characters")
    return value  # type: ignore[return-value]


@dataclass(frozen=True)
class Commitment:
    """What is owed, by whom, to whom, and (optionally) the 055 deadline it is due by.

    ``owed_by`` are the obligors (a party may owe a mission together); ``owed_to`` who it is owed to.
    ``summary`` is director-facing canon, never actor knowledge: who knows of a promise is 020's business.
    """
    campaign_id: str
    commitment_id: str
    kind: str
    summary: str
    owed_by: tuple[InstanceRef, ...]
    owed_to: tuple[InstanceRef, ...]
    deadline_id: str | None = None

    def __post_init__(self) -> None:
        segment(self.campaign_id, "campaign id")
        segment(self.commitment_id, "commitment id")
        if type(self.kind) is not str or self.kind not in KINDS:
            raise CommitmentError("unknown commitment kind")
        text(self.summary, "commitment summary")
        owed_by = _refs(self.owed_by, self.campaign_id, "owed_by")
        owed_to = _refs(self.owed_to, self.campaign_id, "owed_to")
        if set(owed_by) & set(owed_to):
            raise CommitmentError("nobody owes a commitment to themselves", "self_owed")
        object.__setattr__(self, "owed_by", owed_by)
        object.__setattr__(self, "owed_to", owed_to)
        if self.deadline_id is not None:
            segment(self.deadline_id, "deadline id")


@dataclass(frozen=True)
class Authorship:
    """The player's 003 contribution and endorsement behind a player-controlled decider's decision.

    A snapshot: the contribution is the player's own words for that scene and turn, ``fingerprint`` the
    exact step they endorsed (``transition_fingerprint``), ``principal`` the endorsing human.
    """
    contribution_id: str
    actor_id: str
    submitted_by: str
    kind: str
    scene_id: str
    turn_id: str
    text: str
    fingerprint: str

    def __post_init__(self) -> None:
        for name in ("contribution_id", "actor_id", "submitted_by", "kind", "scene_id", "turn_id", "fingerprint"):
            text(getattr(self, name), name, maximum=512)
        text(self.text, "contribution text", maximum=CONTRIBUTION_MAX)


@dataclass(frozen=True)
class Offer:
    """Opens a commitment, citing where it was offered. ``schedules`` records whether this same write
    scheduled the commitment's deadline in the 055 calendar (as calendar operation ``commitment-<operation_id>``)."""
    operation_id: str
    commitment: Commitment
    cites: tuple[str, ...]
    at: Moment
    clock_at: ClockTime | None = None
    schedules: bool = False

    def __post_init__(self) -> None:
        operation_id(self.operation_id)
        if type(self.commitment) is not Commitment:
            raise CommitmentError("an offer needs a Commitment")
        object.__setattr__(self, "cites", cites_of(self.cites))
        _moment(self.at)
        _clock(self.clock_at)
        if type(self.schedules) is not bool or (self.schedules and self.commitment.deadline_id is None):
            raise CommitmentError("only an offer with a deadline id can schedule it", "no_deadline_id")

    @property
    def commitment_id(self) -> str:
        return self.commitment.commitment_id

    def request(self) -> tuple:
        """What a retry must repeat: not the derived moment or clock time."""
        return ("offer", self.commitment, self.cites, self.schedules)


@dataclass(frozen=True)
class Transition:
    """One recorded step of an existing commitment. See ``NEXT``, ``DECISIONS`` and ``OUTCOMES``.

    A decision snapshots, in ``controls``, each obligor's controller when it was recorded (``director`` for
    an uncontrolled or director-controlled obligor), and carries one ``Authorship`` per obligor a player
    controlled then. An outcome has neither.
    """
    operation_id: str
    commitment_id: str
    status: str
    cites: tuple[str, ...]
    at: Moment
    note: str = ""
    clock_at: ClockTime | None = None
    controls: tuple[tuple[str, str], ...] = ()
    authorship: tuple[Authorship, ...] = ()

    def __post_init__(self) -> None:
        operation_id(self.operation_id)
        segment(self.commitment_id, "commitment id")
        if type(self.status) is not str or self.status not in STATUSES or self.status == "offered":
            raise CommitmentError("a step's status must be a status after offered")
        object.__setattr__(self, "cites", cites_of(self.cites))
        _moment(self.at)
        _clock(self.clock_at)
        text(self.note, "step note", empty=True)
        if type(self.controls) not in (tuple, list) or not all(
                type(pair) in (tuple, list) and len(pair) == 2 for pair in self.controls):
            raise CommitmentError("controls must be (actor id, controller) pairs")
        controls = tuple(sorted((segment(actor, "actor id"), text(who, "controller", maximum=512))
                                for actor, who in self.controls))
        if len({actor for actor, _ in controls}) != len(controls):
            raise CommitmentError("an obligor's control is recorded twice")
        if type(self.authorship) not in (tuple, list) or not all(type(a) is Authorship for a in self.authorship):
            raise CommitmentError("authorship must be Authorship values")
        authorship = tuple(sorted(self.authorship, key=lambda a: a.actor_id))
        players = {actor: who for actor, who in controls if who != DIRECTOR}
        if self.status in OUTCOMES and (controls or authorship):
            raise CommitmentError(f"{self.status} is an adjudicated outcome: it has no backing", "unexpected_backing")
        if self.status in DECISIONS and not controls:
            raise CommitmentError(f"{self.status} is the obligors' decision: record their control", "no_controls")
        if {a.actor_id: a.submitted_by for a in authorship} != players or any(
                (a.scene_id, a.turn_id) != (self.at.scene_id, self.at.turn_id) for a in authorship):
            raise CommitmentError("a decision needs exactly one backing per player-controlled obligor, "
                                  "for its own scene and turn", "pc_authorship")
        object.__setattr__(self, "controls", controls)
        object.__setattr__(self, "authorship", authorship)

    def request(self) -> tuple:
        return ("transition", self.commitment_id, self.status, self.cites, self.note)


@dataclass(frozen=True)
class Void:
    """Withdraws an earlier offer or step recorded in error, which 020 cannot retract. The director's key,
    and the endorsement of every player who backed the voided step (``void_fingerprint``)."""
    operation_id: str
    target: str
    reason: str
    authorship: tuple[Authorship, ...] = ()

    def __post_init__(self) -> None:
        operation_id(self.operation_id)
        segment(self.target, "voided operation id")
        text(self.reason, "void reason")
        if type(self.authorship) not in (tuple, list) or not all(type(a) is Authorship for a in self.authorship):
            raise CommitmentError("authorship must be Authorship values")
        object.__setattr__(self, "authorship", tuple(sorted(self.authorship, key=lambda a: a.actor_id)))

    def request(self) -> tuple:
        return ("void", self.target, self.reason)


Operation = Offer | Transition | Void


def _digest(*fields: str) -> str:
    # Length-prefixed, so no field's text can pass for another's: a note cannot forge a citation.
    encoded = "".join(f"{len(value)}:{value};" for value in fields)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def transition_fingerprint(campaign_id: str, commitment_id: str, status: str, cites, note: str = "") -> str:
    """What a player endorses: exactly this step of this commitment, on these citations, with this note."""
    cited = cites_of(cites)
    return _digest("grimoire.commitments/transition", campaign_id, commitment_id, status, str(len(cited)),
                   *cited, note)


def void_fingerprint(campaign_id: str, target: str, reason: str) -> str:
    """What a player endorses to withdraw a step they backed."""
    return _digest("grimoire.commitments/void", campaign_id, target, reason)


@dataclass(frozen=True)
class CommitmentLedger:
    """One campaign's ordered commitment operations. Structure only: legality depends on support (``fold``)."""
    campaign_id: str
    operations: tuple[Operation, ...]

    def __post_init__(self) -> None:
        segment(self.campaign_id, "campaign id")
        if type(self.operations) is not tuple or not all(
                type(op) in (Offer, Transition, Void) for op in self.operations):
            raise CommitmentError("a ledger holds Offer, Transition and Void operations")
        seen: dict[str, Operation] = {}
        offers: dict[str, Offer] = {}
        voided: set[str] = set()
        for op in self.operations:
            if op.operation_id in seen:
                raise CommitmentError("an operation id appears twice", "duplicate_operation")
            if isinstance(op, Offer):
                if op.commitment.campaign_id != self.campaign_id:
                    raise CommitmentError("an offer belongs to another campaign", "campaign_mismatch")
                if op.commitment_id in offers:
                    raise CommitmentError("a commitment is offered twice", "duplicate_commitment")
                offers[op.commitment_id] = op
            elif isinstance(op, Transition):
                offer = offers.get(op.commitment_id)
                if offer is None:
                    raise CommitmentError("a step names a commitment not offered before it", "unknown_commitment")
                obligors = {ref.instance_id for ref in offer.commitment.owed_by}
                if op.controls and {actor for actor, _ in op.controls} != obligors:
                    raise CommitmentError("a decision's controls are not its obligors'", "not_obligor")
                pinned = transition_fingerprint(self.campaign_id, op.commitment_id, op.status, op.cites, op.note)
                if any(a.fingerprint != pinned for a in op.authorship):
                    raise CommitmentError("a backing endorses another step", "pc_authorship")
            else:
                target = seen.get(op.target)
                if target is None or isinstance(target, Void) or op.target in voided:
                    raise CommitmentError("a void names no earlier offer or step, or one already voided",
                                          "invalid_void")
                backers = () if isinstance(target, Offer) else target.authorship
                if {(a.contribution_id, a.actor_id, a.submitted_by) for a in op.authorship} != {
                        (a.contribution_id, a.actor_id, a.submitted_by) for a in backers} or any(
                        a.fingerprint != void_fingerprint(self.campaign_id, op.target, op.reason) for a in op.authorship):
                    raise CommitmentError("a void needs every player who backed the step", "pc_authorship")
                voided.add(op.target)
            seen[op.operation_id] = op

    def operation(self, operation_id: str) -> Operation | None:
        return next((op for op in self.operations if op.operation_id == operation_id), None)

    def offer(self, commitment_id: str) -> Offer | None:
        return next((op for op in self.operations if isinstance(op, Offer) and op.commitment_id == commitment_id),
                    None)

    def steps(self, commitment_id: str) -> tuple[Transition, ...]:
        return tuple(op for op in self.operations if isinstance(op, Transition) and op.commitment_id == commitment_id)

    def voids(self) -> dict[str, Void]:
        """Voided operation id -> its void."""
        return {op.target: op for op in self.operations if isinstance(op, Void)}

    def commitment_of(self, op: Operation) -> str:
        if isinstance(op, Void):
            return self.commitment_of(self.operation(op.target))  # type: ignore[arg-type]
        return op.commitment_id

    def appended(self, op: Operation) -> CommitmentLedger:
        return CommitmentLedger(self.campaign_id, self.operations + (op,))
