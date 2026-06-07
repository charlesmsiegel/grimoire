"""Task → tier mapping for the LLM gateway.

Three logical tiers — Heavy (generation), Light (classification +
mechanical transforms), Embedding — provide a coarse routing knob so
users can point cheap and expensive models at the right work without
editing per-task routes by hand.

The mapping is built-in and stable; user overrides live in
``model_routing`` (per-task) and ``model_tiers`` (per-campaign) on
``campaign.yaml``. See ``docs/superpowers/specs/2026-05-23-llm-tiering-design.md``
§1 for the rationale behind each assignment.
"""

from __future__ import annotations

from enum import StrEnum


class Tier(StrEnum):
    HEAVY = "heavy"
    LIGHT = "light"
    EMBEDDING = "embedding"


_TASK_TIER: dict[str, Tier] = {
    # Heavy — generative work
    "main": Tier.HEAVY,
    "scene_summary": Tier.HEAVY,
    "scenes.final_summary": Tier.HEAVY,
    "scene_analysis": Tier.HEAVY,
    "auxiliary.rewrite_post": Tier.HEAVY,
    "auxiliary.continue_as": Tier.HEAVY,
    "auxiliary.brainstorm": Tier.HEAVY,
    "scene_first_post": Tier.HEAVY,
    # Light — classification, mechanical transforms, short/rolling summaries
    "scenes.running_summary": Tier.LIGHT,
    "drift_check": Tier.LIGHT,
    "scene_break_classifier": Tier.LIGHT,
    "auxiliary.translate": Tier.LIGHT,
    "auxiliary.what_would_x_say": Tier.LIGHT,
    "auxiliary.edit_prose": Tier.LIGHT,
    "world_atmosphere": Tier.LIGHT,
    "world_location_generate": Tier.LIGHT,
    "extractor": Tier.LIGHT,
    "scene_suggest": Tier.LIGHT,
    "scene_preview": Tier.LIGHT,
    "imagegen.prompt": Tier.LIGHT,
    # Embedding
    "library.embed": Tier.EMBEDDING,
}


def tier_for_task(task: str) -> Tier | None:
    """Return the tier for ``task`` or ``None`` for an unknown task.

    Unknown tasks fall through to the resolver's default route — they
    are not silently routed to Light or Heavy.
    """
    return _TASK_TIER.get(task)


__all__ = ["Tier", "tier_for_task"]
