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


__all__ = ["ContextBuilderConfig", "RetrievalConfig", "TierBudget"]
