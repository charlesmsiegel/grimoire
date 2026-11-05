"""The one deterministic mutator for unique possessions (Sprint 034).

``plan`` turns an operation, the item's current record and the other items of its campaign into
either a new record or "already applied". It never reads files or clocks: the time and which
instances exist are passed in. Checks run in a fixed order (shape, replay, revision, parties,
steps), so a retry of an operation that landed is recognized before anything else can refuse it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from grimoire.core.identity import InstanceRef, SourceRef

from .model import (
    MAX_LOG, LogEntry, Operation, Origin, Possession, PossessionRefused, State, check_int, check_party,
    check_timestamp,
)


@dataclass(frozen=True)
class Applied:
    """The new record and the item's state before and after: what a human approves."""

    value: Possession
    before: State
    after: State
    outcome: str = "applied"


@dataclass(frozen=True)
class AlreadyApplied:
    entry: LogEntry
    outcome: str = "already_applied"


def _names(refs) -> str:  # type: ignore[no-untyped-def]
    return ", ".join(str(ref) for ref in refs)


def plan(op: Operation, current: Possession, *, expected_rev: int, at: str,
         exists: Callable[[InstanceRef], bool], others: Mapping[InstanceRef, Possession]) -> Applied | AlreadyApplied:
    """Apply ``op`` to ``current`` as planned against ``expected_rev``, or say it already landed.

    ``others`` is every *other* item of the campaign: equip checks its slots and move its custody
    chains, so a caller that cannot read every item must refuse before calling (the service does).
    """
    if type(op) is not Operation or type(current) is not Possession:
        raise TypeError("plan takes an Operation and a Possession")
    check_timestamp(at)
    check_int(expected_rev, "expected_rev", 1)  # a shape check: refused before replay, like the operation
    item = current.item
    for step in op.steps:
        for party in step.parties:
            check_party(party, "a step's party", item.campaign_id)
    if current.origin.op == op.op_id:
        raise PossessionRefused("op_id_reused", f"{op.op_id} created {item}")
    logged = current.entry(op.op_id)
    if logged is not None:
        if not logged.same_operation(op):
            raise PossessionRefused("op_id_reused", f"{op.op_id} was logged by {item} with other content")
        return AlreadyApplied(logged)
    if expected_rev != current.rev:
        raise PossessionRefused("stale_revision", f"{item} is at revision {current.rev}, not the {expected_rev} "
                                "this operation was planned against; read it again")
    gone = sorted({party for step in op.steps for party in step.parties if not exists(party)}, key=str)
    if gone:
        raise PossessionRefused("missing_party", f"no instance file for {_names(gone)}")

    state = current.state
    for step in op.steps:
        state = _step(item, state, step, current.fits, others)
    value = _record(current, current.rev + 1, at, op, current.state, state)
    return Applied(value, current.state, state)


def _step(item: InstanceRef, state: State, step, fits: tuple[str, ...],  # type: ignore[no-untyped-def]
          others: Mapping[InstanceRef, Possession]) -> State:
    if step.kind == "unequip":
        if state.equipped is None:
            raise PossessionRefused("not_equipped", f"{item} is not equipped")
        return replace(state, equipped=None)
    if step.kind == "equip":
        if state.equipped is not None:
            raise PossessionRefused("already_equipped", f"{item} is already equipped in {state.equipped}")
        if step.by != state.owner:
            raise PossessionRefused("not_owner", f"{step.by} does not own {item} (owner: {state.owner})")
        if step.by != state.holder:
            raise PossessionRefused("not_holder", f"{step.by} does not have {item} (custody: {state.holder})")
        if step.slot not in fits:
            raise PossessionRefused("slot_mismatch", f"{item} does not fit {step.slot} (fits: {', '.join(fits)})")
        taken = sorted((ref for ref, other in others.items()
                        if ref != item and other.holder == step.by and other.equipped == step.slot), key=str)
        if taken:
            raise PossessionRefused("slot_taken", f"{step.by} already wears {_names(taken)} in {step.slot}")
        return replace(state, equipped=step.slot)
    if state.equipped is not None:  # move and own: the wearer must take it off first
        raise PossessionRefused("still_equipped", f"{item} is equipped in {state.equipped}; unequip it first")
    if step.kind == "own":
        if step.to == state.owner:
            raise PossessionRefused("same_owner", f"{item} is already owned by {step.to}")
        if step.to == item:
            raise PossessionRefused("self_owner", f"{item} cannot own itself")
        return replace(state, owner=step.to)
    if step.to == state.holder:
        raise PossessionRefused("same_holder", f"{item} is already with {step.to}")
    if step.to == item:
        raise PossessionRefused("self_custody", f"{item} cannot hold itself")
    chain = custody_chain(step.to, others)
    if item in chain:
        raise PossessionRefused("custody_cycle", f"{step.to} is itself inside {item}")
    return replace(state, holder=step.to)


