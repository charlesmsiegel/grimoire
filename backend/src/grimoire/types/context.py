"""Context Builder types: assembled prompts, tiers, sources."""

from __future__ import annotations

from dataclasses import dataclass, field

from .common import Json, Scope
from .llm import Message, ModelParams
from .state import ContextTier


@dataclass
class ContextSource:
    """One source that contributed to the assembled prompt."""

    kind: str  # 'character', 'location', 'lore', 'scene', 'fact', 'commitment', 'style_guide', ...
    scope: Scope
    owner_id: str | None  # library asset id, campaign id, or None for system
    tier: ContextTier
    library_version: int | None = None
    override_applied: bool = False
    tokens: int = 0
    summary: str = ""


@dataclass
class BudgetEstimate:
    total_budget: int
    reserve_for_response: int
    per_tier: dict[ContextTier, int] = field(default_factory=dict)
    sources_preview: list[ContextSource] = field(default_factory=list)


@dataclass
class AssembledPrompt:
    """Output of Context Builder; ready to hand to the LLM Gateway."""

    messages: list[Message]
    params: ModelParams
    budget_used: dict[ContextTier, int] = field(default_factory=dict)
    sources: list[ContextSource] = field(default_factory=list)
    summary: str = ""
    composition_snapshot: Json = field(default_factory=dict)
    messages_hash: str = ""
