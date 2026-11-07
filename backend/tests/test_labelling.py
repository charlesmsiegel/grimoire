"""035 milestone 2's pure labelling pieces: the facts a labeller is offered, how its reply is decoded and
bounded, the runtime's handle rule, and the scene values that record a labelling. No model call."""

from __future__ import annotations

import json

import pytest

from generation_fixtures import Harness

from grimoire.adjudication import MAX_FACTORS, MAX_STAKES, Factor, Stake
from grimoire.context.model import Origin, PackedItem
from grimoire.core.identity import SourceRef
from grimoire.core.inference import InferenceStage
from grimoire.labelling import (
    MAX_FACTS, MAX_STAKE_CHARS, NOVEL, Fact, LabelRefused, Obstacle, facts, label_messages, parse_attempt, parse_labels,
)
from grimoire.providers import RouteChoice
from grimoire.runtime import LabelRuntime
from grimoire.runtime.label import MAX_REPLY_CHARS, decode_reply
from grimoire.scene import MAX_LABELS, CallRecord, Label, SceneRefused

OFFERED = (Fact("f1", "The rigging is slick with rain.", 3, "perception:p1"),
           Fact("f2", "Anya has climbed masts since she was small.", 0, "description:card:anya"))


ANYA = SourceRef("card:anya", "character", "anya")
LORE = SourceRef("card:mara", "lorebook", "mara-lore")


def item(kind: str, line: str, *, sequence=None, stance=None, audience=None, **origin) -> PackedItem:
    return PackedItem("lane", line, 1, Origin(kind, **origin), stance=stance, sequence=sequence, audience=audience)


# -- facts ----------------------------------------------------------------------------------------

def test_facts_are_the_packs_fiction_numbered_in_order_with_their_sources():
    pack = [item("identity", "You are Anya Rook.", source=ANYA),
            item("description", "A harbor pilot.", source=ANYA),
            item("perception", "The rigging is slick.", sequence=4, record="p1"),
            item("perception", "Unrecorded.", sequence=4),  # no record to attribute it to
            item("proposition", "The mast was cut.", sequence=5, stance="suspected", record="q1"),
            item("proposition", "The crew is loyal.", sequence=2, stance="believed", record="q2"),
            item("lore", "The lighthouse is dark.", source=LORE, name="lighthouse", audience="common"),
            item("lore", "The keeper drowned it.", source=LORE, name="keeper", audience="director"),
            item("section", "   ", source=ANYA)]  # nothing to cite
    assert facts(pack) == (Fact("f1", "A harbor pilot.", 0, f"description:{ANYA}"),
                           Fact("f2", "The rigging is slick.", 4, "perception:p1"),
                           Fact("f3", "The crew is loyal.", 2, "proposition:q2"),
                           Fact("f4", "The lighthouse is dark.", 0, f"lore:{LORE}#lighthouse"))


def test_at_most_max_facts_are_offered():
    pack = [item("description", f"Fact {n}.", source=ANYA) for n in range(MAX_FACTS + 5)]
    assert len(facts(pack)) == MAX_FACTS


def test_the_prompt_lists_the_facts_and_marks_the_scene_as_context_only():
    system, user = label_messages("Anya Rook", "I climb the rigging", OFFERED, "Mara: Careful.")
    assert system[0] == "system" and "never decide" in system[1]
    assert "f1: The rigging is slick with rain." in user[1] and "not citable" in user[1]
    assert "(no facts)" in label_messages("Anya Rook", "I wave", ())[1][1]


# -- the reply ------------------------------------------------------------------------------------

def test_a_reply_cites_facts_whose_text_and_source_are_the_facts_own():
    factors, stakes = parse_labels({"factors": [{"fact": "f2", "bearing": "favours"},
                                                {"fact": "f1", "bearing": "hinders"}],
                                    "stakes": ["  She falls to the deck  "]}, OFFERED)
    assert factors == (Factor("f1", "hinders", "The rigging is slick with rain.", 3, "perception:p1"),
                       Factor("f2", "favours", "Anya has climbed masts since she was small.", 0,
                              "description:card:anya"))
    assert stakes == (Stake("s1", "She falls to the deck"),)
    assert parse_labels({}, OFFERED) == ((), ())


