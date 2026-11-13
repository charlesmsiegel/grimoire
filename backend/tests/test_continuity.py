import copy
from types import MappingProxyType

import pytest

from grimoire.continuity import Dependency, Pin, RetconRequest, Rehearsal, preview_impact
from grimoire.continuitywire.projection import scene_revision


def pin(name, campaign='c', revision='a'):
    return Pin(f'campaigns/{campaign}/turns/{name}.json', 'sha256:' + revision * 64)


def test_transfer_possession_dialogue_and_potential_summary():
    source, possession, dialogue, summary, unrelated = map(pin, ('transfer', 'possession', 'dialogue', 'summary', 'other'))
    edges = (Dependency(source, possession, 'asset', 'known'), Dependency(possession, dialogue, 'dialogue', 'known'),
             Dependency(dialogue, summary, 'summary', 'potential'))
    report = preview_impact((source, possession, dialogue, summary, unrelated), edges, (source,))
    assert report.complete
    assert {i.target for i in report.impacts} == {possession, dialogue, summary}
    assert next(i for i in report.impacts if i.target == dialogue).chain == (source, possession, dialogue)
    assert next(i for i in report.impacts if i.target == summary).certainty == 'potential'
    assert report == preview_impact((unrelated, summary, dialogue, possession, source), tuple(reversed(edges)), (source,))


@pytest.mark.parametrize('case', ['cycle', 'missing', 'stale', 'duplicate', 'cross_campaign', 'bounds'])
def test_bad_graph_is_incomplete(case):
    a, b = pin('a'), pin('b')
    nodes, edges, roots, kwargs = (a, b), [Dependency(a, b, 'dialogue', 'known')], (a,), {}
    if case == 'cycle': edges.append(Dependency(b, a, 'dialogue', 'known'))
    if case == 'missing': nodes = (a,)
    if case == 'stale': nodes = (pin('a', revision='b'), b)
    if case == 'duplicate': edges *= 2
    if case == 'cross_campaign': nodes += (pin('x', campaign='other'),)
    if case == 'bounds': kwargs['max_nodes'] = 1
    report = preview_impact(nodes, edges, roots, **kwargs)
    assert not report.complete and report.problems


def test_scene_projection_ignores_effect_completion_and_bookkeeping():
    payload = {'chat': 'chat', 'number': 1, 'post': {'text': 'key', 'actor': 'a', 'audience': ['b']},
               'takes': [{'state': 'kept', 'lines': [{'text': 'yes', 'actor_revision': 'source@1'}], 'calls': []}],
               'effects': [{'state': 'accepting'}], 'labels': []}
    before = scene_revision(payload)
    changed = copy.deepcopy(payload)
    changed.update(effects=[{'state': 'accepted'}], labels=[{'label': 'new'}], intentions=['new'])
    changed['takes'][0]['calls'] = ['new']
    assert scene_revision(changed) == before
    for path, field, value in [(('post',), 'text', 'no key'), (('post',), 'actor', 'c'),
                                (('post',), 'audience', ['d']),
                                (('takes', 0, 'lines', 0), 'actor_revision', 'source@2')]:
        edited = copy.deepcopy(changed)
        target = edited
        for part in path: target = target[part]
        target[field] = value
        assert scene_revision(edited) != before


def test_refs_and_rehearsal_are_bounded_and_speculative():
    with pytest.raises(ValueError): Pin('../escape', 'sha256:' + 'a' * 64)
    with pytest.raises(ValueError): Pin('campaigns/c/turns/t.json', 'no')
    request = RetconRequest('fork', (pin('a'),))
    rehearsal = Rehearsal(request, ('Keep the key.', 'Give the key away.'))
    assert rehearsal.status == 'speculative' and rehearsal.model_cost == 0
    assert not hasattr(rehearsal, 'commit') and not hasattr(rehearsal, 'accept')


def test_scene_revision_accepts_frozen_native_payload():
    plain = {'post': {'text': 'A key', 'audience': ['a']}, 'takes': [{'lines': [{'text': 'Hello'}], 'calls': []}]}
    frozen = MappingProxyType({'post': MappingProxyType({'text': 'A key', 'audience': ('a',)}),
                               'takes': (MappingProxyType({'lines': (MappingProxyType({'text': 'Hello'}),), 'calls': ()}),)})
    assert scene_revision(plain) == scene_revision(frozen)


@pytest.mark.parametrize('path', ['campaigns/c/unknown/x.json', 'campaigns/c/turns/../x.json',
                                  'campaigns/C/turns/x.json', 'campaigns/c/turns/.json',
                                  'campaigns/c/turns/nul.json', 'campaigns/c/turns/x.json/extra'])
def test_pin_refuses_noncanonical_paths(path):
    with pytest.raises(ValueError): Pin(path, 'sha256:' + 'a' * 64)


def test_edge_bounds_and_known_path_preference():
    a, b, c = map(pin, ('a', 'b', 'c'))
    edges = (Dependency(a, c, 'chronological', 'potential'), Dependency(a, b, 'asset', 'known'),
             Dependency(b, c, 'dialogue', 'known'))
    report = preview_impact((a, b, c), edges, (a,))
    assert report.complete
    found = next(i for i in report.impacts if i.target == c)
    assert found.chain == (a, b, c) and found.certainty == 'known'
    assert not preview_impact((a, b, c), edges, (a,), max_edges=1).complete
    with pytest.raises(ValueError): Dependency(a, b, 'chronological', 'known')
