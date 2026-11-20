"""Consolidate one editor's summaries of an actor into a dossier, traced to exact evidence (053).

A summary is authored text that cites evidence the actor held; nothing here writes or rewords one. On
every read each summary is resolved again against the actor's projection and the editor's corrections:

- one that cites evidence a correction made obsolete is ``stale`` and is never current again,
  whenever it was written, so a recap compiled before an identity was corrected (or filed later
  from those older scenes) cannot bring the obsolete identity back;
- one that names an account to replace (``supersedes``) displaces it, and the chain that account
  replaced, only when it is itself supported and rests on evidence at least as late as every supported
  account on that chain: otherwise it is kept as ``older_basis`` history and the chain stands as it was.
  The result does not depend on the order the summaries arrive in;
- standing accounts of one topic that no editorial step reconciled are all kept, as ``contested``.

``as_of`` is the campaign knowledge sequence, as for ``consolidate_view``. Nothing here reads files,
writes or calls a model.
"""
from dataclasses import replace

from grimoire.core.identity import validate_segment
from grimoire.core.perspective import ActorKnowledge
from .model import (
    DossierTopic, MemoryCorrection, MemoryDossier, MemorySelection, MemorySummary, SummaryItem,
)
from .view import consolidate_view


def _links(corrections, as_of):
    links = {}
    for correction in corrections:
        if correction.moment.sequence <= as_of:
            for old in correction.obsolete_ids:
                if old in links:
                    raise ValueError('competing_correction')
                links[old] = correction.replacement_ids
    return links


def _terminals(links, start):
    """The replacements ``start`` ends in after every correction: ids no correction made obsolete."""
    found, seen, pending = set(), set(), list(links.get(start, ()))
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        if item in links:
            pending.extend(links[item])
        else:
            found.add(item)
    return found