@pytest.mark.parametrize(("decoded", "reason"), [
    ([], "unparseable"),
    ({"factors": [], "result": "success"}, "unparseable"),
    ({"factors": {}}, "unparseable"),
    ({"factors": [{"fact": "f1"}]}, "unparseable"),
    ({"factors": [{"fact": "f1", "bearing": "hinders", "text": "The mast is rotten"}]}, "unparseable"),
    ({"factors": [{"fact": "f9", "bearing": "hinders"}]}, "unknown_fact"),
    ({"factors": [{"fact": 1, "bearing": "hinders"}]}, "unknown_fact"),
    ({"factors": [{"fact": "f1", "bearing": "dooms"}]}, "invalid_bearing"),
    ({"factors": [{"fact": "f1", "bearing": "hinders"}, {"fact": "f1", "bearing": "favours"}]}, "repeated_fact"),
    ({"factors": [{"fact": "f1", "bearing": "hinders"}] * (MAX_FACTORS + 1)}, "too_many_factors"),
    ({"stakes": ["x"] * (MAX_STAKES + 1)}, "too_many_stakes"),
    ({"stakes": ["   "]}, "invalid_stake"),
    ({"stakes": [7]}, "invalid_stake"),
    ({"stakes": ["x" * (MAX_STAKE_CHARS + 1)]}, "invalid_stake"),
])
def test_anything_else_in_a_reply_is_refused_whole(decoded, reason):
    with pytest.raises(LabelRefused) as error:
        parse_labels(decoded, OFFERED)
    assert error.value.reason == reason


def test_against_an_obstacle_the_reply_chooses_an_approach_and_a_suggestion_lists_no_stakes():
    hold = Obstacle("Get into the hold.", (("bribe", "Pay the keeper."), ("sneak", "Slip past.")))
    system, user = label_messages("Anya Rook", "I pay her", OFFERED, obstacle=hold)
    assert '"novel"' in system[1] and "never refuse an approach for being unlisted" in system[1]
    assert "Obstacle: Get into the hold.\nSuggested approaches:\nbribe: Pay the keeper.\nsneak: Slip past." in user[1]
    assert "Obstacle:" not in label_messages("Anya Rook", "I pay her", OFFERED)[1][1]
    cited = {"fact": "f2", "bearing": "favours"}
    assert parse_attempt({"approach": "bribe", "factors": [cited]}, OFFERED, ("bribe", "sneak"))[:2] == (
        "bribe", (Factor("f2", "favours", OFFERED[1].text, 0, OFFERED[1].source),))
    assert parse_attempt({"approach": NOVEL, "stakes": ["She is seen"]}, OFFERED, ("bribe",)) == (
        NOVEL, (), (Stake("s1", "She is seen"),))
    for decoded, reason in (({"approach": "bribe", "stakes": ["x"]}, "suggested_stakes"),
                            ({"approach": "climb"}, "unknown_approach"), ({"factors": []}, "unknown_approach"),
                            ({"approach": ["bribe"]}, "unknown_approach"),
                            ({"approach": "bribe", "result": "success"}, "unparseable"),
                            ({"approach": "bribe", "factors": [{"fact": "f9", "bearing": "favours"}]},
                             "unknown_fact")):
        with pytest.raises(LabelRefused) as error:
            parse_attempt(decoded, OFFERED, ("bribe", "sneak"))
        assert error.value.reason == reason, decoded
    with pytest.raises(LabelRefused) as error:  # with no obstacle, an approach is a field no reply may carry
        parse_labels({"approach": "bribe"}, OFFERED)
    assert error.value.reason == "unparseable"


@pytest.mark.parametrize(("text", "value"), [
    ('{"stakes": []}', {"stakes": []}),
    ('  ```json\n{"stakes": []}\n```  ', {"stakes": []}),
    ('```\n[1]\n```', [1]),
])
def test_a_reply_decodes_as_one_json_value_or_one_fenced_block(text, value):
    assert decode_reply(text) == value


@pytest.mark.parametrize("text", [
    "Here you go: {\"stakes\": []}",
    '{"stakes": [], "stakes": ["x"]}',  # a duplicate key could hide what a reader sees
    '{"n": NaN}',
    '```json\n{}\n```\n```json\n{}\n```',
    "{" * 5000 + "}" * 5000,
    " " * (MAX_REPLY_CHARS + 1),
])
def test_anything_but_one_json_value_is_not_decoded(text):
    with pytest.raises(ValueError):
        decode_reply(text)


