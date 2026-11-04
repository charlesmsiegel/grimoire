"""The pure counted-holdings model and its ``grimoire.holdings/1`` codec (Sprint 023)."""

from __future__ import annotations

import copy

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.holdings import (
    HOLDINGS_SCHEMA, Holdings, HoldingsCodecError, HoldingsRefused, Line, LogEntry, Operation,
    holdings_from_payload, holdings_to_payload,
)

from holdings_fixtures import AT, CHEST, MARA, ORDINARY, OTHER, STRONG, TOMA


def sample() -> Holdings:
    move = LogEntry("m1", 2, AT, (Line.move(MARA, TOMA, ORDINARY, 2),), {TOMA: 1}, {ORDINARY: 6},
                    note="paid for the boat", cause="effect-7")
    add = LogEntry("a1", 1, "2026-11-01T09:59:59.123456Z", (Line.add(MARA, ORDINARY, 8),), {}, {ORDINARY: 8})
    return Holdings(MARA, 2, 0, {STRONG: 2, ORDINARY: 6}, (add, move), {"x-note": [1, {"k": True}]})


def payload() -> dict:
    return holdings_to_payload(sample())


def test_round_trip_is_canonical():
    value = sample()
    data = holdings_to_payload(value)
    assert holdings_from_payload(MARA, HOLDINGS_SCHEMA, data) == value
    assert [stack["item"] for stack in data["stacks"]] == [ORDINARY, STRONG]
    assert data["ops"][1] == {
        "op": "m1", "seq": 2, "at": AT, "note": "paid for the boat", "cause": "effect-7",
        "lines": [{"line": "move", "holder": "character/mara-1", "item": ORDINARY, "amount": 2,
                   "target": "character/toma-1"}],
        "peers": {"character/toma-1": 1}, "after": {ORDINARY: 6}}
    shuffled = copy.deepcopy(data)
    shuffled["stacks"].reverse()
    shuffled["ops"].reverse()
    assert holdings_to_payload(holdings_from_payload(MARA, HOLDINGS_SCHEMA, shuffled)) == data


def test_an_adjust_line_round_trips():
    entry = LogEntry("j1", 1, AT, (Line.adjust(CHEST, STRONG, 0, 3),), {}, {STRONG: 3})
    value = Holdings(CHEST, 1, 0, {STRONG: 3}, (entry,))
    assert holdings_from_payload(CHEST, HOLDINGS_SCHEMA, holdings_to_payload(value)) == value


def _edit(change):
    data = payload()
    change(data)
    return data


@pytest.mark.parametrize("change, reason", [
    (lambda d: d["ops"][0].update(extra=1), "unknown_field"),
    (lambda d: d["ops"][0]["lines"][0].update(extra=1), "unknown_field"),
    (lambda d: d["stacks"][0].update(label="x"), "unknown_field"),
    (lambda d: d["stacks"].append({"item": ORDINARY, "count": 1}), "invalid_field"),
    (lambda d: d["ops"][1].update(op="a1"), "invalid_field"),
    (lambda d: d["ops"][1].update(seq=3), "invalid_field"),
    (lambda d: d.update(evicted_through=1), "invalid_field"),
    (lambda d: d["stacks"][0].update(count=True), "invalid_field"),
    (lambda d: d["stacks"][0].update(count=1.0), "invalid_field"),
    (lambda d: d["stacks"][0].update(count=10 ** 12 + 1), "invalid_field"),
    (lambda d: d["ops"][0].update(at="2100-02-29T10:00:00Z"), "invalid_field"),
    (lambda d: d["ops"][0].update(at="2026-11-01T10:00:60Z"), "invalid_field"),
    (lambda d: d["ops"][0].update(at="2026-11-01T10:00:00.1Z"), "invalid_field"),
    (lambda d: d["ops"][0].update(at="2026-11-01T10:00:00+00:00"), "invalid_field"),
    (lambda d: d["ops"][0].update(at="2026-13-45T99:99:99Z"), "invalid_field"),
    (lambda d: d["ops"][0].update(at="0000-00-00T00:00:00Z"), "invalid_field"),
    (lambda d: d["ops"][0].update(at="2026-02-29T10:00:00Z"), "invalid_field"),
    (lambda d: d["ops"][0].update(at="2026-11-01T10:60:00Z"), "invalid_field"),
    (lambda d: d["ops"][1]["peers"].update({"character/mara-1": 1}), "invalid_field"),
    (lambda d: d["ops"][0]["lines"][0].update(holder="mara-1"), "invalid_field"),
    (lambda d: d["ops"][0]["lines"][0].update(line="steal"), "invalid_field"),
    (lambda d: d["ops"][0]["lines"][0].update(line={"x": 1}), "invalid_field"),
    (lambda d: d["ops"][0]["lines"][0].update(line=[{}]), "invalid_field"),
    (lambda d: d["ops"][0].update(lines=[{"line": {"x": 1}}]), "invalid_field"),
    (lambda d: d["ops"][0]["lines"][0].pop("amount"), "invalid_field"),
    (lambda d: d["ops"][1]["peers"].update({"character/ivo-1": 1}), "invalid_field"),
    (lambda d: d.update(holder="character/mara-1"), "identity_in_payload"),
    (lambda d: d.update(schema=HOLDINGS_SCHEMA), "identity_in_payload"),
    (lambda d: d.pop("ops"), "invalid_field"),
])
def test_known_structures_are_strict(change, reason):
    with pytest.raises(HoldingsCodecError) as error:
        holdings_from_payload(MARA, HOLDINGS_SCHEMA, _edit(change))
    assert error.value.reason == reason