def consolidate_dossier(knowledge, collection_id, selections, corrections, summaries, *, editor, limit=100):
    """``MemoryDossier`` of ``knowledge.actor`` in ``collection_id`` as of ``knowledge.as_of``.

    ``selections``, ``corrections`` and ``summaries`` are one editor's (``editor``) operations for this
    actor; the library chooses them. ``limit`` (1-1000) bounds the evidence view and, separately, the
    history kept: every standing account is kept (a context pack's budget bounds what is shown), then
    the newest history up to ``limit``; ``omitted`` counts the history left out.
    """
    if type(knowledge) is not ActorKnowledge or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('invalid_view')
    if type(editor) is not str or not editor:
        raise ValueError('invalid_editor')
    validate_segment(collection_id, 'collection_id')
    selections, corrections, summaries = tuple(selections), tuple(corrections), tuple(summaries)
    if any(type(c) is not MemoryCorrection or c.actor != knowledge.actor for c in corrections):
        raise ValueError('invalid_correction')
    if any(type(s) is not MemorySelection or s.actor != knowledge.actor for s in selections):
        raise ValueError('invalid_selection')
    if any(type(s) is not MemorySummary or s.actor != knowledge.actor for s in summaries):
        raise ValueError('invalid_summary')
    view = consolidate_view(knowledge, collection_id, selections, corrections, limit=limit)
    as_of = knowledge.as_of
    links = _links(corrections, as_of)
    evidence = {e.evidence_id: e for e in knowledge.evidence if e.moment.sequence <= as_of}
    chosen = [s for s in summaries if s.collection_id == collection_id and s.moment.sequence <= as_of]
    by_id = {}
    for summary in chosen:
        if summary.operation_id in by_id:
            raise ValueError('duplicate_operation')
        by_id[summary.operation_id] = summary
    for summary in chosen:
        target = by_id.get(summary.supersedes) if summary.supersedes is not None else None
        if summary.supersedes is not None and (target is None or target.topic != summary.topic
                                               or target.moment.sequence > summary.moment.sequence):
            raise ValueError('unknown_supersedes')

    facts = {}
    for summary in chosen:
        cited = sorted((evidence[i] for i in summary.evidence_ids if i in evidence),
                       key=lambda e: (e.moment.sequence, e.evidence_id))
        obsolete = tuple(sorted(i for i in summary.evidence_ids if i in links))
        unavailable = tuple(sorted(i for i in summary.evidence_ids if i not in evidence))
        replacements = tuple(sorted(set().union(*(_terminals(links, i) for i in obsolete)))) if obsolete else ()
        status = 'stale' if obsolete else 'unsupported' if unavailable else None
        basis = max((e.moment.sequence for e in cited), default=None)
        scenes = tuple(dict.fromkeys(e.moment.scene_id for e in cited if e.moment.scene_id is not None))
        facts[summary.operation_id] = dict(summary=summary, status=status, basis=basis, scenes=scenes,
                                           evidence=tuple(cited), obsolete=obsolete, unavailable=unavailable,
                                           replacements=replacements, superseded_by=set())
    bars = {}

    def bar(op):
        """The latest evidence basis among the supported accounts on ``op``'s supersedes chain, or ``None``."""
        path, seen, node = [], set(), op
        while node is not None and node not in bars:
            if node in seen:
                raise ValueError('unknown_supersedes')  # a cycle of equal moments
            seen.add(node)
            path.append(node)
            node = by_id[node].supersedes
        best = bars.get(node)
        for name in reversed(path):
            own = facts[name]['basis'] if facts[name]['status'] is None else None
            best = own if best is None else best if own is None else max(best, own)
            bars[name] = best
        return bars[op]

    # Order-independent: an account replaces its chain only when its own basis is at least as late as every
    # supported account on that chain; an unsupported or stale account displaces nothing.
    effective, older = set(), set()
    for name, fact in facts.items():
        target = fact['summary'].supersedes
        if fact['status'] is None and target is not None:
            latest = bar(target)
            (effective if latest is None or fact['basis'] >= latest else older).add(name)
    displaced = set()
    for name in effective:
        child, node = name, by_id[name].supersedes
        while node is not None:
            facts[node]['superseded_by'].add(child)
            if node in displaced:
                break
            displaced.add(node)
            child, node = node, by_id[node].supersedes
    for name, fact in facts.items():
        if fact['status'] is None:
            fact['status'] = 'superseded' if name in displaced else 'older_basis' if name in older else None
    standing = {}
    for fact in facts.values():
        if fact['status'] is None:
            standing.setdefault(fact['summary'].topic, []).append(fact)
    for group in standing.values():
        for fact in group:
            fact['status'] = 'current' if len(group) == 1 else 'contested'

    def item(fact):
        s = fact['summary']
        return SummaryItem(s.operation_id, s.topic, s.text, s.moment, fact['status'], fact['evidence'], fact['basis'],
                           fact['scenes'], s.supersedes, tuple(sorted(fact['superseded_by'])), fact['obsolete'],
                           fact['replacements'], fact['unavailable'])

    live = sorted((f for f in facts.values() if f['status'] in ('current', 'contested')),
                  key=lambda f: (f['summary'].topic, -f['basis'], f['summary'].operation_id))
    past = sorted((f for f in facts.values() if f['status'] not in ('current', 'contested')),
                  key=lambda f: (-f['summary'].moment.sequence, f['summary'].operation_id))
    kept = live + past[:limit]  # every standing account; ``limit`` bounds the history kept beside them
    topics = {}
    for fact in kept:
        entry = topics.setdefault(fact['summary'].topic, ([], []))
        entry[0 if fact['status'] in ('current', 'contested') else 1].append(fact)
    built = tuple(DossierTopic(name, tuple(item(f) for f in current),
                               tuple(item(f) for f in sorted(history, key=lambda f: (f['summary'].moment.sequence,
                                                                                    f['summary'].operation_id))))
                  for name, (current, history) in sorted(topics.items()))
    return MemoryDossier(knowledge.actor, collection_id, as_of, editor, view, built, tuple(sorted(links)),
                         len(facts) - len(kept))


def corrected_knowledge(knowledge, memory):
    """``knowledge`` without what ``memory``'s corrections made obsolete: that evidence, the reports sent
    as it and the beliefs citing it. The one projection a pack, a brief and an agenda's choice all read,
    so nothing reintroduces a corrected detail. ``memory`` must be the same actor's dossier as of the same
    moment (``memory_mismatch``).
    """
    if (type(knowledge) is not ActorKnowledge or type(memory) is not MemoryDossier
            or memory.actor != knowledge.actor or memory.as_of != knowledge.as_of):
        raise ValueError('memory_mismatch')
    obsolete = set(memory.suppressed)
    if not obsolete:
        return knowledge

    def beliefs(items):
        return None if items is None else tuple(b for b in items if obsolete.isdisjoint(b.evidence_ids))

    return replace(knowledge, evidence=tuple(e for e in knowledge.evidence if e.evidence_id not in obsolete),
                   sent=tuple(s for s in knowledge.sent if s.evidence_id not in obsolete),
                   current=beliefs(knowledge.current), inherited=beliefs(knowledge.inherited))
