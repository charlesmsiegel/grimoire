"""Merging one campaign's 023 holder files from two devices (028): replay, never a count copied over.

Each device's holder file is the agreed (base) file plus new log entries, and perhaps a hand edit of a
count. The merge collects every new operation of both devices, once each, orders them so that the
operations one device logged on one holder keep that device's order, and replays them from the agreed
stacks with 023's own ``plan``: seqs, peers and ``after`` are recomputed, each entry keeps its original
``at``, and the log is evicted as any write would. A hand edit is kept as a residual (the device's final
count less the replay of its own new operations) where only that device touched the count.

Anything the replay refuses, a log that does not extend the agreed one, one operation id with two
contents, or a hand edit the other device's operations also touched, stops the merge with a review value
naming it: an operation is never dropped. Pure: no files, clocks or randomness; sizing is the caller's.
"""

from __future__ import annotations

import heapq
from itertools import pairwise
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from grimoire.core.identity import HoldingsRef, InstanceRef
from grimoire.holdings import (
    HOLDINGS_SCHEMA, MAX_COUNT, MAX_FILE, MAX_STACKS, AlreadyApplied, Holdings, HoldingsError, HoldingsRefused,
    LogEntry, Operation, holdings_to_payload, plan,
)

from .model import BASE, Kept, Output, Review, ReviewItem, Rewrite, Version, choose, ranks

Key = Callable[[Operation], tuple]
_DETAIL = 300  # a refusal's detail names holders, items and counts; bounded so a review stays small


class EnvelopeSizer(Protocol):
    """How large a merged holder file is once written, in the envelope the merge chose for it."""

    def document(self, holdings: Holdings, owner: str | None, extra: Mapping[str, object],
                 previous: str | None) -> int:
        """The exact UTF-8 bytes of the file the store would write."""

    def entry(self, holdings: Holdings, entry: LogEntry) -> int:
        """About how many bytes one log entry adds (023's ``Sizer.entry``)."""


@dataclass(frozen=True)
class HoldingsMerge:
    """The merged holder files, and what the event merge needs from them.

    ``seqs`` gives every replayed operation's new seq on each of its holders, keyed ``(holder, op_id)``:
    an accepted event's holdings receipt is rewritten from it. ``values`` are the merged holdings.
    """

    files: Mapping[HoldingsRef, Output]
    values: Mapping[InstanceRef, Holdings]
    seqs: Mapping[tuple[InstanceRef, str], int]
    order: tuple[str, ...]


class _Bound:
    """023's ``Sizer`` for the merged files: each holder sized in the envelope chosen for it."""

    def __init__(self, sizer: EnvelopeSizer, envelopes: Mapping[InstanceRef, tuple]) -> None:
        self._sizer, self._envelopes = sizer, envelopes

    def document(self, holdings: Holdings) -> int:
        owner, extra, previous = self._envelopes[holdings.holder]
        return self._sizer.document(holdings, owner, extra, previous)

    def entry(self, holdings: Holdings, entry: LogEntry) -> int:
        return self._sizer.entry(holdings, entry)


def _ref(holder: InstanceRef) -> str:
    return str(HoldingsRef.of(holder))


def _extension(base: Holdings, side: Holdings, *, evicted: bool = False) -> list[LogEntry] | str:
    """The side's entries after the agreed seq, in order, or why its log is not the agreed log extended.

    ``evicted`` allows entries after the agreed seq to be evicted already (a file taken whole, which is
    never replayed, so only what its log still shows is checked).
    """
    if side.seq < base.seq:
        return "its seq went back below the agreed one (a restored or recreated file)"
    if side.evicted_through > base.seq and not evicted:
        return "entries after the agreed seq were already evicted"
    logged = {entry.seq: entry for entry in side.log}
    for entry in base.log:
        if entry.seq > side.evicted_through and logged.get(entry.seq) != entry:
            return f"agreed seq {entry.seq} is missing or rewritten"
    fresh = [logged.get(seq) for seq in range(max(base.seq, side.evicted_through) + 1, side.seq + 1)]
    if None in fresh:
        return "a seq after the agreed one is missing"
    return fresh  # type: ignore[return-value]


def _deltas(entries: Iterable[LogEntry], holder: InstanceRef) -> dict[str, int]:
    """How much each item of ``holder`` the lines change, assuming each applied (an adjust: to - seen)."""
    found: dict[str, int] = {}
    for entry in entries:
        for line in entry.lines:
            if line.kind == "adjust":
                change = {line.holder: line.to - line.seen}  # type: ignore[operator]
            elif line.kind == "move":
                change = {line.holder: -line.amount, line.target: line.amount}  # type: ignore[operator, dict-item]
            else:
                change = {line.holder: line.amount if line.kind == "add" else -line.amount}  # type: ignore[operator]
            if holder in change:
                found[line.item] = found.get(line.item, 0) + change[holder]
    return found


