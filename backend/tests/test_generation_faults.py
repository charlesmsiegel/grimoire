"""027: a two-speaker turn under every provider fault, rejection, cancel and supersede point."""

from __future__ import annotations

import grimoire.runtime.runtime as runtime_module
from grimoire.runtime import GenerationRuntime
from grimoire.testing.generation import GENERATION_INVARIANTS, explore_generation_faults


def test_the_runtime_holds_every_invariant_at_every_point():
    report = explore_generation_faults(seed=11)
    report.assert_ok()
    assert report.runs == len(report.points) > 150
    outcomes = {trace[4][0] for trace in report.traces.values()}
    assert outcomes == {"complete", "halted", "cancelled", "stale"}  # every ending is reached


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


class Raises(GenerationRuntime):
    def run_turn(self, meter, briefs, **kw):
        raise RuntimeError("broken")


def test_stale_delivers_nothing_can_fail():
    broken("stale delivers nothing", runtime_factory=IgnoresTheFence)


def test_no_leaked_line_can_fail(monkeypatch):
    monkeypatch.setattr(runtime_module, "screen_reply", lambda *a, **kw: None)
    broken("no leaked line")


def test_halt_after_failure_can_fail():
    broken("halt after failure", runtime_factory=Raises)


def test_bounded_exploration_can_fail():
    broken("bounded exploration", max_runs=3)
