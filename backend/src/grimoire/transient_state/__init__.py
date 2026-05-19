"""Per-field ephemeral state with provenance, supersession, and lazy decay.

See docs/superpowers/specs/2026-05-19-transient-state-design.md.
"""

from grimoire.transient_state.routing import RoutingSummary, route_transient_updates
from grimoire.transient_state.service import TransientStateService

__all__ = [
    "RoutingSummary",
    "TransientStateService",
    "route_transient_updates",
]
