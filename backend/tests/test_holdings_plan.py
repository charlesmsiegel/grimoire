"""The deterministic counted-holdings mutator and holder-only context lines (Sprint 023)."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.holdings import (
    MAX_FILE, AlreadyApplied, Applied, Holdings, HoldingsRefused, Line, LogEntry, Operation, context_lines, plan,
)

from holdings_fixtures import AT, CHEST, GREAT, MARA, ORDINARY, STRONG, TOMA, JsonSizer, ZeroSizer, everyone


def run(op, *states, at=AT, exists=everyone, size=None, basis=None, max_file=MAX_FILE):
    current = {holder: None for holder in op.holders}
    current.update({state.holder: state for state in states})
    return plan(op, current, at, exists=exists, size=size or ZeroSizer(), basis=basis, max_file=max_file)


def apply(op, *states, **kwargs) -> dict[InstanceRef, Holdings]:
    result = run(op, *states, **kwargs)
    assert isinstance(result, Applied)
    return dict(result.values)


def refused(op, *states, **kwargs) -> HoldingsRefused:
    with pytest.raises(HoldingsRefused) as error:
        run(op, *states, **kwargs)
    return error.value


def op(op_id, *lines, **kwargs) -> Operation:
    return Operation(op_id, tuple(lines), **kwargs)


def proving() -> Holdings:
    mara = Holdings.empty(MARA)
    for step in (op("a1", Line.add(MARA, ORDINARY, 8)), op("a2", Line.add(MARA, STRONG, 2)),
                 op("c1", Line.consume(MARA, ORDINARY, 3)), op("a3", Line.add(MARA, ORDINARY, 4))):
        mara = apply(step, mara)[MARA]
    return mara


def test_proving_counts():
    mara = proving()
    assert dict(mara.stacks) == {ORDINARY: 9, STRONG: 2} and mara.seq == 4
    assert [entry.op for entry in mara.log] == ["a1", "a2", "c1", "a3"]
    assert mara.entry("c1").after == {ORDINARY: 5}


def test_replay_is_a_noop_and_reuse_is_refused():
    mara = proving()
    again = run(op("c1", Line.consume(MARA, ORDINARY, 3)), mara)
    assert isinstance(again, AlreadyApplied) and again.entries == {MARA: mara.entry("c1")}
    assert refused(op("c1", Line.consume(MARA, ORDINARY, 4)), mara).reason == "op_id_reused"
    assert refused(op("c1", Line.consume(MARA, ORDINARY, 3), note="x"), mara).reason == "op_id_reused"


def test_insufficient_stale_and_limits():
    mara = proving()
    assert refused(op("c2", Line.consume(MARA, ORDINARY, 15)), mara).reason == "insufficient"
    assert refused(op("c3", Line.consume(MARA, GREAT, 1)), mara).reason == "insufficient"
    assert refused(op("j1", Line.adjust(MARA, ORDINARY, 8, 1)), mara).reason == "stale_count"
    assert refused(op("a4", Line.add(MARA, ORDINARY, 10 ** 12 - 8)), mara).reason == "count_limit"
    full = Holdings(MARA, stacks={f"item-{n}": 1 for n in range(1024)})
    assert refused(op("a5", Line.add(MARA, "item-new", 1)), full).reason == "stack_limit"
    assert isinstance(run(op("a6", Line.add(MARA, "item-0", 1)), full), Applied)


def test_adjust_to_zero_removes_the_stack_and_logs():
    mara = apply(op("j1", Line.adjust(MARA, ORDINARY, 9, 0)), proving())[MARA]
    assert ORDINARY not in mara.stacks and mara.count(ORDINARY) == 0
    assert mara.entry("j1").after == {ORDINARY: 0} and mara.seq == 5


def test_move_and_batch_are_atomic():
    toma = Holdings(TOMA, 3, 0, {STRONG: 1})
    values = apply(op("m1", Line.move(MARA, TOMA, ORDINARY, 2)), proving(), toma)
    assert values[MARA].count(ORDINARY) == 7 and values[TOMA].count(ORDINARY) == 2
    assert values[MARA].entry("m1").peers == {TOMA: 4} and values[TOMA].entry("m1").peers == {MARA: 5}
    assert values[TOMA].entry("m1").after == {ORDINARY: 2}
    craft = op("k1", Line.consume(MARA, ORDINARY, 2), Line.consume(MARA, STRONG, 1), Line.add(MARA, GREAT, 1))
    assert dict(apply(craft, proving())[MARA].stacks) == {ORDINARY: 7, STRONG: 1, GREAT: 1}
    mara = proving()
    over = op("k2", Line.consume(MARA, ORDINARY, 2), Line.consume(MARA, STRONG, 3), Line.add(MARA, GREAT, 1))
    assert refused(over, mara).reason == "insufficient" and mara == proving()
    trade = op("t1", Line.move(MARA, TOMA, ORDINARY, 3), Line.move(TOMA, MARA, STRONG, 1))
    values = apply(trade, proving(), toma)
    assert dict(values[MARA].stacks) == {ORDINARY: 6, STRONG: 3} and dict(values[TOMA].stacks) == {ORDINARY: 3}


def test_several_lines_on_one_holder_apply_in_order_and_log_once():
    mara = apply(op("x1", Line.add(MARA, GREAT, 1), Line.consume(MARA, GREAT, 1)), Holdings.empty(MARA))[MARA]
    assert len(mara.log) == 1 and mara.stacks == {} and mara.entry("x1").after == {GREAT: 0}
    assert refused(op("x2", Line.consume(MARA, GREAT, 1), Line.add(MARA, GREAT, 1)),
                   Holdings.empty(MARA)).reason == "insufficient"


def _logged_move(peer_seq: int = 3) -> Holdings:
    entry = LogEntry("m1", 5, AT, (Line.move(MARA, TOMA, ORDINARY, 2),), {TOMA: peer_seq}, {ORDINARY: 7})
    return Holdings(MARA, 5, 4, {ORDINARY: 7}, (entry,))


MOVE = op("m1", Line.move(MARA, TOMA, ORDINARY, 2))


def test_partial_and_beyond_history():
    error = refused(MOVE, _logged_move(), Holdings(TOMA, 2, 0))
    assert error.reason == "partial_op" and "toma-1" in error.detail


def test_one_side_evicted_is_beyond_history():
    assert refused(MOVE, _logged_move(), Holdings(TOMA, 3, 3)).reason == "beyond_history"


def test_seq_reuse_is_beyond_history():
    # toma never logged m1; its seq 3 went to another operation, which was later evicted.
    assert refused(MOVE, _logged_move(), Holdings(TOMA, 9, 4)).reason == "beyond_history"


def test_a_three_holder_op_with_one_evicted_side_is_beyond_history():
    lines = (Line.move(MARA, TOMA, ORDINARY, 1), Line.move(MARA, CHEST, ORDINARY, 1))
    entry = LogEntry("s1", 5, AT, lines, {TOMA: 3, CHEST: 8}, {ORDINARY: 7})
    mara = Holdings(MARA, 5, 4, {ORDINARY: 7}, (entry,))
    assert refused(op("s1", *lines), mara, Holdings(TOMA, 2, 0), Holdings(CHEST, 9, 8)).reason == "beyond_history"
    assert refused(op("s1", *lines), mara, Holdings(TOMA, 2, 0), Holdings(CHEST, 7, 0)).reason == "partial_op"


def test_disagreeing_peers_take_the_conservative_seq():
    """After a reset or hand edit, logged holders can disagree about a missing holder's seq: the lowest decides."""
    lines = (Line.move(MARA, CHEST, ORDINARY, 1), Line.move(TOMA, CHEST, ORDINARY, 1))
    mara = Holdings(MARA, 5, 4, {ORDINARY: 7}, (LogEntry("s1", 5, AT, lines, {CHEST: 3, TOMA: 2}, {ORDINARY: 7}),))
    toma = Holdings(TOMA, 2, 1, {ORDINARY: 1}, (LogEntry("s1", 2, AT, lines, {CHEST: 9, MARA: 5}, {ORDINARY: 1}),))
    chest = Holdings(CHEST, 9, 4)  # evicted past 3, not past 9: only the lower peer seq says it may have landed
    assert refused(op("s1", *lines), mara, toma, chest).reason == "beyond_history"