def _touched(entries: Iterable[LogEntry], holder: InstanceRef) -> set[str]:
    return {line.item for entry in entries for line in entry.lines if holder in line.holders}


def merge_holdings(base: Mapping[InstanceRef, Version], sides: Mapping[str, Mapping[InstanceRef, Version]], *,
                   exists: Callable[[InstanceRef], bool], sizer: EnvelopeSizer, key: Key | None = None,
                   max_file: int = MAX_FILE) -> HoldingsMerge | Review:
    """Merge the holder files of one campaign: ``base`` is the agreed set, ``sides`` each device's, by id.

    ``exists`` says whether a holder has an instance file in the merged library (023's ``missing_holder``).
    ``key`` orders operations no log orders against each other (the event merge passes each event's merged
    position); ties then go to the lower-ranked device, then the lower op id.
    """
    order = ranks(sides)
    devices = sorted(sides, key=order.__getitem__)
    key = key or (lambda op: ())
    holders = sorted(set(base) | {holder for device in devices for holder in sides[device]}, key=str)
    starts = {holder: base[holder].value if holder in base else Holdings.empty(holder) for holder in holders}
    items: list[ReviewItem] = []
    for holder in holders:
        for device in devices:
            if holder not in sides[device] and holder in base:  # 023 has no delete: a hand deletion, never merged away
                items.append(ReviewItem("deleted", _ref(holder), device))
    if items:
        return Review(tuple(items))
    whole = _whole(base, sides, holders, devices, order)
    new: dict[str, dict[InstanceRef, list[LogEntry]]] = {device: {} for device in devices}
    replayed: set[InstanceRef] = set()
    pending = [holder for holder in holders if holder not in whole]
    while pending:  # a holder an operation shares with a replayed holder is replayed too, never taken whole
        holder = pending.pop()
        if holder in replayed:
            continue
        replayed.add(holder)
        starts.setdefault(holder, Holdings.empty(holder))
        for device in devices:
            version = sides[device].get(holder)
            if version is None:
                continue
            found = _extension(starts[holder], version.value)
            if isinstance(found, str):
                items.append(ReviewItem("log_diverged", _ref(holder), f"{device}: {found}"))
                continue
            new[device][holder] = found
            pending += [peer for entry in found for peer in entry.operation.holders if peer not in replayed]
    for holder, (source, version) in whole.items():
        if holder in replayed or source == BASE:
            continue
        found = _extension(starts[holder], version.value, evicted=True)
        if isinstance(found, str):
            items.append(ReviewItem("log_diverged", _ref(holder), f"{source}: {found}"))
    if items:
        return Review(tuple(items))
    holders = sorted(replayed, key=str)
    ops, held_by, at = _operations(new, devices, items)
    after = _constraints(new, devices)
    envelopes, extras = _envelopes(base, sides, holders, devices, order, items)
    if items:
        return Review(tuple(items))
    sequence = _topological(ops, after, held_by, order, key)
    if isinstance(sequence, ReviewItem):
        return Review((sequence,))

    state = {holder: replace(starts[holder], extras=extras[holder]) for holder in holders}
    bound, seqs = _Bound(sizer, envelopes), {}
    for op_id in sequence:
        op = ops[op_id]
        try:
            result = plan(op, {holder: state[holder] for holder in op.holders}, at[op_id], exists=exists,
                          size=bound, max_file=max_file)
        except HoldingsRefused as error:
            return Review((ReviewItem(error.reason, op_id, error.detail[:_DETAIL]),))
        if isinstance(result, AlreadyApplied):  # an agreed entry already logs it: one id, logged twice
            return Review((ReviewItem("id_collision", op_id, "already logged by the agreed holder files"),))
        for holder, value in result.values.items():
            state[holder] = value
            seqs[(holder, op_id)] = value.seq  # the entry just logged (eviction only takes older ones)

    for holder in holders:
        try:
            state[holder] = _with_residuals(holder, starts[holder], state[holder], sides, new, held_by, devices,
                                            items)
        except HoldingsError as error:
            items.append(ReviewItem(error.reason, _ref(holder), error.detail[:_DETAIL]))
    if items:
        return Review(tuple(items))
    files: dict[HoldingsRef, Output] = {
        HoldingsRef.of(holder): _output(holder, state[holder], envelopes[holder], base, sides, devices)
        for holder in holders}
    for holder, (source, version) in whole.items():
        if holder not in replayed:
            files[HoldingsRef.of(holder)] = Kept(version.etag, source)
            state[holder] = version.value
    files = {ref: files[ref] for ref in sorted(files, key=str)}
    return HoldingsMerge(files, {holder: state[holder] for holder in sorted(state, key=str)}, seqs, tuple(sequence))


