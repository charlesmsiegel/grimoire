"""Selective offscreen world progression (076, first slice): faction plans advanced by calendar skips.

Pure values and deterministic replay; no model, roster scan, knowledge delivery or accepted effect.
The store-backed adapter is ``grimoire.offscreenstore``. See docs/OFFSCREEN.md.
"""
from .codec import OFFSCREEN_KEY, OFFSCREEN_SCHEMA, state_from_payload, state_to_payload
from .model import (
    MAX_BUDGET,
    MAX_PLANS,
    MAX_STEPS,
    OUTCOMES,
    Adopt,
    Adopted,
    Advanced,
    DeadlineOutcome,
    Development,
    FactionPlan,
    OffscreenError,
    OffscreenState,
    PlanProgress,
    PlanStep,
    Skip,
)
from .schedule import apply, pending_skips

__all__ = [
    "MAX_BUDGET", "MAX_PLANS", "MAX_STEPS", "OFFSCREEN_KEY", "OFFSCREEN_SCHEMA", "OUTCOMES", "Adopt", "Adopted",
    "Advanced", "DeadlineOutcome", "Development", "FactionPlan", "OffscreenError", "OffscreenState", "PlanProgress",
    "PlanStep", "Skip", "apply", "pending_skips", "state_from_payload", "state_to_payload",
]
