"""Per-field ephemeral state with provenance, supersession, and lazy decay.

See docs/superpowers/specs/2026-05-19-transient-state-design.md.
"""

from grimoire.transient_state.service import TransientStateService

__all__ = ["TransientStateService"]
