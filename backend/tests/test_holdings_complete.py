"""023's ``complete`` (Sprint 030): finish an operation that landed on some holders only, at its exact seqs."""

from __future__ import annotations

import pytest

from grimoire.holdings import AlreadyApplied, Applied, Holdings, HoldingsRefused, Line, Operation, complete, plan

from holdings_fixtures import AT, CHEST, MARA, ORDINARY, TOMA, ZeroSizer, everyone

LATER = "2026-11-02T10:00:00Z"


def _seeded() -> dict:
    found = plan(Operation("seed", (Line.add(MARA, ORDINARY, 5), Line.add(TOMA, ORDINARY, 1))),
                 {MARA: None, TOMA: None}, AT, exists=everyone, size=ZeroSizer())
    return dict(found.values)  # type: ignore[union-attr]


def _landed(op: Operation, state: dict) -> dict:
    """``op`` applied to every holder: the state the original commit would have written."""
    result = plan(op, {holder: state.get(holder) for holder in op.holders}, AT, exists=everyone, size=ZeroSizer())
    return dict(result.values)  # type: ignore[union-attr]


MOVE = Operation("give-1", (Line.move(MARA, TOMA, ORDINARY, 2),), note="a gift", cause="effect:accepted/e-1")


def _complete(op, current, seqs):  # type: ignore[no-untyped-def]
    return complete(op, current, LATER, seqs=seqs, exists=everyone, size=ZeroSizer())


@pytest.mark.parametrize("landed_side", [MARA, TOMA])
def test_a_move_landed_on_one_side_is_completed_on_the_other(landed_side):
    before = _seeded()
    after = _landed(MOVE, before)
    seqs = {holder: after[holder].seq for holder in MOVE.holders}
    current = {holder: after[holder] if holder == landed_side else before[holder] for holder in MOVE.holders}
    result = _complete(MOVE, current, seqs)
    assert isinstance(result, Applied)
    (missing,) = set(MOVE.holders) - {landed_side}
    assert set(result.values) == {missing}
    value = result.values[missing]
    assert value.stacks == after[missing].stacks and value.seq == seqs[missing]
    entry = value.log[-1]
    assert (entry.op, entry.seq, entry.peers, entry.after, entry.note, entry.cause) == (
        "give-1", seqs[missing], {landed_side: seqs[landed_side]}, after[missing].log[-1].after, "a gift",
        "effect:accepted/e-1")
    assert [(row.holder, row.before, row.after) for row in result.summary] == [
        (missing, before[missing].count(ORDINARY), after[missing].count(ORDINARY))]


def test_landed_everywhere_is_already_applied_and_nowhere_is_applied_whole():
    before = _seeded()
    after = _landed(MOVE, before)
    seqs = {holder: after[holder].seq for holder in MOVE.holders}
    assert isinstance(_complete(MOVE, after, seqs), AlreadyApplied)
    result = _complete(MOVE, before, seqs)
    assert isinstance(result, Applied)
    assert {holder: value.stacks for holder, value in result.values.items()} == {
        holder: after[holder].stacks for holder in MOVE.holders}


def test_a_first_holder_file_that_never_landed_is_written_from_nothing():
    op = Operation("find-1", (Line.add(CHEST, ORDINARY, 2), Line.add(MARA, ORDINARY, 1)))
    before = _seeded()
    after = _landed(op, {MARA: before[MARA]})
    result = _complete(op, {CHEST: None, MARA: after[MARA]}, {CHEST: 1, MARA: after[MARA].seq})
    assert result.values[CHEST].stacks == {ORDINARY: 2} and result.values[CHEST].seq == 1  # type: ignore[union-attr]


