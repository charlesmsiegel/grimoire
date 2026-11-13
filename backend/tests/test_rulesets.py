"""Synthetic first proofs: incompatible rules proposals are never accepted effects."""
from dataclasses import replace

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.rulesets import (
    ActionRequest, ArsResult, ArsStudy, D20Check, D20Result, ModuleBinding,
    PbtaMove, PbtaResult, ResolutionProposal, RulesetModule, WodResult, WodTest, propose,
)

ACTOR = InstanceRef('test', 'actor', 'mara')
PROFILES = [('d20', '5e-2024'), ('wod', 'v5'), ('pbta', 'dungeon-world-1e'), ('ars', '5e')]


def request(family, action):
    binding = ModuleBinding(*dict((f, (f, e)) for f, e in PROFILES)[family])
    module = RulesetModule(SourceRef('synthetic', 'ruleset', family, 'v1'), (binding,))
    return ActionRequest('attempt-1', ACTOR, module, binding, action)


def examples():
    return [
        (request('d20', D20Check('dexterity', 'stealth', 15, 3)), D20Result(12, 15, True)),
        (request('wod', WodTest('wits', 'awareness', 3, 1, 3)),
         WodResult((10, 6), (10,), 5, 'messy-critical')),
        (request('pbta', PbtaMove('Mara crosses a collapsing bridge', 'dexterity', 1)),
         PbtaResult((3, 4), 8, 'partial', 'Leave the pack or lose time taking a safer path')),
        (request('ars', ArsStudy('ignem', 13, 5, 8, 1220, 'spring')),
         ArsResult(2, 1220, 'spring')),
    ]


@pytest.mark.parametrize('req,result', examples())
def test_family_proof_and_repeat_are_noncanonical(req, result):
    proposal = propose(req.module, req, result)
    assert proposal == propose(req.module, req, result)
    assert proposal.request == req
    assert proposal.result == result


def test_no_mechanics_does_not_accept_a_fake_roll():
    assert propose(None, None, None) is None
    req, result = examples()[0]
    with pytest.raises(ValueError):
        propose(None, req, result)


@pytest.mark.parametrize('change', [
    {'ref': SourceRef('elsewhere', 'ruleset', 'd20', 'v1')},
    {'ref': SourceRef('synthetic', 'ruleset', 'd20', 'v2')},
])
def test_selection_must_match_pinned_identity(change):
    req, result = examples()[0]
    with pytest.raises(ValueError):
        propose(replace(req.module, **change), req, result)


def test_composite_is_one_explicit_module_not_a_list():
    req, result = examples()[0]
    other = examples()[2][0]
    composite = RulesetModule(SourceRef('synthetic', 'ruleset', 'hybrid', 'v1'),
                             (req.binding, other.binding), (req.module.ref, other.module.ref))
    combined_request = replace(req, module=composite)
    assert propose(composite, combined_request, result).request.module == composite
    with pytest.raises(ValueError):
        propose([req.module, other.module], req, result)
    with pytest.raises(ValueError):
        replace(composite, components=(req.module.ref.unpinned(), other.module.ref))
    with pytest.raises(ValueError):
        replace(composite, bindings=(req.binding, req.binding))


@pytest.mark.parametrize('req,result', examples())
def test_other_family_result_rejected_even_directly(req, result):
    wrong = examples()[1 if req.binding.family == 'd20' else 0][1]
    with pytest.raises(ValueError):
        ResolutionProposal(req, wrong)


def test_ability_check_has_no_natural_twenty_automatic_success():
    req = request('d20', D20Check('strength', 'athletics', 25, 0))
    assert propose(req.module, req, D20Result(20, 20, False)).result.success is False
    with pytest.raises(ValueError):
        propose(req.module, req, D20Result(20, 20, True))
    with pytest.raises(ValueError):
        propose(req.module, req, D20Result(20, 21, False))


def test_v5_hunger_failure_and_critical_arithmetic():
    req = request('wod', WodTest('wits', 'awareness', 2, 1, 3))
    assert propose(req.module, req, WodResult((6,), (1,), 1, 'bestial-failure'))
    with pytest.raises(ValueError):
        propose(req.module, req, WodResult((10,), (10,), 2, 'success'))
    with pytest.raises(ValueError):
        propose(req.module, req, WodResult((6, 6), (1,), 2, 'bestial-failure'))


def test_partial_move_keeps_choice_unresolved():
    req, result = examples()[2]
    assert propose(req.module, req, result).result.choice == result.choice
    with pytest.raises(ValueError):
        replace(result, choice='')
    with pytest.raises(ValueError):
        propose(req.module, req, replace(result, outcome='strong', choice=None))


def test_seasonal_study_caps_partial_progress_without_a_roll():
    req, result = examples()[3]
    assert propose(req.module, req, result).result.xp == 2
    with pytest.raises(ValueError):
        propose(req.module, req, replace(result, xp=8))
    with pytest.raises(ValueError):
        propose(req.module, req, replace(result, season='summer'))
    with pytest.raises(ValueError):
        replace(req.action, starting_xp=15)


@pytest.mark.parametrize('build', [
    lambda: D20Result(True, 1, True),
    lambda: D20Result(21, 21, True),
    lambda: WodResult([6], (), 1, 'success'),
    lambda: WodResult((True,), (), 1, 'success'),
    lambda: WodTest('wits', 'awareness', 2, 3, 1),
    lambda: PbtaMove(' ', 'dexterity', 1),
    lambda: PbtaResult((3, 7), 10, 'strong'),
    lambda: ArsStudy('ignem', -1, 5, 8, 1220, 'spring'),
    lambda: ModuleBinding('d20', '5e-2014'),
    lambda: RulesetModule(SourceRef('synthetic', 'ruleset', 'd20'), (ModuleBinding(*PROFILES[0]),)),
])
def test_malformed_inputs_rejected(build):
    with pytest.raises(ValueError):
        build()
