"""Read-only bounded navigation over one loaded canonical snapshot (065).

Response size is bounded; loading/scanning the underlying file snapshot is not indexed.
"""
from __future__ import annotations

import hashlib
import json
from grimoire.core.identity import SourceRef
from grimoire.core.index import MissingDefinition
from grimoire.core.portrayal import PortrayalError
from grimoire.effective.inspect import compose_in
from .errors import ApiRefused
from .views import source_key

PAGE = 50


def workspace(api, args):  # type: ignore[no-untyped-def]
    # Import shared validation lazily: api dispatch imports this module.
    from .api import field, parse_ref
    which = field(args, 'filter', str, required=False, default='all')
    if which not in ('all', 'recent', 'character', 'scenario', 'campaign', 'world', 'pinned'):
        raise ApiRefused('invalid_field', 'unknown workspace filter')
    match = field(args, 'match', str, required=False, default='exact')
    sort = field(args, 'sort', str, required=False, default='recent')
    if match not in ('exact', 'derived') or sort not in ('recent', 'name'):
        raise ApiRefused('invalid_field', 'unknown match or sort')
    q = field(args, 'q', str, required=False, default='').casefold().strip()
    sq = field(args, 'source_q', str, required=False, default='').casefold().strip()
    limit = field(args, 'limit', int, required=False, default=PAGE)
    offsets = [field(args, key, int, required=False, default=0) for key in ('offset', 'source_offset', 'comparison_offset')]
    if not 1 <= limit <= PAGE or any(n < 0 for n in offsets):
        raise ApiRefused('invalid_field', 'limit must be 1..50 and offsets nonnegative')
    offset, source_offset, comparison_offset = offsets
    snapshot = field(args, 'snapshot', str, required=False)
    if any(offsets) and not snapshot:
        raise ApiRefused('invalid_field', 'continuation requires snapshot')
    source = parse_ref(SourceRef.parse, args, 'source', required=False)
    campaign = field(args, 'campaign', str, required=False)
    world = field(args, 'world', str, required=False)
    pins = field(args, 'pins', list, required=False, default=[])
    if len(pins) > 10000 or any(type(ref) is not str for ref in pins):
        raise ApiRefused('invalid_field', 'pins must be chat references')
    view = api.store.load()
    index = api.chats.navigation(view=view)
    entries = [index.entry(ref) for ref in index.all_chats()]
    names = api._mention_names(view, entries)
    candidates = sorted({SourceRef(*key, revision=rev) for entry in entries
                         for role, key, rev, active in entry.mentions
                         if active and role in ('character', 'scenario')}, key=str)
    diagnostics, facts, sources, comparisons = [], [], [], []
    accepted = set()
    for ref in candidates:
        path, problem = [], None
        try:
            effective = compose_in(view, ref)
            path = [str(node.ref) for node in effective.chain]
            facts.append([(str(node.ref), node.etag) for node in effective.chain])
        except (MissingDefinition, PortrayalError, ValueError) as error:
            problem = getattr(error, 'reason', type(error).__name__)
            diagnostics.append({'ref': str(ref), 'code': str(problem)})
            facts.append((str(ref), str(problem)))
        item = {'ref': str(ref), 'role': ref.kind, 'name': names.get(str(ref.unpinned())),
                'path': path, 'problem': problem}
        sources.append(item)
        exact = source is not None and ref.entity_key == source.entity_key and (not source.pinned or ref == source)
        descendant = source is not None and match == 'derived' and any(
            SourceRef.parse(node).entity_key == source.entity_key and (not source.pinned or node == str(source))
            for node in path)
        if exact or descendant:
            accepted.add(ref)
            comparisons.append(item)
    fingerprint = hashlib.sha256(json.dumps([index.to_json(), names, facts,
        sorted((key, value.etag) for key, value in view.sources.items()),
        [(str(d.ref), d.name, getattr(d, "etag", None)) for d in view.index.definitions()],
        [(p.path, p.code, p.detail) for p in view.problems]], sort_keys=True).encode()).hexdigest()
    if snapshot is not None and snapshot != fingerprint:
        raise ApiRefused('changed', 'Navigation changed. Restart from the first page.')
    found = []
    for entry in entries:
        scope = index.scope(entry.ref.campaign_id)
        if which in ('character', 'scenario'):
            if not source or not any(active and role == which and SourceRef(*key, revision=rev) in accepted
                                     for role, key, rev, active in entry.mentions):
                continue
        if (which == 'campaign' or campaign) and entry.ref.campaign_id != campaign:
            continue
        if (which == 'world' or world) and (not world or scope is None or scope.world != world):
            continue
        if which == 'pinned' and str(entry.ref) not in pins:
            continue
        found.append(entry.ref)
    rows = api._entries(view, index, found, names)
    def title(row):
        return row['label'] or (row['launch'] or {}).get('name') or row['chat']
    if q:
        rows = [row for row in rows if all(word in json.dumps(row, ensure_ascii=False).casefold() for word in q.split())]
    if sort == 'name':
        rows.sort(key=lambda row: (title(row).casefold(), row['chat']))
    sources.sort(key=lambda item: ((item['name'] or '').casefold(), item['ref']))
    if which in ('character', 'scenario'):
        sources = [item for item in sources if item['role'] == which]
    if sq:
        sources = [item for item in sources if sq in ((item['name'] or '') + ' ' + item['ref']).casefold()]
    def next_at(at, values):
        return at + limit if at + limit < len(values) else None
    return {'filter': which, 'chats': rows[offset:offset + limit], 'total': len(rows),
            'next': next_at(offset, rows), 'snapshot': fingerprint,
            'sources': sources[source_offset:source_offset + limit], 'source_total': len(sources),
            'source_next': next_at(source_offset, sources),
            'comparisons': comparisons[comparison_offset:comparison_offset + limit],
            'comparison_total': len(comparisons), 'comparison_next': next_at(comparison_offset, comparisons),
            'diagnostics': diagnostics[:PAGE]}
