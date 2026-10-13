"""Admitted-only RRF lists retain their round, without a fused confidence.

Expansion builds a full EvidenceItem through a caller-bound adapter (headers,
summary and prompt excerpts); candidates deliberately carry no prompt payload.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import replace

from .. import tokens
from . import budget, coverage, model, settings

RRF_K = 60


def order(candidates: Sequence[model.Candidate]) -> list[model.Candidate]:
    def value(c: model.Candidate) -> float:
        return sum(
            1 / (RRF_K + rank)
            for (n, signal), rank in c.ranks.items()
            if signal in c.admitted_by
            and rank > 0
            and n < len(c.rounds)
            and getattr(c.rounds[n], signal, None) is not None
        )

    out = sorted((c for c in candidates if c.admitted_by), key=lambda c: c.scene.sid, reverse=True)
    out.sort(key=value, reverse=True)
    return out


def select(
    candidates: Sequence[model.Candidate],
    depth: int,
    expand: Callable[[model.Candidate], model.EvidenceItem | None],
) -> list[model.EvidenceItem]:
    """An unusable/omitted-summary-only scene spends no depth slot (09 M6)."""
    out: list[model.EvidenceItem] = []
    for candidate in candidates:
        if len(out) >= depth:
            break
        item = expand(candidate)
        if item is not None and (item.excerpts or (item.summary and not item.summary_omission)):
            out.append(item)
    return out


def _semantic_complete(cv: model.Coverage) -> bool:
    """Whether a round left no eligible document unscored by an available
    semantic signal: it had none (off, preview, failed, backing off) -- so
    complete admission is enough -- or it already reached `exhausted`, which
    with semantic `ok` means every eligible document was evaluated. An `ok`
    round short of that is not known complete, and merging never claims it."""
    return cv.semantic != "ok" or cv.verdict == "exhausted"


def merge(
    a: model.Evidence,
    b: model.Evidence,
    *,
    ceiling: int,
    expand: Callable[[model.Candidate], model.EvidenceItem | None],
    rerank: Callable[[list[model.Candidate]], Sequence[model.Candidate]] | None = None,
) -> model.Evidence:
    cfg = settings.read()
    if cfg is None:
        return model.Evidence(ceiling=min(ceiling, a.ceiling, b.ceiling))
    offset = max((len(c.rounds) for c in a.candidates), default=0)
    width = max((len(c.rounds) for c in b.candidates), default=0)
    combined: dict[str, model.Candidate] = {}
    blank = model.Signals()
    for c in a.candidates:
        combined[c.scene.key] = replace(
            c, rounds=c.rounds + (blank,) * (offset + width - len(c.rounds))
        )
    for c in b.candidates:
        previous = combined.get(c.scene.key)
        ranks = {(n + offset, signal): rank for (n, signal), rank in c.ranks.items()}
        if previous is None:
            combined[c.scene.key] = replace(c, rounds=(blank,) * offset + c.rounds, ranks=ranks)
        else:
            combined[c.scene.key] = replace(
                previous,
                rounds=previous.rounds[:offset] + c.rounds,
                ranks={**previous.ranks, **ranks},
                admitted_by=previous.admitted_by | c.admitted_by,
                tier=min(previous.tier, c.tier),
            )
    ordered = order(list(combined.values()))
    if rerank is not None:
        ordered = list(rerank(ordered))
    held: dict[str, model.EvidenceItem] = {}
    for item in (*a.items, *b.items):
        previous_item = held.get(item.scene.key)
        # A later query can find no anchor for a previously expanded scene.
        # Keep the earlier post/id payload; a summary cannot erase evidence.
        if previous_item is None or not previous_item.excerpts:
            held[item.scene.key] = item
    items = select(
        ordered, cfg.depth, lambda c: held[c.scene.key] if c.scene.key in held else expand(c)
    )
    cap = min(max(ceiling, 0), a.ceiling, b.ceiling)
    fitted = budget.fit(items, cap, tokens.count_tokens, depth=cfg.depth)
    cv = coverage.verdict(
        ordered,
        cfg.depth,
        max(a.coverage.eligible, b.coverage.eligible),
        semantic_complete=_semantic_complete(a.coverage) and _semantic_complete(b.coverage),
    )
    cv = replace(
        cv,
        tiers=tuple(sorted(set(a.coverage.tiers + b.coverage.tiers))),
        semantic=a.coverage.semantic if a.coverage.semantic == b.coverage.semantic else "mixed",
    )
    digest = hashlib.sha256((a.query_digest + "\0" + b.query_digest).encode()).hexdigest()
    return model.Evidence(tuple(fitted), tuple(ordered), cv, cap, digest)