def test_a_restored_file_forgets_its_operations():
    """The documented limit: a restored single-holder file has no record of the op, so a retry applies again."""
    before = Holdings(MARA, 3, 0, {ORDINARY: 10})
    spent = apply(op("spend-1", Line.consume(MARA, ORDINARY, 3)), before, basis={MARA: 3})[MARA]
    assert spent.count(ORDINARY) == 7
    restored = before  # a backup restore or a sync resolution brings back the older file
    again = apply(op("spend-1", Line.consume(MARA, ORDINARY, 3)), restored, basis={MARA: 3})[MARA]
    assert again.count(ORDINARY) == 7  # applied once relative to the restored file, not refused


def test_a_logged_entry_without_a_peer_is_beyond_history():
    entry = LogEntry("m1", 5, AT, (Line.move(MARA, TOMA, ORDINARY, 2),), {}, {ORDINARY: 7})
    mara = Holdings(MARA, 5, 4, {ORDINARY: 7}, (entry,))
    assert refused(MOVE, mara, Holdings(TOMA, 2, 0)).reason == "beyond_history"


def test_holder_check_comes_after_replay():
    mara = proving()
    assert isinstance(run(op("c1", Line.consume(MARA, ORDINARY, 3)), mara, exists=lambda r: False), AlreadyApplied)
    assert refused(op("c9", Line.consume(MARA, ORDINARY, 3)), mara, exists=lambda r: False).reason == "missing_holder"
    assert refused(MOVE, proving(), exists=lambda r: r != TOMA).reason == "missing_holder"


