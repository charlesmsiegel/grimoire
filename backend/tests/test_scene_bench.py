"""048's benchmarks run and report what they claim, at sizes small enough for every test run (``scene_bench``)."""

from __future__ import annotations

from scene_bench import PHASES, TURN_CALLS, card_turns, import_traces, v1_turns

from grimoire.tracing import call_costs


def test_the_card_benchmark_plays_a_cold_and_a_warm_turn_at_each_history(tmp_path):
    measured = card_turns(tmp_path / "bench", (3, 6))
    assert [(each.history, each.phase, each.operation) for each in measured] == [
        (history, phase, call) for history in (3, 6) for phase in PHASES for call in TURN_CALLS]
    assert all(each.wall_s >= 0 and each.trace is not None and each.trace.status == "ok" for each in measured)
    by = {(each.history, each.phase, each.operation): each.trace for each in measured}
    assert by[(3, "cold", "open")].counts("read.turns")["decoded"] == 3  # a cold table decodes everything
    warm = by[(6, "warm", "open")].counts("read.turns")
    assert warm["decoded"] == 1 and warm["reused"] == warm["files"] - 1  # only the turn the cold keep rewrote
    generated = by[(3, "cold", "generate")]
    assert len(generated.calls) == 1 and call_costs(generated).total.latency == "synthetic"


def test_each_history_size_measures_exactly_that_history(tmp_path):
    measured = card_turns(tmp_path / "card", (1, 2)) + v1_turns(tmp_path / "v1", (1, 2))
    for each in measured:
        if each.phase == "cold" and each.operation == "open":
            assert each.trace.counts("read.turns")["turns"] == each.history


def test_the_v1_benchmark_has_two_speakers(tmp_path):
    measured = v1_turns(tmp_path / "bench", (2,))
    (generated,) = [each.trace for each in measured if each.operation == "generate" and each.phase == "warm"]
    assert len({sample.actor_id for sample in generated.calls}) == 2


def test_an_untraced_run_is_still_timed_from_outside(tmp_path):
    measured = card_turns(tmp_path / "bench", (2,), traced=False)
    assert len(measured) == len(PHASES) * len(TURN_CALLS)
    assert all(each.trace is None and each.wall_s > 0 for each in measured)


def test_the_import_benchmark_times_each_phase(tmp_path):
    v1, card = import_traces(tmp_path / "imports")
    assert v1.operation == "import.v1" and [v1.spans[i].operation for i in v1.children(0)] == ["scan", "plan",
                                                                                                 "apply"]
    assert card.operation == "import.card" and card.status == "ok"
    assert sum(v1.spans[i].seconds for i in v1.children(0)) <= v1.seconds
