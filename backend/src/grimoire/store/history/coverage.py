"""Complete admission and complete semantic evaluation are separate facts."""

from __future__ import annotations

from collections.abc import Sequence

from . import model


def verdict(
    candidates: Sequence[model.Candidate],
    depth: int,
    eligible: int,
    *,
    semantic_complete: bool = True,
    sources_complete: bool = True,
) -> model.Coverage:
    admitted = sum(bool(c.admitted_by) for c in candidates)
    agreement = sum(len(c.admitted_by) >= 2 for c in candidates)
    if not sources_complete:
        result = "thin" if admitted else "empty"
    elif not eligible or (admitted >= eligible and semantic_complete):
        result = "exhausted"
    elif not admitted:
        result = "empty"
    elif admitted >= min(depth, eligible) and agreement >= 1:
        result = "sufficient"
    else:
        result = "thin"
    return model.Coverage(result, admitted=admitted, agreement=agreement, eligible=eligible)