def test_basis_boundary():
    log = tuple(LogEntry(f"e-{n}", n, AT, (Line.add(MARA, ORDINARY, 1),), {}, {ORDINARY: 1}) for n in (6, 7))
    mara = Holdings(MARA, 7, 5, {ORDINARY: 9}, log)
    new = op("n1", Line.consume(MARA, ORDINARY, 1))
    assert refused(new, mara, basis={MARA: 4}).reason == "beyond_history"
    assert isinstance(run(new, mara, basis={MARA: 5}), Applied)
    assert isinstance(run(new, mara, basis={MARA: 7}), Applied)


def test_basis_catches_a_restored_recreated_or_holed_file():
    log = tuple(LogEntry(f"e-{n}", n, AT, (Line.add(MARA, ORDINARY, 1),), {}, {ORDINARY: 1}) for n in (1, 2, 3))
    spend = op("spend-1", Line.consume(MARA, ORDINARY, 3))
    older = Holdings(MARA, 2, 0, {ORDINARY: 12}, log[:2])  # a backup from before the basis (seq 5) was recorded
    assert refused(spend, older, basis={MARA: 5}).reason == "beyond_history"
    assert refused(spend, basis={MARA: 5}).reason == "beyond_history"  # the file was deleted: seq 0
    holed = Holdings(MARA, 3, 0, {ORDINARY: 12}, (log[0], log[2]))  # seq 2 removed by hand
    assert refused(spend, holed, basis={MARA: 1}).reason == "beyond_history"
    assert isinstance(run(spend, holed, basis={MARA: 2}), Applied)  # the hole is before the basis


def test_plan_refuses_mis_keyed_state():
    with pytest.raises(ValueError):
        plan(op("a1", Line.add(MARA, ORDINARY, 1)), {MARA: Holdings(TOMA)}, AT, exists=everyone, size=ZeroSizer())


