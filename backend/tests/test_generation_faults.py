"""027: a two-speaker turn under every provider fault, rejection, cancel and supersede point."""

from __future__ import annotations

from dataclasses import replace

import grimoire.runtime.runtime as runtime_module
from grimoire.core.inference import TurnUsageLedger
from grimoire.providers.meter import TurnMeter
from grimoire.runtime import GeneratedLine, GenerationRuntime
from grimoire.testing.generation import GENERATION_INVARIANTS, explore_generation_faults


def test_the_runtime_holds_every_invariant_at_every_point():
    report = explore_generation_faults(seed=11)
    report.assert_ok()
    assert report.runs == len(report.points) > 150
    outcomes = {trace[4][0] for trace in report.traces.values()}
    assert outcomes == {"complete", "halted", "cancelled", "stale"}  # every ending is reached


def test_the_director_reply_meets_every_fault_too():
    """The scene-level director call is screened and halted like a speaker's (Wave 4 audit)."""
    traces = explore_generation_faults(seed=5).traces
    assert traces["clean/director/x1"][4][2][-1][0] == "Narrator"
    for fault, reason in (("leak", "unlearned_secret"), ("copy", "copied_example"), ("silent", "silent_reply")):
        status, calls, lines = traces[f"{fault}/director/x1"][4]
        assert (status, calls[-1], len(lines)) == ("halted", ("rejected", reason), 2)


def test_the_explorer_is_deterministic_and_seeded():
    assert explore_generation_faults(seed=2).traces == explore_generation_faults(seed=2).traces


# -- each invariant can fail ----------------------------------------------------------------------

def broken(invariant: str, **kw) -> set[str]:
    report = explore_generation_faults(**kw)
    found = {v.invariant for v in report.violations}
    assert invariant in GENERATION_INVARIANTS and invariant in found
    return found


class IgnoresTheFence(GenerationRuntime):
    def run_turn(self, meter, briefs, **kw):
        kw["token"] = None
        return super().run_turn(meter, briefs, **kw)


class KeepsCutOffText(GenerationRuntime):
    """Returns a line for the first call that did not end with one."""

    def run_turn(self, meter, briefs, **kw):
        outcome = super().run_turn(meter, briefs, **kw)
        failed = [c for c in outcome.calls if c.status not in ("accepted", "not_issued")]
        if not failed:
            return outcome
        return replace(outcome, lines=outcome.lines + (GeneratedLine(failed[0].call_id, None, "Ash", None, None,
                                                                     "Coin fir"),))


class Doubles(GenerationRuntime):
    def run_turn(self, meter, briefs, **kw):
        outcome = super().run_turn(meter, briefs, **kw)
        return replace(outcome, lines=outcome.lines + outcome.lines[:1])


class RunsTwice(GenerationRuntime):
    """Runs the turn once on a meter nobody sees, then again on the real one."""

    def run_turn(self, meter, briefs, **kw):
        super().run_turn(TurnMeter(TurnUsageLedger(meter.ledger.plan)), briefs, **kw)
        return super().run_turn(meter, briefs, **kw)


class Raises(GenerationRuntime):
    def run_turn(self, meter, briefs, **kw):
        raise RuntimeError("broken")


def test_stale_delivers_nothing_can_fail():
    broken("stale delivers nothing", runtime_factory=IgnoresTheFence)


def test_no_leaked_line_can_fail(monkeypatch):
    monkeypatch.setattr(runtime_module, "screen_reply", lambda *a, **kw: None)
    broken("no leaked line")


def test_no_unaccepted_line_can_fail():
    broken("no unaccepted line", runtime_factory=KeepsCutOffText)


def test_no_rejected_line_can_fail(monkeypatch):
    monkeypatch.setattr(runtime_module, "screen_reply", lambda *a, **kw: None)
    broken("no rejected line")


def test_one_line_per_call_can_fail():
    broken("one line per call", runtime_factory=Doubles)


def test_every_send_booked_and_bounded_calls_can_fail():
    found = broken("every send booked", runtime_factory=RunsTwice)
    assert "bounded calls" in found


def test_halt_after_failure_can_fail():
    broken("halt after failure", runtime_factory=Raises)


def test_bounded_exploration_can_fail():
    broken("bounded exploration", max_runs=3)
