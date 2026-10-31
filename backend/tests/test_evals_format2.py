"""grimoire.story-quality/2: runs rebuilt as inference plans and usage ledgers."""

import pytest

from evals_helpers import (
    attempt_v2, guild_run_v2, live_source, minimal_run, minimal_run_v2, minimal_scenario, mutate,
)
from grimoire.core.inference import InferenceStage, Measurement
from grimoire.evals.format import BenchmarkFormatError, parse_run, parse_scenario
from grimoire.evals.format2 import FORMAT_V2, OUTCOMES_V2, STAGES_V2, RunV2, parse_run_v2

SCENARIO = parse_scenario(minimal_scenario())


def parse(run: dict, scenario=SCENARIO) -> RunV2:
    return parse_run_v2(run, scenario)


def rejects(run: dict, where: str, scenario=SCENARIO, *, says: str = "") -> str:
    with pytest.raises(BenchmarkFormatError) as error:
        parse(run, scenario)
    message = str(error.value)
    assert message.startswith(where), message
    assert says in message, message
    return message


def model_line(run: dict) -> dict:
    return run["turns"][0]["contributions"][1]


def test_minimal_v2_parses():
    run = parse(minimal_run_v2())
    assert run.run_id == "r2" and len(run.calls) == 1
    assert run.calls[0].stage is InferenceStage.NPC_GENERATION
    assert run.usage.total.sums["input_tokens"] == 100
    assert run.summaries[0].complete
    assert FORMAT_V2 == "grimoire.story-quality/2"


def test_stage_vocabulary_is_frozen_and_matches_core():
    assert STAGES_V2 == ("npc_generation", "planning", "decision", "epistemic_inference",
                         "recall", "graph_lookup", "critique")
    # A new InferenceStage (say, narration) must not silently change /2: that is /3.
    assert set(STAGES_V2) == {stage.value for stage in InferenceStage}


def test_unknown_stage_and_narration_purpose_rejected():
    run = mutate(minimal_run_v2(), lambda r: r["calls"][0].update(stage="narration"))
    rejects(run, "run.calls[0].stage")


def test_scene_level_model_contribution_rejected():
    run = mutate(minimal_run_v2(), lambda r: model_line(r).update(speaker_id=None))
    rejects(run, "run.turns[0].contributions[1].speaker_id")


def test_contribution_speaker_must_match_its_call_actor():
    def change(r):
        r["turns"][0]["contributions"].append(
            {"speaker_id": "pc", "author": "model", "text": "Pat says hi.", "call_id": "k1"})
    rejects(mutate(minimal_run_v2(), change), "run.turns[0].contributions[2].speaker_id")


def test_contribution_call_needs_completed_attempt():
    run = mutate(minimal_run_v2(), lambda r: r["calls"][0]["attempts"][0].update(outcome="failed"))
    rejects(run, "run.turns[0].contributions[1].call_id", says="completed")


def test_npc_generation_for_player_actor_rejected():
    def change(r):
        r["calls"][0]["actor_id"] = "pc"
        model_line(r)["speaker_id"] = "pc"
    rejects(mutate(minimal_run_v2(), change), "run.calls[0].actor_id")


def test_duplicate_speaker_in_turn_rejected():
    def change(r):
        second = dict(r["calls"][0], call_id="k2", attempts=[attempt_v2()])
        r["calls"].append(second)
    rejects(mutate(minimal_run_v2(), change), "run.calls[1].actor_id", says="one generation")


def decision_call(**overrides) -> dict:
    entry = {"call_id": "d1", "turn": 1, "stage": "decision", "actor_id": None,
             "purpose": "choose who answers", "latency_ms": 20, "attempts": [attempt_v2(30, 2)]}
    entry.update(overrides)
    return entry


def test_optional_stage_without_cap_rejected():
    run = mutate(minimal_run_v2(), lambda r: r["calls"].append(decision_call()))
    rejects(run, "run.calls[1].stage", says="disabled")


def test_optional_stage_over_cap_rejected():
    def change(r):
        r["policy"]["per_turn_call_caps"] = {"decision": 1}
        r["calls"] += [decision_call(), decision_call(call_id="d2")]
    rejects(mutate(minimal_run_v2(), change), "run.calls[2].stage", says="cap")


