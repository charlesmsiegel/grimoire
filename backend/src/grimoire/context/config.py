"""Configuration for the Context Builder.

Mirrors the per-tier budget example in spec 02. Defaults target Claude-class
windows (180k effective budget, 20k reserved for the response). Smaller
local models can override these at the campaign or app level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grimoire.types.state import ContextTier


@dataclass
class TierBudget:
    max_tokens: int
    priority: str = "medium"  # 'required' | 'high' | 'medium' | 'low'


@dataclass
class RetrievalConfig:
    vector_top_k: int = 8
    keyword_top_k: int = 5
    similarity_threshold: float = 0.65
    embedding_task: str = "extractor.embed"  # reusing existing embed task
    include_library: bool = True
    keyword_kinds: tuple[str, ...] = ("fact",)
    # When set, the builder passes `priority_hints={world_id: priority}` to
    # the store's `vector_search` / `keyword_search`. The store may use the
    # hint to re-rank library hits. Builder behaviour is unchanged when the
    # store ignores the kwarg.
    enable_priority_weighting: bool = True


@dataclass
class ContextBuilderConfig:
    total_budget: int = 180_000
    reserve_for_response: int = 20_000
    tiers: dict[ContextTier, TierBudget] = field(
        default_factory=lambda: {
            ContextTier.LOCK_IN: TierBudget(max_tokens=8_000, priority="required"),
            ContextTier.SPOTLIGHT: TierBudget(max_tokens=40_000, priority="high"),
            ContextTier.BACKGROUND: TierBudget(max_tokens=30_000, priority="medium"),
            ContextTier.ARCHIVE: TierBudget(max_tokens=20_000, priority="low"),
        }
    )
    recent_posts_budget: int = 30_000
    recent_posts_n: int = 8
    lock_in_verbatim_posts: int = 2
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    # Approximation: how many characters map to one token when no tokenizer
    # is available. Spec 05 §Estimation uses ~4 chars / token.
    chars_per_token: int = 4
    # Pull this many promoted-to-background offscreen characters per turn.
    background_character_limit: int = 6
    # Generate temperature for downstream LLM calls.
    default_temperature: float = 1.0
    default_max_tokens: int = 4_096
    # § Recent facts (spec context-builder-remaining §5). Limit applied to
    # `continuity.facts_about(limit=...)` and a per-tier char cap for the
    # compact fact-line renderer.
    recent_facts_limit: int = 50
    recent_facts_char_cap: int = 4_000
    # § Per-speaker recent dialogue (§10): last N posts authored by each
    # spotlighted speaker, rendered as a spotlight item.
    recent_dialogue_per_speaker: int = 3
    # § Cast voice anchor (§9): when True, append the character's voice-only
    # snippet under a "# Voice anchor" heading distinct from the full card.
    enable_voice_anchor: bool = True
    # § Faction state in background tier (§3): cap the number of faction
    # rows we pull per turn to keep background tight.
    faction_state_limit: int = 4
    # § Promotion cooldown (§1): a character demoted/promoted in the last
    # N turns will not be re-demoted automatically (avoid tier churn).
    promotion_cooldown_turns: int = 3
    # § Explicit scene refs (§7): cap how many scenes can be force-injected
    # from a single player input.
    scene_ref_limit: int = 5


__all__ = ["ContextBuilderConfig", "RetrievalConfig", "TierBudget"]
