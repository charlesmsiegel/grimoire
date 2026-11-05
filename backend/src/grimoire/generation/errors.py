"""Generation contract errors. Messages cite ids and reasons, never authored or generated text."""

from __future__ import annotations


class GenerationError(ValueError):
    """A turn, brief or reply that cannot be generated safely: refused before any model call."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.message))
