"""Typed relationship graph (Sprint 017): pure relation values, codec, derived index and actor claims.

Canonical relations are director truth. The derived ``GraphIndex`` is director-only and
rebuildable. What an actor thinks is connected comes only from its own 012 projection,
one node at a time (``claims_about``). Nothing here touches files or calls a model;
``grimoire.graphstore`` is the store-backed service.
"""

from .model import PREDICATES, Ending, GraphError, Predicate, Relation, Retraction, UnsupportedRelation

__all__ = ["PREDICATES", "Ending", "GraphError", "Predicate", "Relation", "Retraction", "UnsupportedRelation"]
