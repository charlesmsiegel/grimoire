"""Expression sprites: per-character emotion state, classification, and serving.

The module owns:

- ``ExpressionStateService`` — read/write to the ``expression_state`` table.
- ``heuristic`` — keyword + punctuation rule-based classifier.
- ``llm_classifier`` — single-call LLM classifier per post.
- ``routing`` — confidence-band dispatch to state / review / discard.

The REST surface lives in ``grimoire.api.expressions``.
"""

from grimoire.expressions.heuristic import heuristic_classify
from grimoire.expressions.routing import (
    ROUTE_AUTO_APPLY,
    ROUTE_DISCARD,
    ROUTE_REVIEW,
    classify_route,
)
from grimoire.expressions.service import ExpressionStateService

__all__ = [
    "ROUTE_AUTO_APPLY",
    "ROUTE_DISCARD",
    "ROUTE_REVIEW",
    "ExpressionStateService",
    "classify_route",
    "heuristic_classify",
]