def test_a_later_small_op_keeps_the_eviction_boundary():
    mara = Holdings(MARA, 20, 0, {ORDINARY: 20}, _entries(MARA, 20))
    evicted = apply(op("n1", Line.add(MARA, ORDINARY, 1)), mara, size=Linear(), max_file=1000)[MARA]
    assert evicted.evicted_through == 11
    later = apply(op("n2", Line.add(MARA, ORDINARY, 1)), evicted, size=Linear(), max_file=2000)[MARA]
    assert later.evicted_through == 11  # nothing evicted now: the old boundary stays
    assert refused(op("e-3", Line.add(MARA, ORDINARY, 1)), later, basis={MARA: 2}).reason == "beyond_history"


def test_evicted_retry_without_basis_applies_again():
    """The documented D2 limit: once an entry is evicted, a retry without a basis is a fresh operation."""
    mara = Holdings(MARA, 7, 5, {ORDINARY: 9})  # op "c1" was seq 3, long evicted
    assert apply(op("c1", Line.consume(MARA, ORDINARY, 3)), mara)[MARA].count(ORDINARY) == 6


def _entries(holder: InstanceRef, count: int, lines_per: int = 1, item: str = ORDINARY) -> tuple[LogEntry, ...]:
    return tuple(LogEntry(f"e-{n}", n, AT, tuple(Line.add(holder, item, 1) for _ in range(lines_per)), {},
                          {item: 1}) for n in range(1, count + 1))


def test_log_is_bounded_to_1000():
    mara = Holdings(MARA, 999, 0, {ORDINARY: 999}, _entries(MARA, 999))
    for n in (1, 2):
        mara = apply(op(f"new-{n}", Line.add(MARA, ORDINARY, 1)), mara)[MARA]
    assert len(mara.log) == 1000 and mara.evicted_through == 1 and mara.seq == 1001
    assert mara.log[0].op == "e-2" and mara.log[-1].op == "new-2"


class Linear:
    def __init__(self, per: int = 100, estimate: int | None = None, base: int = 0) -> None:
        self.per, self.estimate, self.base = per, estimate if estimate is not None else per, base

    def document(self, holdings):
        return self.base + self.per * len(holdings.log)

    def entry(self, holdings, entry):
        return self.estimate


def test_eviction_respects_size():
    mara = Holdings(MARA, 20, 0, {ORDINARY: 20}, _entries(MARA, 20))
    grown = apply(op("n1", Line.add(MARA, ORDINARY, 1)), mara, size=Linear(), max_file=1000)[MARA]
    assert len(grown.log) == 10 and grown.evicted_through == 11 and grown.log[-1].op == "n1"
    for estimate in (1, 10_000, 0):  # estimates that err either way, or say nothing, are calibrated
        other = apply(op("n1", Line.add(MARA, ORDINARY, 1)), mara, size=Linear(estimate=estimate),
                      max_file=1000)[MARA]
        assert other == grown
    error = refused(op("n1", Line.add(MARA, ORDINARY, 1)), mara, size=Linear(base=2000), max_file=1000)
    assert error.reason == "too_large"


class Uneven(Linear):
    def entry(self, holdings, entry):  # estimates that grow with age: wrong by a different factor each time
        return entry.seq


class OldHeavy(Linear):
    def entry(self, holdings, entry):  # old entries look huge, new ones tiny: calibration evicts too few
        return 1000 if entry.seq <= 5 else 1


def test_undershooting_calibration_is_confirmed_and_corrected():
    mara = Holdings(MARA, 20, 0, {ORDINARY: 20}, _entries(MARA, 20))
    sizer = OldHeavy()
    grown = apply(op("n1", Line.add(MARA, ORDINARY, 1)), mara, size=sizer, max_file=1000)[MARA]
    assert sizer.document(grown) <= 1000 and len(grown.log) == 10 and grown.log[-1].op == "n1"


