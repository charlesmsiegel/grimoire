"""Provisional inference-cost contracts: call planning, flags and usage.

Pure policy and accounting (no provider, I/O or clock). Deliberately not
re-exported from ``grimoire.core``, whose vocabulary is the fiction domain, and
not yet a persisted schema. See ``docs/INFERENCE.md``.
"""

from .policy import (
    BASELINE_STAGES,
    OPTIONAL_STAGES,
    InferencePolicy,
    InferenceStage,
    coerce_stage,
)

__all__ = [
    "BASELINE_STAGES", "OPTIONAL_STAGES", "InferencePolicy", "InferenceStage",
    "coerce_stage",
]
