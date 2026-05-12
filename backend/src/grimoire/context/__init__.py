"""Context Builder module (spec 02).

Assembles every prompt the LLM ever sees. Pulls resolved entities from the
domain modules (Characters / Setting / Continuity / Scene Manager), tiers
them, applies a token budget, and emits a canonical ``AssembledPrompt`` for
the LLM Gateway. Style guides, content boundaries and mechanics results are
embedded verbatim; archive retrieval (vector + keyword) is scoped to the
campaign-local store + its referenced library assets.
"""

from __future__ import annotations

from grimoire.context.builder import ContextBuilderService
from grimoire.context.config import (
    ContextBuilderConfig,
    RetrievalConfig,
    TierBudget,
)
from grimoire.context.errors import ContextBuilderError, LockInOverflowError

__all__ = [
    "ContextBuilderConfig",
    "ContextBuilderError",
    "ContextBuilderService",
    "LockInOverflowError",
    "RetrievalConfig",
    "TierBudget",
]
