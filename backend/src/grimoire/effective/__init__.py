"""Effective world snapshots over one loaded library (Sprint 022): an adapter over the pure core.

Joins the native store's envelopes (010), entity meaning (009) and world-optional chats (107) into
``grimoire.core.snapshot.EffectiveSnapshot``. See docs/EFFECTIVE_VIEWS.md.
"""

from .library import ChatSnapshot, chat_selection, chat_snapshot, entity_decoder, explainer, selection_snapshot
from .inspect import compose_in, inspect_definition, view_reader

__all__ = ["ChatSnapshot", "chat_selection", "chat_snapshot", "compose_in", "entity_decoder", "explainer",
           "inspect_definition", "selection_snapshot", "view_reader"]
