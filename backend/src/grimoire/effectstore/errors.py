"""The service's refusal (Sprint 021): one exception type, a machine-readable reason, ids only."""

from __future__ import annotations

from collections.abc import Iterable

from grimoire.effects import EffectError


class InvalidEffect(ValueError):
    """An effect change that cannot be made; nothing was written. ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str, records: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.reason = reason
        self.records = tuple(records)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.records))


def refused(error: EffectError) -> InvalidEffect:
    return InvalidEffect(f"the effect change is refused: {error}", error.reason, error.records)