def _whole(base: Mapping[InstanceRef, Version], sides: Mapping[str, Mapping[InstanceRef, Version]],
           holders: list[InstanceRef], devices: list[str], order: Mapping[str, int]
           ) -> dict[InstanceRef, tuple[str, Version]]:
    """The holder files one device changed and the other did not (or both alike): taken whole, unreplayed.

    Why: a file only one device wrote is already that device's consistent result, and replaying it could
    only lose what its log no longer shows (entries evicted after the agreed seq), so a merged file merged
    again, or merged with a device that did not touch it, stays as it is. Only files both devices changed
    differently, and those sharing an operation with them, are replayed.
    """
    def same(a: Version, b: Version) -> bool:
        return a.value == b.value and a.meta == b.meta

    found = {}
    for holder in holders:
        versions = {device: sides[device][holder] for device in devices if holder in sides[device]}
        agreed = base.get(holder)
        pick = choose(agreed, versions, order, same)
        if pick is not None:
            found[holder] = (pick, agreed if pick == BASE else versions[pick])
    return found  # type: ignore[return-value]


def _operations(new: Mapping[str, Mapping[InstanceRef, list[LogEntry]]], devices: list[str],
                items: list[ReviewItem]) -> tuple[dict[str, Operation], dict[str, set[str]], dict[str, str]]:
    """Every new operation once, by op id: which devices log it, and its ``at`` (the lower-ranked device's).

    One operation logs one entry on each holder it touches, on the same device, with the other holders'
    seqs as its peers; one op id with two contents (lines, note or cause) is a collision.
    """
    ops: dict[str, Operation] = {}
    held_by: dict[str, set[str]] = {}
    at: dict[str, str] = {}
    for device in devices:
        landed: dict[str, dict[InstanceRef, LogEntry]] = {}
        for holder, entries in new[device].items():
            for entry in entries:
                landed.setdefault(entry.op, {})[holder] = entry
        for op_id in sorted(landed):
            logged = landed[op_id]
            op = next(iter(logged.values())).operation
            if any(entry.operation != op for entry in logged.values()) or (op_id in ops and ops[op_id] != op):
                items.append(ReviewItem("id_collision", op_id, "one operation id with two contents"))
                continue
            missing = sorted(str(holder) for holder in op.holders if holder not in logged)
            if missing:
                items.append(ReviewItem("partial_op", op_id, f"{device} does not log it on {', '.join(missing)}"))
                continue
            seqs = {holder: entry.seq for holder, entry in logged.items()}
            if any(dict(entry.peers) != {h: s for h, s in seqs.items() if h != holder}
                   for holder, entry in logged.items()):
                items.append(ReviewItem("log_diverged", op_id, f"{device}: its entries name other peers"))
                continue
            ops[op_id] = op
            held_by.setdefault(op_id, set()).add(device)
            at.setdefault(op_id, next(iter(logged.values())).at)
    return ops, held_by, at


def _constraints(new: Mapping[str, Mapping[InstanceRef, list[LogEntry]]], devices: list[str]) -> dict[str, set[str]]:
    """``after[b]``: the operations that must come before ``b``: one device's order on one holder."""
    after: dict[str, set[str]] = {}
    for device in devices:
        for entries in new[device].values():
            for first, second in pairwise(entries):
                if first.op != second.op:
                    after.setdefault(second.op, set()).add(first.op)
    return after


def _topological(ops: Mapping[str, Operation], after: Mapping[str, set[str]], held_by: Mapping[str, set[str]],
                 order: Mapping[str, int], key: Key) -> list[str] | ReviewItem:
    """One deterministic order: every device's per-holder order kept, ties by key, device rank, op id."""
    waiting = {op_id: set(after.get(op_id, ())) & set(ops) for op_id in ops}
    followers: dict[str, list[str]] = {}
    for op_id, before in waiting.items():
        for first in before:
            followers.setdefault(first, []).append(op_id)

    def priority(op_id: str) -> tuple:
        return (key(ops[op_id]), min(order[device] for device in held_by[op_id]), op_id)

    ready = [(priority(op_id), op_id) for op_id, before in waiting.items() if not before]
    heapq.heapify(ready)
    sequence: list[str] = []
    while ready:
        _, op_id = heapq.heappop(ready)
        sequence.append(op_id)
        for follower in followers.get(op_id, ()):
            waiting[follower].discard(op_id)
            if not waiting[follower]:
                heapq.heappush(ready, (priority(follower), follower))
    if len(sequence) != len(ops):
        stuck = sorted(set(ops) - set(sequence))
        return ReviewItem("log_diverged", stuck[0], "the devices' logs order these operations both ways: "
                          + ", ".join(stuck[:8]))
    return sequence


