"""Provider-free replay of real 0.2.1 dry-run captures (ordinary CI).

The fixtures are ``evidence.json`` files the capture harness wrote after driving the unmodified
0.2.1 app (pinned commit) through ``guild_betrayal`` with 0.2.1's own provider adapters over its
test transport. Their numbers are synthetic; what is real is 0.2.1's call shape and ledger.
"""

from pathlib import Path

import pytest

from grimoire.core.inference import InferenceStage, Measurement
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.loader import benchmark_file, load_evidence, load_json, load_scenario
from grimoire.evals.v021 import PINNED_V021_COMMIT, convert, verify_evidence

FIXTURES = Path(__file__).parent / "fixtures" / "v021"
KINDS = ("openrouter", "anthropic")


def replay(kind):
    scenario = load_scenario(benchmark_file("guild_betrayal", "scenario.json"))
    bundle, digest = load_evidence(FIXTURES / f"guild_betrayal_dry_run_{kind}.evidence.json")
    raw = convert(bundle, scenario, digest=digest, run_id=f"replay-{kind}", label="replay")
    return scenario, bundle, digest, parse_run_v2(raw, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_dry_run_replay_is_one_call_per_speaking_npc(kind):
    scenario, bundle, _, run = replay(kind)
    assert bundle["mode"] == "dry_run" and bundle["provider_kind"] == kind
    assert bundle["v1_commit"] == PINNED_V021_COMMIT
    assert len(run.calls) == 6
    assert {call.stage for call in run.calls} == {InferenceStage.NPC_GENERATION}
    assert {call.actor_id for call in run.calls} == {"npc-oda", "npc-bellamy"}
    for plan in run.plans:  # four NPCs present, two speak, nobody else costs a call
        assert {"npc-quill", "npc-hask"} <= plan.present_actor_ids
        assert len(plan.calls) == 2
    assert len([row for row in bundle["usage_rows"] if row.get("kind") == "llm"]) == 6
    assert run.usage.measurements == {Measurement.SYNTHETIC}


@pytest.mark.parametrize("kind", KINDS)
def test_dry_run_replay_passes_the_gate_as_synthetic(kind):
    scenario, bundle, digest, run = replay(kind)
    evidence = verify_evidence(run, bundle, digest, scenario)
    assert evidence.ok, evidence.reason
    report = compare_v2(scenario, run, run, baseline_evidence=evidence,
                        candidate_evidence=evidence)
    assert report.passed, report.findings
    assert report.baseline.status == "synthetic"


@pytest.mark.parametrize("kind", KINDS)
def test_dry_run_replay_usage_is_the_scripted_usage(kind):
    _, _, _, run = replay(kind)
    setup = load_json(benchmark_file("guild_betrayal", "v021_setup.json"))
    replies = setup["dry_run"]["replies"]
    for call in run.calls:
        (attempt,) = call.attempts
        scripted = replies[str(call.turn)][call.actor_id]
        assert attempt.usage.input_tokens == scripted["prompt_tokens"]
        assert attempt.usage.cache_read_tokens == scripted["cache_read_tokens"]
        assert attempt.usage.output_tokens == scripted["completion_tokens"]
