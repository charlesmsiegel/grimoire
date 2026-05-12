"""Context Builder types: assembled prompts, tiers, sources."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import Json, Scope
from .llm import Message, ModelParams
from .state import ContextTier


class ContextSource(BaseModel):
    """One source that contributed to the assembled prompt."""

    kind: str  # 'character', 'location', 'lore', 'scene', 'fact', 'commitment', ...
    scope: Scope
    owner_id: str | None  # library asset id, campaign id, or None for system
    tier: ContextTier
    library_version: int | None = None
    override_applied: bool = False
    tokens: int = 0
    summary: str = ""


class BudgetEstimate(BaseModel):
    total_budget: int
    reserve_for_response: int
    per_tier: dict[ContextTier, int] = Field(default_factory=dict)
    sources_preview: list[ContextSource] = Field(default_factory=list)


class AssembledPrompt(BaseModel):
    """Output of Context Builder; ready to hand to the LLM Gateway."""

    messages: list[Message]
    params: ModelParams
    budget_used: dict[ContextTier, int] = Field(default_factory=dict)
    sources: list[ContextSource] = Field(default_factory=list)
    summary: str = ""
    composition_snapshot: Json = Field(default_factory=dict)
    messages_hash: str = ""
