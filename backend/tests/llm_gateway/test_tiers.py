"""Tier mapping for the LLM gateway."""

from __future__ import annotations

from grimoire.llm_gateway.tiers import Tier, tier_for_task


def test_heavy_tasks() -> None:
    assert tier_for_task("main") == Tier.HEAVY
    assert tier_for_task("scenes.running_summary") == Tier.HEAVY
    assert tier_for_task("scenes.final_summary") == Tier.HEAVY
    assert tier_for_task("auxiliary.rewrite_post") == Tier.HEAVY
    assert tier_for_task("auxiliary.continue_as") == Tier.HEAVY
    assert tier_for_task("auxiliary.brainstorm") == Tier.HEAVY
    assert tier_for_task("world.atmosphere") == Tier.HEAVY


def test_light_tasks() -> None:
    assert tier_for_task("drift_check") == Tier.LIGHT
    assert tier_for_task("scene_break_classifier") == Tier.LIGHT
    assert tier_for_task("auxiliary.translate") == Tier.LIGHT
    assert tier_for_task("auxiliary.what_would_x_say") == Tier.LIGHT
    assert tier_for_task("auxiliary.edit_prose") == Tier.LIGHT
    assert tier_for_task("world.location_generator") == Tier.LIGHT
    assert tier_for_task("extractor") == Tier.LIGHT


def test_embedding_task() -> None:
    assert tier_for_task("library.embed") == Tier.EMBEDDING


def test_unknown_task_returns_none() -> None:
    assert tier_for_task("not.a.real.task") is None
    assert tier_for_task("") is None