def test_optional_call_produces_no_contribution():
    def change(r):
        r["policy"]["per_turn_call_caps"] = {"decision": 1}
        r["calls"].append(decision_call(phase="select"))
    run = parse(mutate(minimal_run_v2(), change))
    assert [c.stage for c in run.calls] == [InferenceStage.NPC_GENERATION, InferenceStage.DECISION]
    assert run.summaries[0].phases == ("main", "select")
    assert run.usage.optional.sums["input_tokens"] == 30

    def speaks(r):
        change(r)
        model_line(r)["call_id"] = "d1"
    rejects(mutate(minimal_run_v2(), speaks), "run.turns[0].contributions[1].call_id",
            says="npc_generation")


def test_attempt_order_and_limit_enforced():
    def change(r):
        r["calls"][0]["attempts"] = [attempt_v2(outcome="failed", input_tokens=None,
                                                output_tokens=None), attempt_v2(attempt=2)]
    rejects(mutate(minimal_run_v2(), change), "run.calls[0].attempts[1]", says="limit")
    allowed = mutate(minimal_run_v2(), change)
    allowed["policy"]["max_attempts_per_call"] = 2
    assert parse(allowed).usage.total.attempts == 2

    def out_of_order(r):
        r["policy"]["max_attempts_per_call"] = 3
        r["calls"][0]["attempts"] = [attempt_v2(attempt=2)]
    rejects(mutate(minimal_run_v2(), out_of_order), "run.calls[0].attempts[0]")


def test_missing_is_not_zero():
    unreported = parse(mutate(minimal_run_v2(),
                              lambda r: r["calls"][0]["attempts"][0]["usage"].update(
                                  input_tokens=None)))
    zero = parse(mutate(minimal_run_v2(),
                        lambda r: r["calls"][0]["attempts"][0]["usage"].update(input_tokens=0)))
    assert unreported.usage.total.unreported["input_tokens"] == 1
    assert zero.usage.total.unreported["input_tokens"] == 0
    assert not unreported.usage.complete and zero.usage.complete


def test_not_sent_requires_exact_zero_input_output():
    def nothing_sent(tokens):
        def change(r):
            r["policy"]["max_attempts_per_call"] = 2
            r["calls"][0]["attempts"] = [attempt_v2(tokens, tokens, cache_read=None,
                                                    outcome="not_sent"), attempt_v2(attempt=2)]
        return mutate(minimal_run_v2(), change)
    assert parse(nothing_sent(0)).usage.total.attempts == 2
    rejects(nothing_sent(None), "run.calls[0].attempts[0]")
    rejects(nothing_sent(3), "run.calls[0].attempts[0]")


@pytest.mark.parametrize("source", [{"kind": "synthetic"}, live_source(mode="dry_run")],
                         ids=["synthetic", "dry_run"])
def test_synthetic_and_dry_run_sources_reject_provider_reported(source):
    def change(r):
        r["source"] = source
        r["calls"][0]["attempts"][0]["measurement"] = "provider_reported"
    rejects(mutate(minimal_run_v2(), change), "run.calls[0].attempts[0].measurement")


def test_live_source_accepts_provider_reported_and_estimated():
    for measurement in ("provider_reported", "estimated"):
        def change(r):
            r["source"] = live_source()
            r["calls"][0]["attempts"][0]["measurement"] = measurement
        run = parse(mutate(minimal_run_v2(), change))
        assert run.usage.measurements == {Measurement(measurement)}

    def synthetic_live(r):
        r["source"] = live_source()
    rejects(mutate(minimal_run_v2(), synthetic_live), "run.calls[0].attempts[0].measurement")


def test_source_is_strict():
    rejects(mutate(minimal_run_v2(), lambda r: r.update(source={"kind": "measured"})),
            "run.source.kind")
    rejects(mutate(minimal_run_v2(), lambda r: r.update(source=live_source(v1_commit="abc"))),
            "run.source.v1_commit")
    rejects(mutate(minimal_run_v2(), lambda r: r.update(source=live_source(mode="replay"))),
            "run.source.mode")
    rejects(mutate(minimal_run_v2(), lambda r: r.update(source={"kind": "synthetic",
                                                                  "mode": "live"})),
            "run.source", says="unknown key")
    reserved = parse(mutate(minimal_run_v2(), lambda r: r.update(
        source={"kind": "v2-runtime", "harness": "grimoire.runtime/1"})))
    assert reserved.source.kind == "v2-runtime"


