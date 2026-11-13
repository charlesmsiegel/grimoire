from dataclasses import replace

import pytest

from grimoire.core import DIRECTOR, AuthorityError
from grimoire.topology import Path, Place, Topology, TopologyError, TopologyIndex
from grimoire.topology.codec import decode_topology, encode_topology
from topology_fixtures import GRAPH, TOWN, GATE, WOOD, PORT, OTHER, NAMES, topology

D = {'requested_by': DIRECTOR}


def test_four_places_hierarchy_aliases_and_direction():
    index = TopologyIndex(topology(), NAMES)
    assert index.ancestors(GATE, **D) == (TOWN,)
    assert index.children(TOWN, **D) == (GATE,)
    assert index.matches('town', **D) == (TOWN,)
    assert index.matches('Saltmarch', **D) == (TOWN,)
    assert index.neighbors(GATE, **D) == (('bridge', PORT), ('road', TOWN))
    route = index.route(TOWN, PORT, **D)
    assert route.places == (TOWN, GATE, PORT) and route.paths == ('road', 'bridge')
    assert index.route(PORT, TOWN, **D) is None
    assert index.route(TOWN, TOWN, **D).places == (TOWN,)


def test_block_and_condition_tokens_never_implicit():
    graph = topology()
    blocked = replace(graph, paths=tuple(replace(p, blocked=True) if p.path_id == 'road' else p for p in graph.paths))
    index = TopologyIndex(blocked, NAMES)
    assert index.route(TOWN, PORT, **D) is None
    assert index.route(TOWN, PORT, conditions=('unrelated',), **D) is None
    assert index.route(TOWN, PORT, conditions=('ticket',), **D).paths == ('trail', 'ferry')


def test_missing_imported_namesake_never_substituted():
    graph = replace(topology(), places=topology().places + (Place(OTHER, ('Town',)),))
    index = TopologyIndex(graph, {**NAMES, OTHER: 'Saltmarch'})
    assert set(index.matches('Saltmarch', **D)) == {TOWN, OTHER}
    index = TopologyIndex(graph, {k: v for k, v in {**NAMES, OTHER: 'Saltmarch'}.items() if k != TOWN})
    assert index.missing == (TOWN,)
    with pytest.raises(TopologyError, match='missing_place'):
        index.route(TOWN, PORT, **D)
    assert index.route(OTHER, PORT, **D) is None


def test_bounds_are_not_unreachable_and_cycle_terminates():
    index = TopologyIndex(topology(), NAMES)
    assert index.reach(TOWN, max_depth=0, **D).truncated
    with pytest.raises(TopologyError, match='search_limit'):
        index.route(TOWN, PORT, max_depth=1, **D)
    with pytest.raises(TopologyError, match='search_limit'):
        index.route(TOWN, PORT, max_steps=1, **D)
    assert set(index.reach(TOWN, **D).reached) == set(NAMES)
    for invalid in (-1, True, 5000):
        with pytest.raises(TopologyError):
            index.reach(TOWN, max_steps=invalid, **D)


@pytest.mark.parametrize('method,args', [('matches', ('Town',)), ('ancestors', (GATE,)),
    ('children', (TOWN,)), ('neighbors', (TOWN,)), ('reach', (TOWN,)), ('route', (TOWN, PORT))])
def test_director_only_queries(method, args):
    with pytest.raises(AuthorityError):
        getattr(TopologyIndex(topology(), NAMES), method)(*args, requested_by='actor')


@pytest.mark.parametrize('mutate', [
    lambda g: replace(g, places=g.places + (g.places[0],)),
    lambda g: replace(g, places=g.places + (Place(TOWN.at('r2')),)),
    lambda g: replace(g, paths=g.paths + (g.paths[0],)),
    lambda g: replace(g, paths=(Path('x', TOWN, OTHER),)),
    lambda g: replace(g, places=(Place(TOWN, parent=GATE), Place(GATE, parent=TOWN))),
    lambda g: replace(g, places=(Place(TOWN, parent=OTHER),)),
    lambda g: replace(g, ref=GRAPH.unpinned()),
    lambda g: replace(g, places=(Place(TOWN.unpinned()),)),
    lambda g: replace(g, paths=(Path('x', TOWN, GATE, bidirectional='yes'),)),
    lambda g: replace(g, paths=(Path('x', TOWN, GATE, requires=('ticket', 'ticket')),)),
])
def test_invalid_graph_refused(mutate):
    with pytest.raises((TopologyError, ValueError)):
        mutate(topology())


def test_codec_strict_roundtrip():
    payload = encode_topology(topology())
    assert decode_topology(GRAPH, payload) == topology()
    for key, value in [('schema', 'future/9'), ('unknown', True), ('paths', None)]:
        with pytest.raises(TopologyError):
            decode_topology(GRAPH, {**payload, key: value})
