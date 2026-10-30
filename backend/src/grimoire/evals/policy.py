"""Tolerances of the story-quality gate. Loosened values are always printed in the report."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class GatePolicy:
    max_voice_drop: float = 0.0
    cached_input_weight: float = 0.1
    max_cost_increase: float = 0.10
    max_latency_increase: float | None = None
    allowed_flags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("max_voice_drop", "cached_input_weight", "max_cost_increase",
                     "max_latency_increase"):
            value = getattr(self, name)
            if value is None and name == "max_latency_increase":
                continue
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite number >= 0")
        if not isinstance(self.allowed_flags, frozenset) or not all(
                isinstance(flag, str) and flag.strip() for flag in self.allowed_flags):
            raise ValueError("allowed_flags must be a frozenset of nonempty strings")

    def exact(self, name: str) -> Fraction:
        """A tolerance as an exact fraction, so ``0.1`` means one tenth at the boundary."""
        return Fraction(str(getattr(self, name)))

    def non_defaults(self) -> dict[str, object]:
        changed: dict[str, object] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if value != field.default:
                changed[field.name] = sorted(value) if isinstance(value, frozenset) else value
        return changed