def test_cost_is_per_attempt_and_strict():
    def priced(cost):
        return mutate(minimal_run_v2(), lambda r: r["calls"][0]["attempts"][0].update(cost=cost))
    run = parse(priced({"usd": 0.0012, "basis": "billed"}))
    assert run.calls[0].costs[0].usd == 0.0012 and run.calls[0].costs[0].basis == "billed"
    assert parse(minimal_run_v2()).calls[0].costs == (None,)
    rejects(priced({"usd": -1, "basis": "billed"}), "run.calls[0].attempts[0].cost.usd")
    rejects(priced({"usd": 1, "basis": "list"}), "run.calls[0].attempts[0].cost.basis")


def test_latency_is_per_call_and_may_be_unreported():
    run = parse(mutate(minimal_run_v2(), lambda r: r["calls"][0].update(latency_ms=None)))
    assert run.calls[0].latency_ms is None
    rejects(mutate(minimal_run_v2(), lambda r: r["calls"][0].update(latency_ms=-1)),
            "run.calls[0].latency_ms")


def test_unlabelled_outcome_accepted_in_v2_only():
    assert "unlabelled" in OUTCOMES_V2
    run = parse(mutate(minimal_run_v2(), lambda r: r["choice_outcomes"].update(c1="unlabelled")))
    assert run.choice_outcomes["c1"] == "unlabelled"
    with pytest.raises(BenchmarkFormatError):
        parse_run(mutate(minimal_run(), lambda r: r["choice_outcomes"].update(c1="unlabelled")),
                  SCENARIO)


def test_unattempted_call_without_contribution_is_unmetered():
    def change(r):
        r["calls"][0]["attempts"] = []
        r["turns"][0]["contributions"].pop()
    run = parse(mutate(minimal_run_v2(), change))
    assert len(run.usage.unmetered_call_ids) == 1 and not run.usage.complete
    named = mutate(minimal_run_v2(), lambda r: r["calls"][0].update(attempts=[]))
    rejects(named, "run.turns[0].contributions[1].call_id", says="completed")


def test_four_present_two_speaking_yields_two_calls():
    raw_scenario, raw_run = guild_run_v2()
    scenario = parse_scenario(raw_scenario)
    run = parse(raw_run, scenario)
    assert len(run.summaries) == 3
    for summary in run.summaries:
        assert summary.by_stage.keys() == {InferenceStage.NPC_GENERATION}
        assert summary.by_stage[InferenceStage.NPC_GENERATION].attempts == 2
        assert set(summary.by_actor) == {"npc-oda", "npc-bellamy"}
    for plan in run.plans:
        assert {"npc-quill", "npc-hask"} <= plan.present_actor_ids
    assert {c.actor_id for c in run.calls} == {"npc-oda", "npc-bellamy"}


@pytest.mark.parametrize("key, where", [
    ("provenance", "run"), ("enabled_flags", "run"), ("feature_flag", "run.calls[0]")])
def test_v2_rejects_v1_keys(key, where):
    def change(r):
        target = r["calls"][0] if key == "feature_flag" else r
        target[key] = "synthetic" if key == "provenance" else ([] if key == "enabled_flags" else None)
    rejects(mutate(minimal_run_v2(), change), where, says="unknown key")


def test_required_conditions_and_formats():
    rejects(mutate(minimal_run_v2(), lambda r: r["conditions"].pop("budget")),
            "run.conditions.budget")
    rejects(mutate(minimal_run_v2(), lambda r: r.update(format="grimoire.story-quality/1")),
            "run.format")


def test_scoring_view_feeds_the_v1_checks():
    def change(r):
        r["policy"]["per_turn_call_caps"] = {"decision": 1}
        r["calls"].append(decision_call(phase="select"))
        r["calls"][0]["attempts"][0]["usage"]["cache_read_tokens"] = 40
    view = parse(mutate(minimal_run_v2(), change)).scoring_view()
    npc, decision = view.calls
    assert (npc.purpose, npc.actor_id, npc.feature_flag) == ("npc_generation", "npc-a", None)
    assert (npc.input_tokens, npc.cached_input_tokens, npc.output_tokens) == (100, 40, 5)
    assert (decision.purpose, decision.feature_flag) == ("decision", "decision")
    assert view.enabled_flags == ("decision",)
    assert view.provenance == "synthetic"