def test_a_holder_that_moved_on_or_was_restored_further_back_is_refused():
    before = _seeded()
    after = _landed(MOVE, before)
    seqs = {holder: after[holder].seq for holder in MOVE.holders}
    later = _landed(Operation("other", (Line.add(TOMA, ORDINARY, 1),)), {TOMA: before[TOMA]})[TOMA]
    with pytest.raises(HoldingsRefused) as raised:  # Toma never got the gift but changed since
        _complete(MOVE, {MARA: after[MARA], TOMA: later}, seqs)
    assert raised.value.reason == "seq_moved"
    with pytest.raises(HoldingsRefused) as raised:  # Toma restored from before the seed
        _complete(MOVE, {MARA: after[MARA], TOMA: None}, seqs)
    assert raised.value.reason == "seq_moved"


def test_a_landed_holder_that_moved_on_since_is_refused_so_a_repair_is_never_doubled():
    before = _seeded()
    after = _landed(MOVE, before)
    seqs = {holder: after[holder].seq for holder in MOVE.holders}
    repaired = _landed(Operation("repair", (Line.add(MARA, ORDINARY, 2),)), {MARA: after[MARA]})[MARA]
    with pytest.raises(HoldingsRefused) as raised:  # a human gave Mara her coins back by hand
        _complete(MOVE, {MARA: repaired, TOMA: before[TOMA]}, seqs)
    assert raised.value.reason == "seq_moved"
    assert isinstance(_complete(MOVE, {MARA: repaired, TOMA: after[TOMA]}, seqs), AlreadyApplied)  # replay still is


def test_a_landed_entry_at_another_seq_or_with_other_peers_or_content_is_refused():
    before = _seeded()
    after = _landed(MOVE, before)
    seqs = {holder: after[holder].seq for holder in MOVE.holders}
    with pytest.raises(HoldingsRefused) as raised:
        _complete(MOVE, {MARA: after[MARA], TOMA: before[TOMA]}, {MARA: seqs[MARA] + 1, TOMA: seqs[TOMA]})
    assert raised.value.reason == "seq_moved"
    with pytest.raises(HoldingsRefused) as raised:
        _complete(MOVE, {MARA: after[MARA], TOMA: before[TOMA]}, {MARA: seqs[MARA], TOMA: seqs[TOMA] + 1})
    assert raised.value.reason == "seq_moved"  # Mara's peers name another seq for Toma
    other = Operation("give-1", MOVE.lines, note="edited", cause=MOVE.cause)
    with pytest.raises(HoldingsRefused) as raised:
        _complete(other, {MARA: after[MARA], TOMA: before[TOMA]}, seqs)
    assert raised.value.reason == "op_id_reused"


@pytest.mark.parametrize("seqs, reason", [
    ({MARA: 2}, "invalid_field"), ({MARA: 2, TOMA: 0}, "invalid_field"), ({MARA: 2, TOMA: "3"}, "invalid_field"),
    ({MARA: 2, TOMA: 2, CHEST: 1}, "invalid_field"), ([], "invalid_field"),
])
def test_seqs_must_name_exactly_the_holders_as_positive_integers(seqs, reason):
    with pytest.raises(HoldingsRefused) as raised:
        _complete(MOVE, _seeded(), seqs)
    assert raised.value.reason == reason


def test_line_refusals_and_missing_holders_pass_through():
    before = _seeded()
    after = _landed(MOVE, before)
    seqs = {holder: after[holder].seq for holder in MOVE.holders}
    poor = Holdings(MARA, seq=before[MARA].seq, stacks={ORDINARY: 1}, log=before[MARA].log)
    with pytest.raises(HoldingsRefused) as raised:  # Mara's file was restored with fewer balls than she gave
        _complete(MOVE, {MARA: poor, TOMA: after[TOMA]}, seqs)
    assert raised.value.reason == "insufficient"
    with pytest.raises(HoldingsRefused) as raised:
        complete(MOVE, {MARA: after[MARA], TOMA: before[TOMA]}, LATER, seqs=seqs, exists=lambda ref: ref != TOMA,
                 size=ZeroSizer())
    assert raised.value.reason == "missing_holder"
    with pytest.raises(TypeError):
        complete("give-1", before, LATER, seqs=seqs, exists=everyone, size=ZeroSizer())  # type: ignore[arg-type]
