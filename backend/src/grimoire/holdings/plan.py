"""The one deterministic mutator for counted holdings, and the holder-only prompt context (Sprint 023).

``plan`` turns an operation and the current state of every holder it touches into either new values
or "already applied". It never reads files or clocks: the time, holder existence and file sizing are
passed in. Checks run in a fixed order (shape, reuse, replay, holders, history boundary, apply), so a
retry of an operation that landed is recognized before anything else can refuse it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from grimoire.core.identity import InstanceRef

from .model import (
    MAX_COUNT, MAX_FILE, MAX_LOG, MAX_STACKS, Holdings, HoldingsRefused, LogEntry, Operation, check_timestamp,
)


class Sizer(Protocol):
    """How large a holder's file is once written; supplied by the storage adapter."""

    def document(self, holdings: Holdings) -> int:
        """The exact UTF-8 bytes of the file the store would write."""

    def entry(self, holdings: Holdings, entry: LogEntry) -> int:
        """About how many bytes one log entry adds, indentation and separator included."""


@dataclass(frozen=True)
class SummaryRow:
    """What a human approves: one holder's item before and after. ``new_stack`` flags a possible typo."""

    holder: InstanceRef
    item: str
    before: int
    after: int
    new_stack: bool


@dataclass(frozen=True)
class Applied:
    values: Mapping[InstanceRef, Holdings]
    summary: tuple[SummaryRow, ...]
    outcome: str = "applied"


@dataclass(frozen=True)
class AlreadyApplied:
    entries: Mapping[InstanceRef, LogEntry]
    outcome: str = "already_applied"


def _names(holders) -> str:  # type: ignore[no-untyped-def]
    return ", ".join(str(holder) for holder in holders)


def plan(op: Operation, current: Mapping[InstanceRef, Holdings | None], at: str, *,
         exists: Callable[[InstanceRef], bool], size: Sizer, basis: Mapping[InstanceRef, int] | None = None,
         max_file: int = MAX_FILE) -> Applied | AlreadyApplied:
    """Apply ``op`` to working copies of ``current`` (``None``: no file yet), or say it already landed."""
    if type(op) is not Operation:
        raise TypeError("plan takes an Operation")
    check_timestamp(at)
    holders = op.holders
    basis = _basis(basis, holders)  # a shape check: refused before replay, like the operation itself
    absent = [holder for holder in holders if holder not in current]
    if absent:
        raise ValueError(f"current state is missing holders {_names(absent)}")
    state = {holder: current[holder] or Holdings.empty(holder) for holder in holders}
    for holder, value in state.items():
        if value.holder != holder:
            raise ValueError(f"the state given for {holder} belongs to {value.holder}")

    logged = {holder: entry for holder, value in state.items() if (entry := value.entry(op.op_id)) is not None}
    reused = [holder for holder, entry in logged.items() if not entry.same_operation(op)]
    if reused:
        raise HoldingsRefused("op_id_reused", f"{op.op_id} was logged with other content by {_names(reused)}")
    if logged:
        if len(logged) == len(holders):
            return AlreadyApplied(dict(logged))
        missing = [holder for holder in holders if holder not in logged]
        beyond = []
        for holder in missing:
            peers = [entry.peers.get(holder) for entry in logged.values()]
            if None in peers or state[holder].evicted_through >= min(peers):  # type: ignore[type-var]
                beyond.append(holder)
        if beyond:
            raise HoldingsRefused("beyond_history", f"{op.op_id} is logged by {_names(logged)}; whether it landed "
                                  f"on {_names(beyond)} can no longer be told from their logs")
        raise HoldingsRefused("partial_op", f"{op.op_id} is logged by {_names(logged)} but not by {_names(missing)}; "
                              "check whether those files were reset or restored before completing it by hand")

    gone = [holder for holder in holders if not exists(holder)]
    if gone:
        raise HoldingsRefused("missing_holder", f"no instance file for {_names(gone)}")
    for holder, seen in basis.items():
        current_state = state[holder]
        if current_state.evicted_through > seen:
            raise HoldingsRefused("beyond_history", f"{holder}'s log was evicted past seq {seen}; "
                                  f"whether {op.op_id} landed can no longer be told")
        if current_state.seq < seen:  # seqs only grow: the file was restored from an older copy, or recreated
            raise HoldingsRefused("beyond_history", f"{holder}'s seq went back below {seen}; its file was restored "
                                  f"or recreated, so whether {op.op_id} landed can no longer be told")
        logged_seqs = {entry.seq for entry in current_state.log}
        missing = [seq for seq in range(max(seen, current_state.evicted_through) + 1, current_state.seq + 1)
                   if seq not in logged_seqs]
        if missing:  # a valid log is contiguous; a hole means an entry was removed by hand
            raise HoldingsRefused("beyond_history", f"{holder}'s log is missing seq {missing[0]} since {seen}; "
                                  f"whether {op.op_id} landed can no longer be told")

    stacks = {holder: dict(value.stacks) for holder, value in state.items()}
    touched: dict[InstanceRef, list[str]] = {holder: [] for holder in holders}

    def change(holder: InstanceRef, item: str, count: int) -> None:
        if count > MAX_COUNT:
            raise HoldingsRefused("count_limit", f"{holder} would hold {count} {item}, above {MAX_COUNT}")
        if count:
            stacks[holder][item] = count
        else:
            stacks[holder].pop(item, None)
        if item not in touched[holder]:
            touched[holder].append(item)

    def take(holder: InstanceRef, item: str, amount: int) -> None:
        have = stacks[holder].get(item, 0)
        if have < amount:
            raise HoldingsRefused("insufficient", f"{holder} holds {have} {item}, not {amount}")
        change(holder, item, have - amount)

    for line in op.lines:
        holder, item = line.holder, line.item
        if line.kind == "add":
            change(holder, item, stacks[holder].get(item, 0) + line.amount)  # type: ignore[operator]
        elif line.kind == "consume":
            take(holder, item, line.amount)  # type: ignore[arg-type]
        elif line.kind == "adjust":
            have = stacks[holder].get(item, 0)
            if have != line.seen:
                raise HoldingsRefused("stale_count", f"{holder} holds {have} {item}, not the {line.seen} seen")
            change(holder, item, line.to)  # type: ignore[arg-type]
        else:
            take(holder, item, line.amount)  # type: ignore[arg-type]
            target = line.target
            change(target, item, stacks[target].get(item, 0) + line.amount)  # type: ignore[index, operator]
    for holder in holders:
        if len(stacks[holder]) > MAX_STACKS:
            raise HoldingsRefused("stack_limit", f"{holder} would hold more than {MAX_STACKS} stacks")

    seqs = {holder: state[holder].seq + 1 for holder in holders}
    values, summary = {}, []
    for holder in holders:
        before = state[holder]
        entry = LogEntry(op.op_id, seqs[holder], at, op.lines,
                         {other: seqs[other] for other in holders if other != holder},
                         {item: stacks[holder].get(item, 0) for item in touched[holder]}, op.note, op.cause)
        values[holder] = _evict(before, seqs[holder], stacks[holder], (*before.log, entry), size, max_file)
        summary += [SummaryRow(holder, item, before.count(item), stacks[holder].get(item, 0),
                               item not in before.stacks and stacks[holder].get(item, 0) > 0)
                    for item in sorted(touched[holder])]
    return Applied(values, tuple(summary))


