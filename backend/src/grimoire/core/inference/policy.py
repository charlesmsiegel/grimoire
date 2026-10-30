"""Which model calls a turn may cost, before any provider exists.

One RP generation per speaking NPC contribution is the V0.2.1 quality baseline
and is not a flag. Every other model-call stage is optional: disabled unless a
policy names it with a per-turn cap, so extra inference is opt-in and bounded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class InferenceStage(StrEnum):
    """Why a model is called. A call with no stage here cannot be planned."""

    NPC_GENERATION = "npc_generation"
    PLANNING = "planning"
    DECISION = "decision"
    EPISTEMIC_INFERENCE = "epistemic_inference"
    RECALL = "recall"
    GRAPH_LOOKUP = "graph_lookup"
    CRITIQUE = "critique"

    @property
    def is_baseline(self) -> bool:
        return self in BASELINE_STAGES


BASELINE_STAGES = frozenset({InferenceStage.NPC_GENERATION})
OPTIONAL_STAGES = frozenset(InferenceStage) - BASELINE_STAGES


def coerce_stage(value: InferenceStage | str) -> InferenceStage:
    """Accept a member or its config spelling; anything else is an error."""
    if not isinstance(value, str):
        raise ValueError(f"inference stage must be a string, not {value!r}")
    try:
        return InferenceStage(value)
    except ValueError:
        raise ValueError(f"unknown inference stage: {value!r}") from None


def count(value: object, label: str, minimum: int = 0) -> int:
    """An int of at least ``minimum``; ``bool`` is not a count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an int >= {minimum}, not {value!r}")
    return value


def identifier(value: object, label: str, error: type[ValueError] = ValueError) -> str:
    """A nonempty, non-blank string id."""
    if not isinstance(value, str) or not value.strip():
        raise error(f"{label} must be a nonempty string, not {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class InferencePolicy:
    """Opt-in optional stages and their per-turn call caps.

    ``per_turn_call_caps`` is given as a mapping and stored as a sorted tuple of
    ``(stage, cap)`` pairs so the policy is hashable and order-independent; that
    stored tuple is also accepted, so ``dataclasses.replace`` works. A stage that
    is absent is disabled. ``max_attempts_per_call`` bounds retries of every
    call, baseline included.
    """

    per_turn_call_caps: Mapping[InferenceStage | str, int] | tuple[tuple[InferenceStage, int], ...] = (
        field(default=())
    )
    max_attempts_per_call: int = 1

    def __post_init__(self) -> None:
        raw = self.per_turn_call_caps
        if isinstance(raw, Mapping):
            pairs = list(raw.items())
        elif isinstance(raw, tuple):
            pairs = list(raw)
        else:
            raise ValueError("per_turn_call_caps must be a mapping of stage to cap")
        caps: dict[InferenceStage, int] = {}
        for pair in pairs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError(f"malformed cap entry: {pair!r}")
            stage = coerce_stage(pair[0])
            if stage.is_baseline:
                raise ValueError(f"{stage} is baseline inference, not an optional flag")
            if stage in caps:
                raise ValueError(f"duplicate cap for {stage}")
            caps[stage] = count(pair[1], f"cap for {stage}", minimum=1)
        object.__setattr__(self, "per_turn_call_caps", tuple(sorted(caps.items())))
        count(self.max_attempts_per_call, "max_attempts_per_call", minimum=1)

    @property
    def caps(self) -> Mapping[InferenceStage, int]:
        return MappingProxyType(dict(self.per_turn_call_caps))

    def cap_for(self, stage: InferenceStage | str) -> int | None:
        return self.caps.get(coerce_stage(stage))

    def enabled(self, stage: InferenceStage | str) -> bool:
        return self.cap_for(stage) is not None
