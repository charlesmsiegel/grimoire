"""Where each commitment stands, from its ledger and the current support of what it cites (077).

``fold`` is the one rule that decides a commitment's status, used for every read and for planning a new
step. A citation's support is supplied by the caller (the store adapter reads 020 and 021):

- ``supported``: the cited objective event is on record and, if it is an accepted event's bridge, that
  accepted event is still active;
- ``revoked``: its accepted event was revoked (a retcon): the fiction it recorded did not happen;
- ``missing``: it is not on the 020 ledger (it may still be on its way from another device);
- ``unreadable``: it could not be checked (a damaged or quarantined file, an unreadable ledger).

Only a confirmed retcon removes fiction: a step with a revoked citation is skipped (``unsupported``), and so
is an operation an explicit ``Void`` withdrew (``voided``). A later step that only followed from a skipped
one, because its status is not a legal next step any more or it is earlier than the commitment's current
moment, is skipped too (``stranded``). So a retcon never leaves a later scene claiming what the undone
fiction alone supported. A missing or unreadable citation keeps its step but makes the state
``certain=False``: an event still syncing, or damage, is reported as uncertainty, never resolved by
guessing either way, and the library records no new step on an uncertain history.

``as_of`` filters the narrative steps by campaign sequence. Deadline state comes from the 055 calendar as it
is **now** (the calendar has no mapping to campaign sequence), which the store reads and passes in as
``deadlines`` (this package stays pure): ``passed`` is the calendar's own flag (an explicit advance crossed
it), ``overdue`` an open commitment past its deadline, and ``late`` whether a fulfilment was recorded at a
campaign time after its deadline (``None``: no clock time to compare).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.core.knowledge import Moment

from .model import NEXT, OPEN, ClockTime, Commitment, CommitmentError, CommitmentLedger, Offer, Transition

SUPPORT = ("supported", "missing", "unreadable", "revoked")
_RANK = {name: rank for rank, name in enumerate(SUPPORT)}


@dataclass(frozen=True)
class StepState:
    """One operation as the fold read it: its weakest citation's support and what became of it.

    ``outcome`` is ``applied``, ``unsupported`` (it cites undone fiction), ``voided`` (withdrawn as recorded
    in error) or ``stranded`` (it followed only from a skipped step). ``cited`` pairs each event id with its own support.
    """
    operation: Offer | Transition
    support: str
    outcome: str
    cited: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DeadlineState:
    """The 055 deadline a commitment is due by, read from the calendar as it is now.

    ``found`` is false when the calendar or the deadline is not there (``due`` and ``passed`` are then
    ``None``): never an "all clear".
    """
    deadline_id: str
    found: bool
    due: ClockTime | None = None
    label: str | None = None
    passed: bool | None = None
    triggered_by: str | None = None


@dataclass(frozen=True)
class CommitmentState:
    """A commitment as of campaign sequence ``as_of`` (``None``: everything recorded). ``status`` is
    ``unsupported`` when its offer cites undone fiction and ``voided`` when its offer was withdrawn; its
    deadline, if any, is then an orphan the director may cancel in the calendar."""
    commitment: Commitment
    status: str
    certain: bool
    moment: Moment
    offer: StepState
    steps: tuple[StepState, ...]
    deadline: DeadlineState | None
    as_of: int | None = None

    @property
    def commitment_id(self) -> str:
        return self.commitment.commitment_id

    @property
    def overdue(self) -> bool:
        return self.status in OPEN and self.deadline is not None and self.deadline.passed is True

    @property
    def late(self) -> bool | None:
        """Whether the fulfilment was recorded after the deadline, in campaign time; ``None`` if unknown/n.a."""
        if self.status != "fulfilled" or self.deadline is None or self.deadline.due is None:
            return None
        clock = self.applied[-1].operation.clock_at
        return None if clock is None else clock > self.deadline.due

    @property
    def applied(self) -> tuple[StepState, ...]:
        return tuple(step for step in self.steps if step.outcome == "applied")


def _step_support(op: Offer | Transition, support: Mapping[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    cited = tuple((event_id, support.get(event_id, "unreadable")) for event_id in op.cites)
    for _, value in cited:
        if type(value) is not str or value not in _RANK:
            raise CommitmentError("unknown citation support")
    return max((value for _, value in cited), key=_RANK.__getitem__), cited


def _deadline(commitment: Commitment, deadlines: Mapping[str, DeadlineState]) -> DeadlineState | None:
    if commitment.deadline_id is None:
        return None
    found = deadlines.get(commitment.deadline_id)
    if found is not None and type(found) is not DeadlineState:
        raise CommitmentError("deadlines must map ids to DeadlineState values")
    if found is None or not found.found or found.deadline_id != commitment.deadline_id:
        return DeadlineState(commitment.deadline_id, False)
    return found


def fold_one(ledger: CommitmentLedger, commitment_id: str, support: Mapping[str, str],
             deadlines: Mapping[str, DeadlineState], *, as_of: int | None = None) -> CommitmentState | None:
    """The commitment as of sequence ``as_of`` (``None``: everything recorded), or ``None`` if not offered by then.

    ``deadlines`` maps each 055 deadline id the calendar holds now to its state; an absent id is ``found=False``.
    """
    offer = ledger.offer(commitment_id)
    if offer is None or (as_of is not None and offer.at.sequence > as_of):
        return None
    voids = ledger.voids()
    offer_support, offer_cited = _step_support(offer, support)
    # Only an event that cannot be checked is uncertainty: a revoked offer is known to be undone.
    certain = offer_support not in ("missing", "unreadable") or offer.operation_id in voids
    if offer.operation_id in voids:
        status, outcome = "voided", "voided"
    elif offer_support == "revoked":
        status, outcome = "unsupported", "unsupported"
    else:
        status, outcome = "offered", "applied"
    alive = outcome == "applied"
    offered = StepState(offer, offer_support, outcome, offer_cited)
    moment = offer.at
    steps = []
    for step in ledger.steps(commitment_id):
        if as_of is not None and step.at.sequence > as_of:
            continue
        value, cited = _step_support(step, support)
        could_apply = alive and step.status in NEXT[status] and step.at.sequence >= moment.sequence
        if could_apply and step.operation_id not in voids and value in ("missing", "unreadable"):
            certain = False  # an unchecked step that would apply if supported could change how the rest folds
        if step.operation_id in voids:
            outcome = "voided"
        elif value == "revoked":
            outcome = "unsupported"
        elif not alive or step.status not in NEXT[status] or step.at.sequence < moment.sequence:
            outcome = "stranded"
        else:
            outcome = "applied"
            status, moment = step.status, step.at
        steps.append(StepState(step, value, outcome, cited))
    return CommitmentState(offer.commitment, status, certain, moment, offered, tuple(steps),
                           _deadline(offer.commitment, deadlines), as_of)


def fold(ledger: CommitmentLedger | None, support: Mapping[str, str], deadlines: Mapping[str, DeadlineState], *,
         as_of: int | None = None) -> tuple[CommitmentState, ...]:
    """Every commitment offered by ``as_of``, in offer order."""
    if ledger is None:
        return ()
    found = (fold_one(ledger, op.commitment_id, support, deadlines, as_of=as_of)
             for op in ledger.operations if isinstance(op, Offer))
    return tuple(state for state in found if state is not None)
