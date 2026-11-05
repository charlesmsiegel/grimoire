"""The one error type of ``grimoire.effects`` and its shared field checks (Sprint 021).

Errors carry a reason and ids, never record text, so they are safe to log.
"""

from __future__ import annotations

from collections.abc import Iterable

TEXT_MAX = 512  # principals, ids and attribution text


class EffectError(ValueError):
    """A proposal, record or request that breaks a 021 rule. ``reason`` is machine-readable."""

    def __init__(self, reason: str, records: Iterable[str] = ()) -> None:
        self.reason = reason
        self.records: tuple[str, ...] = tuple(str(record) for record in records)  # never fails on a bad input
        super().__init__(f"{reason}: {', '.join(self.records)}" if self.records else reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.records))


def invalid(*records: str) -> EffectError:
    return EffectError("invalid_field", records)


def text(value: object, limit: int = TEXT_MAX) -> None:
    """Nonblank, storable UTF-8 text of at most ``limit`` characters."""
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise invalid()
    try:
        value.encode("utf-8")  # a lone surrogate could never be stored: refuse it by name, here
    except UnicodeEncodeError:
        raise invalid() from None