def custody_chain(start: InstanceRef, items: Mapping[InstanceRef, Possession]) -> tuple[InstanceRef, ...]:
    """``start`` and, while it is an item, whatever holds it, outward; stops at a repeat (a hand-made cycle)."""
    chain, node = [start], start
    while node in items:
        node = items[node].holder
        if node in chain:
            break
        chain.append(node)
    return tuple(chain)


def _record(current: Possession, rev: int, at: str, op: Operation, before: State, after: State) -> Possession:
    """The new value, its log bounded to ``MAX_LOG`` entries by dropping the oldest (never the new one)."""
    entry = LogEntry(op.op_id, rev, at, op.steps, before, after, op.note, op.cause)
    log = (*current.log, entry)
    evicted = current.evicted_through
    if len(log) > MAX_LOG:
        evicted = log[len(log) - MAX_LOG - 1].rev
        log = log[len(log) - MAX_LOG:]
    return replace(current, state=after, rev=rev, log=log, evicted_through=evicted)


_ANY_TIME = "2000-01-01T00:00:00Z"


@dataclass(frozen=True, slots=True)
class Creation:
    """What a new unique item starts as. ``op_id`` makes a retried create recognisable, never a second item."""

    op_id: str
    holder: InstanceRef
    owner: InstanceRef | None = None
    name: str | None = None
    source: SourceRef | None = None
    fits: tuple[str, ...] = ()
    note: str | None = None
    cause: str | None = None

    def __post_init__(self) -> None:
        self.origin(_ANY_TIME)  # every field check lives in Origin; the time is supplied at planning

    @property
    def campaign_id(self) -> str:
        return self.holder.campaign_id

    def origin(self, at: str) -> Origin:
        return Origin(self.op_id, at, self.holder, self.owner, self.name, self.source, tuple(self.fits), self.note,
                      self.cause)

    def same(self, origin: Origin) -> bool:
        """Whether a recorded origin is this very creation (its time aside)."""
        return self.origin(origin.at) == origin


def originate(creation: Creation, item: InstanceRef, *, at: str,
              exists: Callable[[InstanceRef], bool]) -> Possession:
    """A new item's record at revision 1. Replay of ``creation.op_id`` is the caller's to check first: it
    must search every item of the campaign, which this pure function cannot see."""
    if type(creation) is not Creation:
        raise TypeError("originate takes a Creation")
    check_timestamp(at)
    check_party(item, "item", creation.campaign_id)
    if creation.name is None and creation.source is None:
        raise PossessionRefused("needs_name", "a campaign-original item needs a name")
    gone = sorted({party for party in (creation.holder, creation.owner) if party is not None
                   and not exists(party)}, key=str)
    if gone:
        raise PossessionRefused("missing_party", f"no instance file for {_names(gone)}")
    origin = creation.origin(at)
    return Possession(item, State(creation.holder, creation.owner), origin, fits=origin.fits)
