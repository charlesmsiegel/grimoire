"""Context Builder exceptions."""

from __future__ import annotations


class ContextBuilderError(Exception):
    """Base class for Context Builder errors."""


class LockInOverflowError(ContextBuilderError):
    """Raised when required lock-in tier content cannot fit the budget.

    Spec 02: ``If lock-in tier overflows the budget, that's a configuration
    error — the system surfaces it rather than silently dropping.``
    """

    def __init__(self, used: int, budget: int) -> None:
        super().__init__(
            f"lock-in tier requires {used} tokens but budget is {budget}; "
            "raise context_builder.tiers.lock_in.max or trim required content"
        )
        self.used = used
        self.budget = budget
