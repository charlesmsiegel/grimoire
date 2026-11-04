"""Each built-in invariant must both pass and fail on hand-built outcomes."""

from grimoire.testing import (
    CrashOutcome, FileOp, Target, atomic_outcome, completes_after, idempotent_recovery,
    no_residue, preserves_interleave, preserves_meddle, untouched,
)

BEFORE = {"a.json": b"1", "b.json": b"1"}
DONE = {"a.json": b"2", "b.json": b"2"}
MIXED = {"a.json": b"2", "b.json": b"1"}
TRACE = (FileOp(0, "write_bytes", "j.new", digest="x", size=1), FileOp(1, "replace", "j.new", ".journal"),
         FileOp(2, "replace", "a.new", "a.json"))


def outcome(recovered, *, twice=None, index=None, timing=None, meddled=frozenset(), after_meddle=None):
    return CrashOutcome(
        point="test", before=BEFORE, completed=DONE, recovered=recovered,
        recovered_twice=recovered if twice is None else twice, after_meddle=after_meddle or {},
        meddled_paths=meddled, action_index=index, action_timing=timing, baseline_trace=TRACE,
    )


def test_atomic_outcome():
    check = atomic_outcome().check
    assert check(outcome(BEFORE)) is None and check(outcome(DONE)) is None
    assert "neither" in check(outcome(MIXED))
    # an externally meddled path is excluded from the comparison
    assert check(outcome({**DONE, "b.json": b"edited"}, meddled=frozenset({"b.json"}))) is None


def test_idempotent_recovery():
    check = idempotent_recovery().check
    assert check(outcome(DONE)) is None
    assert "second recovery" in check(outcome(DONE, twice=BEFORE))


def test_no_residue():
    check = no_residue("*.new").check
    assert check(outcome(DONE)) is None
    assert "a.json.~tmp1.new" in check(outcome({**DONE, "a.json.~tmp1.new": b""}))


def test_completes_after():
    check = completes_after(Target("replace", target=".journal")).check
    assert check(outcome(BEFORE, index=1, timing="before")) is None
    assert check(outcome(BEFORE, index=0, timing="after")) is None
    assert check(outcome(DONE, index=1, timing="after")) is None
    assert "not rolled forward" in check(outcome(BEFORE, index=1, timing="after"))
    assert "not rolled forward" in check(outcome(MIXED, index=2, timing="before"))
    assert "not found" in completes_after(Target("remove")).check(outcome(DONE, index=2, timing="after"))
    # no crash: a committed, returned action must survive recovery
    returned = CrashOutcome(point="test", before=BEFORE, completed=DONE, recovered=BEFORE,
                            recovered_twice=BEFORE, after_action=DONE, baseline_trace=TRACE, action_trace=TRACE)
    assert "undid" in check(returned)
    assert check(CrashOutcome(point="test", before=BEFORE, completed=DONE, recovered=DONE, recovered_twice=DONE,
                              after_action=DONE, baseline_trace=TRACE, action_trace=TRACE)) is None


def test_untouched():
    check = untouched("a.*").check
    assert check(outcome(BEFORE)) is None
    assert "a.json" in check(outcome(DONE))


def test_preserves_meddle():
    check = preserves_meddle().check
    edit = {**BEFORE, "b.json": b"edited"}
    meddled = frozenset({"b.json"})
    assert check(outcome(edit, meddled=meddled, after_meddle=edit)) is None
    assert "overwrote" in check(outcome(DONE, meddled=meddled, after_meddle=edit))


def test_preserves_interleave():
    check = preserves_interleave().check

    def raced(recovered, interleaved, paths):
        return CrashOutcome(point="test", before=BEFORE, completed=DONE, recovered=recovered,
                            recovered_twice=recovered, interleaved=interleaved, interleaved_paths=frozenset(paths))

    assert check(raced(DONE, {}, ())) is None  # no interleave: nothing to preserve
    assert check(raced({**DONE, "a.json": b"human"}, {"a.json": b"human"}, {"a.json"})) is None
    assert "lost an interleaved edit" in check(raced(DONE, {"a.json": b"human"}, {"a.json"}))
    # a removal must stay a removal; an interleaved directory must stay a directory
    assert check(raced({"b.json": b"2"}, {}, {"a.json"})) is None
    assert "lost" in check(raced(DONE, {}, {"a.json"}))
    assert check(raced({**DONE, "d": None}, {"d": None}, {"d"})) is None
    assert "lost" in check(raced({**DONE, "d": b"file"}, {"d": None}, {"d"}))


def test_preserves_interleave_defers_to_a_later_meddle_and_normalized_directories():
    check = preserves_interleave().check
    # a meddle edited the same path later: the meddled edit is the one to keep (preserves_meddle)
    later = CrashOutcome(point="test", before=BEFORE, completed=DONE, recovered={**DONE, "a.json": b"meddle"},
                         recovered_twice=DONE, interleaved={"a.json": b"interleave"},
                         interleaved_paths=frozenset({"a.json"}), meddled_paths=frozenset({"a.json"}))
    assert check(later) is None
    # an interleaved directory a normalizer dropped while the writer filled it later
    filled = CrashOutcome(point="test", before=BEFORE, completed=DONE,
                          recovered={**DONE, "box": None, "box/f.json": b"x"}, recovered_twice=DONE,
                          interleaved={}, interleaved_paths=frozenset({"box"}))
    assert check(filled) is None
    # but a removed file still has to stay removed
    removed = CrashOutcome(point="test", before=BEFORE, completed=DONE, recovered={**DONE, "gone.json": b"x"},
                           recovered_twice=DONE, interleaved={}, interleaved_paths=frozenset({"gone.json"}))
    assert "lost" in check(removed)
