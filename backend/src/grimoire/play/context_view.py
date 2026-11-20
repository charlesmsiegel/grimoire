"""The safe view of one NPC's assembled context: what 112's inspector and 085's draft preview return.

One function builds it for both, so the preview can never show more than the inspector: the brief's system text
and what the NPC heard, each packed item's line, lane, tokens and provenance, each item the budget shed (its lane,
reason, tokens and provenance, never its line), and the budget. Provenance names a record only by the kinds the
pack carries (source, entry name, record, belief, evidence ids) and names the NPC itself, never another actor:
no selection bases, no source-wide diagnostics, no snapshot and no secret markers.
"""

from __future__ import annotations

from grimoire.context import ContextPack, Origin
from grimoire.core.identity import InstanceRef
from grimoire.generation.brief import heard_transcript


def origin_view(value: Origin, actor: InstanceRef) -> dict:
    """``value``'s provenance as plain data: no selection bases, global diagnostics or other actors' identities."""
    result: dict[str, object] = {"kind": value.kind}
    for name in ("source", "name", "record", "belief"):
        field = getattr(value, name)
        if field is not None:
            result[name] = str(field)
    if value.actor == actor:
        result["actor"] = str(actor)
    if value.evidence:
        result["evidence"] = list(value.evidence)
    return result


def context_view(brief, pack: ContextPack, actor: InstanceRef, *, label: str) -> dict:  # type: ignore[no-untyped-def]
    """The inspector's answer for ``actor``: its 027 ``brief`` and 031 ``pack``, as the safe view above."""
    return {"label": label, "actor": str(actor),
            "system": brief.system, "heard": heard_transcript(brief),
            "items": [{"line": item.line, "tokens": item.tokens, "lane": item.lane,
                       "provenance": origin_view(item.origin, actor)} for item in pack.items],
            "omitted": [{"reason": item.reason, "tokens": item.tokens, "lane": item.lane,
                         "provenance": origin_view(item.origin, actor)} for item in pack.omitted],
            "budget": {"tokens": pack.tokens, "max_tokens": pack.budget.max_tokens,
                       "measurement": pack.measurement, "lane_caps": dict(pack.budget.lane_caps),
                       "item_overhead": pack.budget.item_overhead}}


__all__ = ["context_view", "origin_view"]
