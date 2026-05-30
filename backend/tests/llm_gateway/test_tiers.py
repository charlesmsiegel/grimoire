"""Tier mapping for the LLM gateway."""

from __future__ import annotations

from grimoire.llm_gateway.tiers import Tier, tier_for_task


def test_heavy_tasks() -> None:
    assert tier_for_task("main") == Tier.HEAVY
    assert tier_for_task("scene_summary") == Tier.HEAVY
    assert tier_for_task("scenes.final_summary") == Tier.HEAVY
    assert tier_for_task("scene_analysis") == Tier.HEAVY
    assert tier_for_task("auxiliary.rewrite_post") == Tier.HEAVY
    assert tier_for_task("auxiliary.continue_as") == Tier.HEAVY
    assert tier_for_task("auxiliary.brainstorm") == Tier.HEAVY
    assert tier_for_task("scene_first_post") == Tier.HEAVY


def test_light_tasks() -> None:
    assert tier_for_task("scenes.running_summary") == Tier.LIGHT
    assert tier_for_task("drift_check") == Tier.LIGHT
    assert tier_for_task("scene_break_classifier") == Tier.LIGHT
    assert tier_for_task("auxiliary.translate") == Tier.LIGHT
    assert tier_for_task("auxiliary.what_would_x_say") == Tier.LIGHT
    assert tier_for_task("auxiliary.edit_prose") == Tier.LIGHT
    assert tier_for_task("extractor") == Tier.LIGHT
    assert tier_for_task("scene_suggest") == Tier.LIGHT
    assert tier_for_task("scene_preview") == Tier.LIGHT


def test_world_tasks_use_call_site_strings() -> None:
    # Regression: the tier map keys must match the literal task strings the
    # call sites pass to the gateway. Previously the map used dotted keys
    # ("world.atmosphere", "world.location_generator") while the call sites
    # passed underscored strings, so the tier never applied.
    assert tier_for_task("world_atmosphere") == Tier.LIGHT
    assert tier_for_task("world_location_generate") == Tier.LIGHT
    # The old dotted keys must NOT be present (they were dead).
    assert tier_for_task("world.atmosphere") is None
    assert tier_for_task("world.location_generator") is None


def test_embedding_task() -> None:
    assert tier_for_task("library.embed") == Tier.EMBEDDING


def test_unknown_task_returns_none() -> None:
    assert tier_for_task("not.a.real.task") is None
    assert tier_for_task("") is None
