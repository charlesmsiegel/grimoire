"""Resolve selections and corrections exclusively through an actor projection."""
from grimoire.core.identity import validate_segment
from grimoire.core.perspective import ActorKnowledge
from .model import MemorySelection, MemoryCorrection, MemoryItem, MemoryView


def consolidate_view(knowledge, collection_id, selections, corrections, *, limit=100):
    if type(knowledge) is not ActorKnowledge or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('invalid_view')
    validate_segment(collection_id, 'collection_id')
    selections, corrections = tuple(selections), tuple(corrections)
    if any(type(s) is not MemorySelection or s.actor != knowledge.actor for s in selections):
        raise ValueError('invalid_selection')
    if any(type(c) is not MemoryCorrection or c.actor != knowledge.actor for c in corrections):
        raise ValueError('invalid_correction')
    selected = {i for s in selections if s.collection_id == collection_id and s.moment.sequence <= knowledge.as_of for i in s.evidence_ids}
    origins = {i: tuple(sorted(s.operation_id for s in selections if s.collection_id == collection_id and s.moment.sequence <= knowledge.as_of and i in s.evidence_ids)) for i in selected}
    links = {}
    correction_ids = {}
    for c in corrections:
        if c.moment.sequence <= knowledge.as_of:
            for old in c.obsolete_ids:
                if old in links:
                    raise ValueError('competing_correction')
                links[old] = c.replacement_ids
                correction_ids[old] = c.operation_id
    reachable = set(selected)
    pending = list(selected)
    while pending:
        item = pending.pop()
        for replacement in links.get(item, ()):
            if replacement not in reachable:
                reachable.add(replacement)
                pending.append(replacement)
    # Check cycles even if the records are supplied directly to this pure API.
    def visit(item, path):
        if item in path:
            raise ValueError('cyclic_correction')
        for replacement in links.get(item, ()):
            visit(replacement, path | {item})
    for item in reachable:
        visit(item, set())
    evidence = {e.evidence_id: e for e in knowledge.evidence if e.moment.sequence <= knowledge.as_of}
    ordered = sorted(reachable, key=lambda i: (evidence[i].moment.sequence, i) if i in evidence else (float('inf'), i))
    shown = ordered[:limit]
    current = tuple(MemoryItem(evidence[i], selection_ids=origins.get(i, ())) for i in shown if i in evidence and i not in links)
    history = tuple(MemoryItem(evidence[i], links[i], origins.get(i, ()), correction_ids[i]) for i in shown if i in evidence and i in links)
    return MemoryView(knowledge.actor, collection_id, knowledge.as_of, current, history,
                      tuple(i for i in shown if i not in evidence), len(ordered) - len(shown))
