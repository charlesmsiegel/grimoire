"""Optional inference is opt-in and bounded; per-NPC generation is not a flag."""

import pytest

from grimoire.core.inference import (
    BASELINE_STAGES, OPTIONAL_STAGES, InferencePolicy, InferenceStage, coerce_stage,
)


def test_default_policy_disables_every_optional_stage():
    policy = InferencePolicy()
    assert BASELINE_STAGES == {InferenceStage.NPC_GENERATION}
    assert OPTIONAL_STAGES == set(InferenceStage) - BASELINE_STAGES
    for stage in OPTIONAL_STAGES:
        assert not policy.enabled(stage)
        assert policy.cap_for(stage) is None
    assert policy.max_attempts_per_call == 1


def test_policy_accepts_string_stage_names_and_is_order_independent():
    from_config = InferencePolicy({"critique": 1, "planning": 2})
    from_code = InferencePolicy({InferenceStage.PLANNING: 2, InferenceStage.CRITIQUE: 1})
    assert from_config == from_code
    assert hash(from_config) == hash(from_code)
    assert from_config.cap_for("planning") == 2
    assert from_config.enabled(InferenceStage.CRITIQUE)
    assert coerce_stage("recall") is InferenceStage.RECALL


@pytest.mark.parametrize("caps", [
    {"npc_generation": 1},
    {InferenceStage.NPC_GENERATION: 1},
    {"telepathy": 1},
    {1: 1},
    {"planning": 0},
    {"planning": -1},
    {"planning": True},
    {"planning": 1.5},
    [("planning", 1)],
    (("planning", 1), ("planning", 2)),
])
def test_policy_rejects_baseline_unknown_and_bad_caps(caps):
    with pytest.raises(ValueError):
        InferencePolicy(caps)


@pytest.mark.parametrize("attempts", [0, -1, True, 1.0])
def test_policy_rejects_bad_attempt_limits(attempts):
    with pytest.raises(ValueError):
        InferencePolicy(max_attempts_per_call=attempts)


def test_caps_view_is_read_only():
    policy = InferencePolicy({"planning": 1})
    assert dict(policy.caps) == {InferenceStage.PLANNING: 1}
    with pytest.raises(TypeError):
        policy.caps[InferenceStage.CRITIQUE] = 1  # type: ignore[index]
