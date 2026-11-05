"""The generation runtime (sprint 027): issue a turn's planned calls through the provider adapter.

An adapter package: it calls providers through ``grimoire.providers``. Planning, voices, briefs
and screening are pure (``grimoire.generation``). ``recorded_turn`` hands a finished turn to the
story-quality ``/3`` recorder. See docs/GENERATION.md.
"""

from .fence import FenceToken, RunFence, cancel_when_stale
from .record import recorded_turn
from .runtime import (
    DEFAULT_MAX_OUTPUT_TOKENS, NARRATOR, CallOutcome, GeneratedLine, GenerationRuntime, TurnOutcome,
)

__all__ = [
    "FenceToken", "RunFence", "cancel_when_stale",
    "DEFAULT_MAX_OUTPUT_TOKENS", "NARRATOR", "CallOutcome", "GeneratedLine", "GenerationRuntime", "TurnOutcome",
    "recorded_turn",
]