def _envelopes(base: Mapping[InstanceRef, Version], sides: Mapping[str, Mapping[InstanceRef, Version]],
               holders: list[InstanceRef], devices: list[str], order: Mapping[str, int],
               items: list[ReviewItem]) -> tuple[dict[InstanceRef, tuple], dict[InstanceRef, object]]:
    """Each merged file's envelope ``(owner, extra, previous)`` and payload extras, three-way."""
    envelopes, extras = {}, {}
    for holder in holders:
        agreed = base.get(holder)
        versions = {device: sides[device][holder] for device in devices if holder in sides[device]}
        if not versions:
            continue  # only the agreed file: both devices deleted it, already reported
        meta = choose(agreed, versions, order, lambda a, b: a.meta == b.meta)
        kept = choose(agreed, versions, order, lambda a, b: a.value.extras == b.value.extras)
        if meta is None or kept is None:
            items.append(ReviewItem("both_changed", _ref(holder), "its envelope or unknown fields"))
            continue
        owner, extra = (agreed if meta == BASE else versions[meta]).meta  # type: ignore[union-attr]
        previous = agreed.etag if agreed is not None else versions[min(versions, key=order.__getitem__)].etag
        envelopes[holder] = (owner, extra, previous)
        extras[holder] = (agreed if kept == BASE else versions[kept]).value.extras  # type: ignore[union-attr]
    return envelopes, extras


def _with_residuals(holder: InstanceRef, start: Holdings, replayed: Holdings,
                    sides: Mapping[str, Mapping[InstanceRef, Version]],
                    new: Mapping[str, Mapping[InstanceRef, list[LogEntry]]], held_by: Mapping[str, set[str]],
                    devices: list[str], items: list[ReviewItem]) -> Holdings:
    """The replayed holdings plus each device's hand edits: its final count less the replay of its own ops.

    A hand edit is kept where the other device did not touch that count: no residual of its own and no
    operation only it logged. Two equal residuals on a count no one-sided operation touched are one edit
    (a result both devices already hold). Anything else is ``hand_edit_overlap``: whose edit wins, or
    whether it goes on top of the other device's operations, is the owner's decision.
    """
    residuals: dict[str, dict[str, int]] = {}
    touches: dict[str, set[str]] = {}
    for device in devices:
        version = sides[device].get(holder)
        entries = new[device].get(holder, [])
        own = [entry for entry in entries if held_by.get(entry.op) == {device}]
        touches[device] = _touched(own, holder)
        if version is None:
            residuals[device] = {}
            continue
        deltas = _deltas(entries, holder)
        final = version.value
        names = set(start.stacks) | set(final.stacks) | set(deltas)
        residuals[device] = {item: delta for item in sorted(names)
                             if (delta := final.count(item) - start.count(item) - deltas.get(item, 0))}
    one, other = devices
    stacks = dict(replayed.stacks)
    for item in sorted(set(residuals[one]) | set(residuals[other])):
        first, second = residuals[one].get(item, 0), residuals[other].get(item, 0)
        if first == second:
            if touches[one] & {item} or touches[other] & {item}:
                items.append(ReviewItem("hand_edit_overlap", _ref(holder), f"{item}: edited by hand on both devices"))
                continue
            delta = first
        elif first and second:
            items.append(ReviewItem("hand_edit_overlap", _ref(holder), f"{item}: edited by hand on both devices"))
            continue
        else:
            edited, by = (one, first) if first else (other, second)
            if item in touches[other if edited == one else one]:
                items.append(ReviewItem("hand_edit_overlap", _ref(holder),
                                        f"{item}: edited by hand on {edited}, changed by operations on the other"))
                continue
            delta = by
        count = stacks.get(item, 0) + delta
        if count < 0:
            raise HoldingsRefused("insufficient", f"{holder}'s hand edit of {item} leaves {count}")
        if count > MAX_COUNT:
            raise HoldingsRefused("count_limit", f"{holder} would hold {count} {item}, above {MAX_COUNT}")
        if count:
            stacks[item] = count
        else:
            stacks.pop(item, None)
    if len(stacks) > MAX_STACKS:
        raise HoldingsRefused("stack_limit", f"{holder} would hold more than {MAX_STACKS} stacks")
    return replace(replayed, stacks=stacks)


def _output(holder: InstanceRef, value: Holdings, envelope: tuple, base: Mapping[InstanceRef, Version],
            sides: Mapping[str, Mapping[InstanceRef, Version]], devices: list[str]) -> Output:
    """An existing version when one already is the merged file (the agreed one first), else a rewrite."""
    owner, extra, previous = envelope
    candidates = [(BASE, base.get(holder))] + [(device, sides[device].get(holder)) for device in devices]
    for source, version in candidates:
        if version is not None and version.value == value and version.meta == (owner, extra):
            return Kept(version.etag, source)
    return Rewrite(HOLDINGS_SCHEMA, holdings_to_payload(value), owner, extra, previous)
