"""The generation runtime (sprint 027): issue a turn's planned calls through the provider adapter.

An adapter package: it calls providers through ``grimoire.providers``. Planning, voices, briefs
and screening are pure (``grimoire.generation``). ``grimoire.runtime.record.recorded_turn`` hands a
finished turn to the story-quality ``/3`` recorder; it is imported from its own module, so using the
runtime never loads the eval recorder. See docs/GENERATION.md.
"""

from .fence import FenceToken, RunFence, cancel_when_stale
from .runtime import (
    DEFAULT_MAX_OUTPUT_TOKENS, NARRATOR, CallOutcome, GeneratedLine, GenerationRuntime, TurnOutcome,
)

__all__ = [
    "FenceToken", "RunFence", "cancel_when_stale",
    "DEFAULT_MAX_OUTPUT_TOKENS", "NARRATOR", "CallOutcome", "GeneratedLine", "GenerationRuntime", "TurnOutcome",
]