def test_one_summary_row_per_holder_and_item():
    result = run(op("d1", Line.consume(MARA, ORDINARY, 1), Line.consume(MARA, ORDINARY, 2)), proving())
    assert [(row.item, row.before, row.after) for row in result.summary] == [(ORDINARY, 9, 6)]


def test_a_stack_created_and_emptied_in_one_op_is_not_new():
    result = run(op("d2", Line.add(MARA, GREAT, 1), Line.consume(MARA, GREAT, 1)), proving())
    assert [(row.item, row.before, row.after, row.new_stack) for row in result.summary] == [(GREAT, 0, 0, False)]


def test_uneven_estimates_never_over_evict():
    mara = Holdings(MARA, 20, 0, {ORDINARY: 20}, _entries(MARA, 20))
    grown = apply(op("n1", Line.add(MARA, ORDINARY, 1)), mara, size=Uneven(), max_file=1000)[MARA]
    assert len(grown.log) == 10 and grown.log[-1].op == "n1"


def test_worst_case_file_stays_under_the_bound():
    started = time.monotonic()
    item = lambda n: f"i{n:04d}-" + "x" * 122  # noqa: E731 -- 128-character item keys
    stacks = {item(n): 10 ** 6 for n in range(1024)}
    extras = {"x-attachments": "y" * 200_000}
    lines = tuple(Line.add(MARA, item(n), 1) for n in range(32))
    sizer = JsonSizer()
    probe = Holdings(MARA, 1, 0, stacks, (LogEntry("p", 1, AT, lines, {}, {}),), extras)
    per_entry = sizer.document(probe) - sizer.document(replace(probe, log=()))
    count = (MAX_FILE - sizer.document(replace(probe, log=()))) // per_entry - 2
    log = tuple(LogEntry(f"e-{n}", n, AT, lines, {}, {}) for n in range(1, count + 1))
    mara = Holdings(MARA, count, 0, stacks, log, extras)
    assert MAX_FILE - 3 * per_entry < sizer.document(mara) <= MAX_FILE
    for n in range(5):
        mara = apply(op(f"w-{n}", *lines), mara, size=sizer)[MARA]
        assert sizer.document(mara) <= MAX_FILE and mara.log[-1].op == f"w-{n}"
    assert mara.evicted_through > 0
    assert time.monotonic() - started < 30


def test_summary_flags_new_stacks():
    result = run(op("s1", Line.add(MARA, ORDINARY, 1), Line.add(MARA, "ordnary-ball", 1)), proving())
    rows = {(row.item, row.before, row.after, row.new_stack) for row in result.summary}
    assert rows == {(ORDINARY, 9, 10, False), ("ordnary-ball", 0, 1, True)}


def test_context_lines_are_holder_only_sorted_and_bounded():
    mara = proving()
    assert context_lines(mara) == (f"{ORDINARY}: 9", f"{STRONG}: 2")
    three = Holdings(MARA, stacks={"c": 3, "a": 1, "b": 2})
    assert context_lines(three, limit=2) == ("a: 1", "b: 2", "… and 1 more")
    assert context_lines(three, limit=3) == ("a: 1", "b: 2", "c: 3")  # exactly the limit: nothing elided
    noted = apply(op("n1", Line.add(MARA, ORDINARY, 1), note="from the smuggler"), mara)[MARA]
    assert not any("smuggler" in line or "n1" in line for line in context_lines(noted))
    with pytest.raises(ValueError):
        context_lines(mara, limit=0)


def test_plan_needs_every_holder_and_a_valid_time():
    with pytest.raises(ValueError):
        plan(MOVE, {MARA: None}, AT, exists=everyone, size=ZeroSizer())
    with pytest.raises(HoldingsRefused):
        run(op("t1", Line.add(MARA, ORDINARY, 1)), at="yesterday")
