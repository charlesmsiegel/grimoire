"""Store-backed reviewed rebase and chat-canon proposal (Sprint 042): an adapter over ``grimoire.store``.

``SourceRebase`` reviews and applies a rebase of one campaign actor onto a newer revision of its
source; ``CanonProposer`` proposes a chat's picked actors as a new reusable world. Trusted application
code; never handed to a worker. See docs/REBASE.md.
"""

from .canon import FIRST_REVISION, MAX_PICKS, CanonEntry, CanonPick, CanonProposal, CanonProposer, CanonReport
from .rebase import (
    ACTOR_REBOUND, ANCESTOR_UNVERIFIED, HISTORY_REVIEW, RebaseChange, RebaseHistory, RebaseReview, SourceRebase,
)

__all__ = ["ACTOR_REBOUND", "ANCESTOR_UNVERIFIED", "FIRST_REVISION", "HISTORY_REVIEW", "MAX_PICKS", "CanonEntry",
           "CanonPick", "CanonProposal", "CanonProposer", "CanonReport", "RebaseChange", "RebaseHistory",
           "RebaseReview", "SourceRebase"]
