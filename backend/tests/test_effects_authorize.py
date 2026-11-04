"""Two-key authority of Sprint 021 (pure part): a PC acts only through its player's own contribution."""

from __future__ import annotations

import pytest

from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError
from grimoire.core.model import ControlAssignment
from grimoire.effects import EffectError, EffectProposal, Op, authorize

from effects_fixtures import ANA, BOB, C, CHAT, CONTROLS, GUILD, INES, KEY1, KEY2, TAM, WREN, contribution, give, moment


def reason(call) -> str:
    with pytest.raises(EffectError) as caught:
        call()
    return caught.value.reason


def test_pc_give_needs_and_names_its_contribution():
    result = authorize(give(), CONTROLS, [contribution()])
    assert result.domains == ("voluntary_action", "voluntary_action")
    assert dict(result.endorsers) == {"c7": ANA}
    assert [c.contribution_id for c in result.contributions] == ["c7"]
    assert [(c.actor_id, c.controller_id) for c in result.controls] == [("wren", ANA)]


def test_without_a_contribution_narration_cannot_make_the_pc_act():
    assert reason(lambda: authorize(give(), CONTROLS, [])) == "pc_authorship"
    assert reason(lambda: authorize(give(backed=None), CONTROLS, [contribution()])) == "pc_authorship"
    assert reason(lambda: authorize(give(backed="c9"), CONTROLS, [contribution()])) == "pc_authorship"


@pytest.mark.parametrize("bad", [
    contribution(turn="t6"), contribution(scene="s2"), contribution(actor_id="tam"), contribution(kind="speech"),
    contribution(kind="directorial_request"), contribution(by=BOB),
])
def test_a_mismatched_contribution_is_an_invalid_expression(bad):
    assert reason(lambda: authorize(give(), CONTROLS, [bad])) == "invalid_expression"


def test_duplicate_contribution_ids_are_refused():
    assert reason(lambda: authorize(give(), CONTROLS, [contribution(), contribution()])) == "invalid_expression"


def test_director_domain_ops_need_no_contribution_and_refuse_backing():
    snatch = give(KEY2, giver=WREN, to=TAM, agent=TAM, backed=None, sequence=8)
    assert authorize(snatch, CONTROLS).domains == ("director", "director")
    lost = EffectProposal(C, CHAT, moment(8), "The key is lost.", (Op("end", "carries", WREN, KEY1),), "director")
    assert authorize(lost, CONTROLS).domains == ("director",)
    unassigned = EffectProposal(C, CHAT, moment(8), "Ines joins.", (Op("begin", "member_of", INES, GUILD, INES),),
                                "narration")
    assert authorize(unassigned, CONTROLS).domains == ("director",)
    assert reason(lambda: authorize(give(KEY2, agent=TAM, backed="c7"), CONTROLS, [contribution()])) == \
        "unexpected_backing"


def test_control_change_moves_the_authorship_key():
    moved = (ControlAssignment(C, "wren", BOB),)
    assert reason(lambda: authorize(give(), moved, [contribution()])) == "invalid_expression"
    assert dict(authorize(give(), moved, [contribution(by=BOB)]).endorsers) == {"c7": BOB}
    to_director = (ControlAssignment(C, "wren", DIRECTOR),)
    assert reason(lambda: authorize(give(), to_director, [contribution()])) == "unexpected_backing"


def test_ambiguous_control_fails_closed():
    both = (ControlAssignment(C, "wren", ANA), ControlAssignment(C, "wren", BOB))
    with pytest.raises(AuthorityError):
        authorize(give(), both, [contribution()])


def test_inputs_are_typed():
    assert reason(lambda: authorize("proposal", CONTROLS)) == "invalid_field"
    assert reason(lambda: authorize(give(), [("c", "wren", ANA)], [contribution()])) == "invalid_field"
    assert reason(lambda: authorize(give(), CONTROLS, [{"contribution_id": "c7"}])) == "invalid_field"