def _basis(basis: Mapping[InstanceRef, int] | None, holders: tuple[InstanceRef, ...]) -> Mapping[InstanceRef, int]:
    """The seqs each holder had when the caller first created the operation; every key is a touched holder."""
    if basis is None:
        return {}
    if not isinstance(basis, Mapping):
        raise HoldingsRefused("invalid_field", "basis must be a mapping of holder to seq")
    for holder, seen in basis.items():
        if holder not in holders:
            raise HoldingsRefused("invalid_field", f"basis names {holder!r}, which the operation does not touch")
        if type(seen) is not int or seen < 0:
            raise HoldingsRefused("invalid_field", f"basis seq for {holder} must be a nonnegative integer")
    return basis


def _evict(before: Holdings, seq: int, stacks: Mapping[str, int], log: tuple[LogEntry, ...], size: Sizer,
           max_file: int) -> Holdings:
    """The new value with the oldest entries dropped until the log fits ``MAX_LOG`` and the file ``max_file``.

    The new entry (the last) is never evicted. Two renders (the whole log, and only the new entry) calibrate
    the adapter's per-entry estimates, so eviction stops close to the bound whichever way they err. One more
    render confirms, evicting further while it is still over; if the estimates were uneven and it evicted too
    much, a bounded search keeps every entry that still fits. With the adapter's near-exact estimates a file
    is rendered about four times per operation, never once per entry.
    """
    last = len(log) - 1

    def candidate(k: int) -> Holdings:
        through = log[k - 1].seq if k else before.evicted_through
        return replace(before, seq=seq, stacks=stacks, log=log[k:], evicted_through=through)

    k = start = max(0, len(log) - MAX_LOG)
    total = size.document(candidate(k))
    if total <= max_file:
        return candidate(k)
    floor = size.document(candidate(last))
    if floor > max_file:
        raise HoldingsRefused("too_large", f"{before.holder}'s file would be {floor} bytes even with only this "
                              f"operation logged, above {max_file}")
    estimates = [size.entry(before, entry) for entry in log[k:last]]
    weight = sum(estimates)
    scale = [(total - floor) * (estimate / weight if weight > 0 else 1 / len(estimates)) for estimate in estimates]
    for share in scale:
        if total <= max_file:
            break
        total -= share
        k += 1
    while size.document(candidate(k)) > max_file:  # calibration fell short: evict one more and re-confirm
        k += 1  # terminates: candidate(last) fits
    if k > start and size.document(candidate(k - 1)) <= max_file:  # it overshot: keep what still fits
        low, high = start, k - 1  # candidate(start) is over, candidate(high) fits
        while high - low > 1:
            middle = (low + high) // 2
            if size.document(candidate(middle)) <= max_file:
                high = middle
            else:
                low = middle
        k = high
    return candidate(k)


def context_lines(holdings: Holdings, *, limit: int = 50) -> tuple[str, ...]:
    """What one holder holds, for that holder's own prompt context: nonzero stacks only, no log or notes."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    lines = [f"{item}: {count}" for item, count in holdings.stacks.items()]
    if len(lines) > limit:
        return (*lines[:limit], f"… and {len(lines) - limit} more")
    return tuple(lines)