# -- the runtime ----------------------------------------------------------------------------------

def test_the_labeller_refuses_a_trusted_handle_and_an_unapproved_one():
    trusted, untrusted = Harness([]), Harness([], approve=True)
    routes = (RouteChoice("primary", "model-a"),)
    with pytest.raises(ValueError, match="untrusted"):
        LabelRuntime(trusted.adapter, routes, trusted.handle, approvals=untrusted.approvals)
    with pytest.raises(ValueError, match="approval"):
        LabelRuntime(untrusted.adapter, routes, untrusted.handle, approvals=None)
    with pytest.raises(ValueError, match="routes"):
        LabelRuntime(untrusted.adapter, (), untrusted.handle, approvals=untrusted.approvals)


# -- the scene values -----------------------------------------------------------------------------

def _call(status="accepted") -> CallRecord:
    return CallRecord("main", InferenceStage.LABELLING, None, 1, status)


def test_a_labelling_records_only_what_was_established_before_its_action():
    factor = Factor("f1", "hinders", "The rigging is slick with rain.", 3, "perception:p1")
    assert Label(1, "proposed", 4, factors=(factor,), calls=(_call(),)).factors == (factor,)
    for bad in (dict(label=0), dict(label=MAX_LABELS + 1), dict(state="decided"), dict(sequence=0),
                dict(sequence=3, factors=(factor,)), dict(state="running", factors=(factor,)),
                dict(state="refused", stakes=(Stake("s1", "x"),)),
                dict(calls=(CallRecord("main", InferenceStage.DIRECTOR_GENERATION, None, 1, "accepted"),))):
        fields = dict(label=1, state="proposed", sequence=4) | bad
        with pytest.raises(SceneRefused):
            Label(**fields)


def test_a_label_reason_is_bounded_text():
    with pytest.raises(SceneRefused):
        Label(1, "refused", 1, reason="x" * 201)
    assert json.dumps(Label(1, "refused", 1, reason="unknown_fact").reason) == '"unknown_fact"'


def test_a_reply_that_029_could_not_record_is_refused_not_raised():
    with pytest.raises(LabelRefused) as error:
        parse_labels({"stakes": ["\ud800"]}, OFFERED)  # a lone surrogate could never be stored
    assert error.value.reason == "invalid_stake"
    long = Fact("f1", "A fact.", 0, "x" * 600)  # facts() never offers it; parse refuses it anyway
    with pytest.raises(LabelRefused):
        parse_labels({"factors": [{"fact": "f1", "bearing": "hinders"}]}, (long,))


def test_a_fact_whose_source_029_could_not_record_is_never_offered():
    long = SourceRef("card:" + "a" * 120, "lorebook", "b" * 128, "r" * 128)
    assert facts([item("lore", "A fact.", source=long, name="c" * 128, audience="common")]) == ()


def test_only_the_controllers_own_beliefs_are_offered():
    pack = [item("proposition", "The crew is loyal.", sequence=2, stance="believed", record="q2"),
            item("description", "A harbor pilot.", source=ANYA)]
    assert [f.text for f in facts(pack, own=frozenset())] == ["A harbor pilot."]
    assert [f.text for f in facts(pack, own=frozenset({"q2"}))] == ["The crew is loyal.", "A harbor pilot."]


def test_a_belief_the_player_never_authored_is_inherited_not_theirs():
    from knowledge_fixtures import C, TAVI, betrayal_knowledge, believes, betrayal_records

    from grimoire.core import ControlAssignment, Knowledge
    from grimoire.play.labels import own_propositions

    controls = (ControlAssignment(C, "tavi", "human:ana"),)
    assert own_propositions(betrayal_knowledge(), "human:ana", controls, TAVI, 4) == frozenset()  # the director's
    records = betrayal_records()
    beliefs = [*records["beliefs"][:2], believes("b-tavi-3", TAVI, "duke-betrayed", "believed", 4, ["r-ines-tavi"],
                                                 authored_by="human:ana")]
    authored = Knowledge(C, **(records | {"beliefs": beliefs}))
    assert own_propositions(authored, "human:ana", controls, TAVI, 4) == frozenset({"duke-betrayed"})
    assert own_propositions(betrayal_knowledge(), "human:bob", controls, TAVI, 4) == frozenset()  # fails closed
