"""Merge and deduplicate strategy outputs.

The three strategies can each emit overlapping items (e.g. both the
rule-based and structured-LLM strategies can detect a time advance).
Merge keys are chosen to be conservative: when in doubt we keep both
items rather than collapsing distinct intents.
"""

from __future__ import annotations

from grimoire.types.extraction import EntityCandidate
from grimoire.types.state import DeltaKind, StateDelta


def _delta_key(delta: StateDelta) -> tuple:
    """A stable key for deduplication.

    Two deltas with the same `(kind, target_id)` and overlapping
    `target_table` are considered the same proposal regardless of which
    strategy produced them.
    """
    return (delta.kind.value, delta.target_id, delta.target_table or "")


def merge_deltas(*delta_lists: list[StateDelta]) -> list[StateDelta]:
    """Deduplicate deltas across strategies, keeping the highest-confidence one.

    When two deltas merge, the kept one inherits the longer evidence and
    union-of-strategies in `extra.strategies` for traceability.
    """
    best: dict[tuple, StateDelta] = {}
    for deltas in delta_lists:
        for delta in deltas:
            key = _delta_key(delta)
            if key not in best:
                best[key] = delta
                continue
            existing = best[key]
            keep = delta if delta.confidence > existing.confidence else existing
            other = existing if keep is delta else delta
            # Merge metadata.
            strategies = set()
            for d in (keep, other):
                s = d.extra.get("strategy")
                if isinstance(s, str):
                    strategies.add(s)
                multi = d.extra.get("strategies")
                if isinstance(multi, list):
                    strategies.update(s for s in multi if isinstance(s, str))
            extra = dict(keep.extra)
            extra["strategies"] = sorted(strategies)
            evidence = (
                keep.evidence if len(keep.evidence) >= len(other.evidence) else other.evidence
            )
            best[key] = keep.model_copy(update={"evidence": evidence, "extra": extra})
    # Stable ordering: by kind then target_id then confidence desc.
    return sorted(
        best.values(),
        key=lambda d: (d.kind.value, d.target_id, -d.confidence),
    )


def merge_candidates(*candidate_lists: list[EntityCandidate]) -> list[EntityCandidate]:
    """Deduplicate entity candidates by (kind, proposed_id, normalized name)."""
    best: dict[tuple[str, str, str], EntityCandidate] = {}
    for cands in candidate_lists:
        for cand in cands:
            key = (
                cand.kind.value if hasattr(cand.kind, "value") else str(cand.kind),
                cand.proposed_id,
                cand.proposed_name.lower().strip(),
            )
            current = best.get(key)
            if current is None or cand.confidence > current.confidence:
                best[key] = cand
    return sorted(best.values(), key=lambda c: (-c.confidence, c.proposed_name))


def split_facts_from_others(deltas: list[StateDelta]) -> tuple[list[StateDelta], list[StateDelta]]:
    """Partition deltas into facts and non-facts (used during contradiction checks)."""
    facts, others = [], []
    for delta in deltas:
        if delta.kind == DeltaKind.FACT_ADD:
            facts.append(delta)
        else:
            others.append(delta)
    return facts, others


__all__ = ["merge_candidates", "merge_deltas", "split_facts_from_others"]