def test_an_unknown_schema_is_refused():
    with pytest.raises(HoldingsCodecError) as error:
        holdings_from_payload(MARA, "grimoire.holdings/2", payload())
    assert error.value.reason == "unsupported_schema"


def test_hand_edits_decode_or_are_refused_never_repaired():
    reordered = _edit(lambda d: d["stacks"].reverse())
    assert holdings_from_payload(MARA, HOLDINGS_SCHEMA, reordered) == sample()
    zeroed = holdings_from_payload(MARA, HOLDINGS_SCHEMA, _edit(lambda d: d["stacks"][0].update(count=0)))
    assert ORDINARY not in zeroed.stacks and zeroed.count(ORDINARY) == 0  # "has none": no stack
    assert [stack["item"] for stack in holdings_to_payload(zeroed)["stacks"]] == [STRONG]  # dropped on write
    for change in (lambda d: d.pop("evicted_through"), lambda d: d["stacks"][0].update(count=-1),
                   lambda d: d["stacks"][0].update(count=False), lambda d: d["ops"].extend(
                       {**d["ops"][0], "op": f"x{n}", "seq": 3 + n} for n in range(1000))):
        with pytest.raises(HoldingsCodecError) as error:
            holdings_from_payload(MARA, HOLDINGS_SCHEMA, _edit(change))
        assert error.value.reason == "invalid_field"


def test_real_times_and_multiline_notes_are_accepted():
    for at in ("2028-02-29T23:59:59Z", "2000-02-29T00:00:00Z", "2026-12-31T00:00:00.000001Z"):
        LogEntry("a1", 1, at, (Line.add(MARA, ORDINARY, 1),))
    Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="line one\nline two\ttabbed — ünïcode ✓")
    for note in ("gave the \U0001f9d1\u200d\U0001f4bb a ball", "می\u200cخواهم", "co\u00adop", "n" * 500,
                 "zero\u200bwidth"):
        Operation("o1", (Line.add(MARA, ORDINARY, 1),), note=note)  # emoji ZWJ, Persian ZWNJ, soft hyphen
    Holdings(MARA, stacks={ORDINARY: 10 ** 12})


def test_a_logged_operation_must_touch_its_file_holder():
    data = _edit(lambda d: d["ops"][0]["lines"][0].update(holder="character/toma-1"))
    with pytest.raises(HoldingsCodecError):
        holdings_from_payload(MARA, HOLDINGS_SCHEMA, data)


def _refused(build) -> str:
    with pytest.raises(HoldingsRefused) as error:
        build()
    return error.value.reason


@pytest.mark.parametrize("build, reason", [
    (lambda: Line.add(MARA, ORDINARY, 0), "invalid_field"),
    (lambda: Line.add(MARA, ORDINARY, True), "invalid_field"),
    (lambda: Line.add(MARA, ORDINARY, 1.0), "invalid_field"),
    (lambda: Line.consume(MARA, ORDINARY, -1), "invalid_field"),
    (lambda: Line.add(MARA, ORDINARY, 10 ** 12 + 1), "invalid_field"),
    (lambda: Line.adjust(MARA, ORDINARY, 0, -1), "invalid_field"),
    (lambda: Line.add(MARA, "Ordinary", 1), "invalid_field"),
    (lambda: Line.move(MARA, MARA, ORDINARY, 1), "same_holder"),
    (lambda: Line("add", "campaign:c1/character/mara-1", ORDINARY, 1), "invalid_field"),
    (lambda: Operation("o1", tuple(Line.add(MARA, ORDINARY, 1) for _ in range(33))), "too_large"),
    (lambda: Operation("o1", tuple(Line.add(InstanceRef("c1", "character", f"h-{n}"), ORDINARY, 1)
                                   for n in range(9))), "too_large"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1), Line.add(OTHER, ORDINARY, 1))), "cross_campaign"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="n" * 501), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), cause="c" * 257), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="bad\x00"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="broken \ud83d emoji"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), cause="fx-\udc00"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="a\x85b"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="x\x9b31m"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="pay\u202eevil"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="isolate\u2066x\u2069"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), note="\ufeffbom"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), cause="fx-1\nforged"), "invalid_field"),
    (lambda: Operation("o1", (Line.add(MARA, ORDINARY, 1),), cause="fx\t1"), "invalid_field"),
    (lambda: Operation("O1", (Line.add(MARA, ORDINARY, 1),)), "invalid_field"),
    (lambda: Operation("o1", ()), "invalid_field"),
])
def test_operation_validation(build, reason):
    assert _refused(build) == reason


@pytest.mark.parametrize("key", ["holder", "schema", "seq", "ops"])
def test_extras_may_not_use_reserved_keys(key):
    assert _refused(lambda: Holdings(MARA, extras={key: "x"})) == "invalid_field"


def test_holdings_invariants():
    assert _refused(lambda: Holdings(MARA, 1, 2)) == "invalid_field"  # evicted_through above seq
    assert _refused(lambda: Holdings(MARA, stacks={ORDINARY: 0})) == "invalid_field"
    entry = LogEntry("a1", 3, AT, (Line.add(MARA, ORDINARY, 1),))
    assert _refused(lambda: Holdings(MARA, 2, 0, {}, (entry,))) == "invalid_field"  # seq beyond the record
    assert _refused(lambda: Holdings(MARA, 3, 3, {}, (entry,))) == "invalid_field"  # seq already evicted
