"""Read-only bounded navigation over one canonical snapshot (065).

Every response is bounded. The snapshot itself comes from ``navcache``: a validated basis built from one
``store.load()`` and reused while the library's stamp is unchanged, so paging, searching and choosing a source
in a large library do not reparse it. A caller with no cache (a bare test double) composes a fresh basis.
"""
from __future__ import annotations

import copy

from grimoire.core.identity import SourceRef
from .errors import ApiRefused
from .navcache import Basis, build_basis

PAGE = 50
FILTERS = ('all', 'recent', 'character', 'scenario', 'campaign', 'world', 'pinned')


def basis_of(api) -> Basis:  # type: ignore[no-untyped-def]
    cache = getattr(api, 'navigation_cache', None)
    return cache.basis(api) if cache is not None else build_basis(api, api.store.load())


def scope_facets(basis: Basis, q: str) -> tuple[list[dict], list[dict]]:
    """Every campaign and world some chat stands in, with how many chats each holds, for the choosers that
    replace typing an exact ID. ``q`` (case-folded) narrows by ID or title. Campaigns sort by title then ID;
    worlds by ID. Chats in a scope with no world are counted under no world; nothing here is a guess."""
    campaigns: dict[str, dict] = {}
    worlds: dict[str, dict] = {}
    for ref in basis.order:
        scope = basis.index.scope(ref.campaign_id)
        item = campaigns.setdefault(ref.campaign_id, {'campaign': ref.campaign_id,
                                                      'title': scope.title if scope else None,
                                                      'world': scope.world if scope else None, 'chats': 0})
        item['chats'] += 1
        if item['world'] is not None:
            place = worlds.setdefault(item['world'], {'world': item['world'], 'campaigns': set(), 'chats': 0})
            place['campaigns'].add(ref.campaign_id)
            place['chats'] += 1
    listed = sorted(campaigns.values(), key=lambda item: ((item['title'] or item['campaign']).casefold(), item['campaign']))
    places = sorted(({**place, 'campaigns': len(place['campaigns'])} for place in worlds.values()),
                    key=lambda place: place['world'])
    if q:
        listed = [item for item in listed if q in (item['campaign'] + ' ' + (item['title'] or '')).casefold()]
        places = [place for place in places if q in place['world'].casefold()]
    return listed, places


def workspace(api, args):  # type: ignore[no-untyped-def]
    # Import shared validation lazily: api dispatch imports this module.
    from .api import field, parse_ref
    which = field(args, 'filter', str, required=False, default='all')
    if which not in FILTERS:
        raise ApiRefused('invalid_field', 'unknown workspace filter')
    match = field(args, 'match', str, required=False, default='exact')
    sort = field(args, 'sort', str, required=False, default='recent')
    if match not in ('exact', 'derived') or sort not in ('recent', 'name'):
        raise ApiRefused('invalid_field', 'unknown match or sort')
    q = field(args, 'q', str, required=False, default='').casefold().strip()
    sq = field(args, 'source_q', str, required=False, default='').casefold().strip()
    scope_q = field(args, 'scope_q', str, required=False, default='').casefold().strip()
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
    basis = basis_of(api)
    if snapshot is not None and snapshot != basis.fingerprint:
        raise ApiRefused('changed', 'Navigation changed. Restart from the first page.')
    accepted, comparisons = set(), []
    if source is not None:
        for item in basis.sources:
            ref = SourceRef.parse(item['ref'])
            exact = ref.entity_key == source.entity_key and (not source.pinned or ref == source)
            descendant = match == 'derived' and any(
                SourceRef.parse(node).entity_key == source.entity_key and (not source.pinned or node == str(source))
                for node in item['path'])
            if exact or descendant:
                accepted.add(ref)
                comparisons.append(item)
    pinned = set(pins)
    found = []
    for ref in basis.order:
        entry = basis.index.entry(ref)
        scope = basis.index.scope(ref.campaign_id)
        if which in ('character', 'scenario'):
            if not source or not any(active and role == which and SourceRef(*key, revision=rev) in accepted
                                     for role, key, rev, active in entry.mentions):
                continue
        if (which == 'campaign' or campaign) and ref.campaign_id != campaign:
            continue
        if (which == 'world' or world) and (not world or scope is None or scope.world != world):
            continue
        if which == 'pinned' and str(ref) not in pinned:
            continue
        if q and not all(word in basis.text[str(ref)] for word in q.split()):
            continue
        found.append(str(ref))
    if sort == 'name':
        def title(key):  # type: ignore[no-untyped-def]
            row = basis.rows[key]
            return row['label'] or (row['launch'] or {}).get('name') or row['chat']
        found.sort(key=lambda key: (title(key).casefold(), key))
    sources = sorted(basis.sources, key=lambda item: ((item['name'] or '').casefold(), item['ref']))
    if which in ('character', 'scenario'):
        sources = [item for item in sources if item['role'] == which]
    if sq:
        sources = [item for item in sources if sq in ((item['name'] or '') + ' ' + item['ref']).casefold()]
    # the choosers are drawn only under Campaign and World: other filters skip the work and get none
    campaigns, worlds = scope_facets(basis, scope_q) if which in ('campaign', 'world') else ([], [])

    def next_at(at, values):  # type: ignore[no-untyped-def]
        return at + limit if at + limit < len(values) else None
    # Rows and source items are shared by the cached basis: hand out copies, never the cached objects.
    return {'filter': which, 'chats': copy.deepcopy([basis.rows[key] for key in found[offset:offset + limit]]),
            'total': len(found), 'next': next_at(offset, found), 'snapshot': basis.fingerprint,
            'sources': copy.deepcopy(sources[source_offset:source_offset + limit]), 'source_total': len(sources),
            'source_next': next_at(source_offset, sources),
            'comparisons': copy.deepcopy(comparisons[comparison_offset:comparison_offset + limit]),
            'comparison_total': len(comparisons), 'comparison_next': next_at(comparison_offset, comparisons),
            'campaigns': campaigns[:PAGE], 'campaign_total': len(campaigns),
            'worlds': worlds[:PAGE], 'world_total': len(worlds),
            'diagnostics': copy.deepcopy(list(basis.diagnostics[:PAGE]))}
