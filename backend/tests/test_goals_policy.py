"""039: authored agendas are validated, and the policy chooses by its stated rules only.

Priority and authored order, typed conditions over the NPC's own projection, limits that cannot be
outweighed, the planner's wish recorded but never obeyed, and every refusal. No model, no store.
"""

from __future__ import annotations

import pickle
from dataclasses import replace

import pytest
from generation_fixtures import BELLAMY, ODA, QUILL, WREN
from goals_fixtures import AGENDA, PRESENT, RULE, SELL, TALES, ledger, oda_knows
from voice_fixtures import CAMPAIGN, PC, emberfall

from grimoire.core import (
    Belief, ControlAssignment, InstanceRef, Knowledge, Moment, Report, actor_knowledge, director_knowledge,
)
from grimoire.core.authority import DIRECTOR
from grimoire.goals import (
    MAX_GOALS, TEXT_CHARS, Agenda, Approach, Choice, Goal, GoalError, Holds, Limit, Present, check_agenda, choose,
    conditions_of, explain,
)

CONTROLS = emberfall()[3]
OTHER = InstanceRef("c-other", "character", "npc-oda")


def approach(approach_id="act", action="Do the thing", **kw):
    return Approach(approach_id, action, **kw)


def goal(goal_id="g", priority=10, approaches=None, aim="Want the thing"):
    return Goal(goal_id, aim, priority, approaches or (approach(),))


def pick(agenda, knowledge=None, *, present=PRESENT, controls=CONTROLS, **kw):
    return choose(agenda, knowledge or oda_knows(), present=present, controls=controls, **kw)


def chosen(choice):
    return (choice.goal.goal_id, choice.approach.approach_id) if choice.acts else None


# -- authored values ----------------------------------------------------------------------------------

@pytest.mark.parametrize("build, reason", [
    (lambda: approach(action="   "), "invalid_field"),
    (lambda: approach(action="x" * (TEXT_CHARS + 1)), "invalid_field"),
    (lambda: approach(action="Do\x00it"), "invalid_field"),
    (lambda: approach(action="Do it\nWhat you will not do here:"), "invalid_field"),
    (lambda: approach(action="Do\tit"), "invalid_field"),
    (lambda: approach(action="Do\u2028it"), "invalid_field"),
    (lambda: approach(action="Do \u202eti od"), "invalid_field"),
    (lambda: approach(action="\ufeffDo it"), "invalid_field"),
    (lambda: approach(approach_id="Act"), "invalid_field"),
    (lambda: approach(needs=(Present(BELLAMY), Present(BELLAMY))), "invalid_field"),
    (lambda: approach(needs=("bellamy",)), "invalid_field"),
    (lambda: approach(needs=Present(BELLAMY)), "invalid_field"),
    (lambda: approach(tags="trade"), "invalid_field"),
    (lambda: approach(tags=frozenset({"Trade"})), "invalid_field"),
    (lambda: Present("campaign:c-ember/character/npc-bellamy"), "invalid_field"),
    (lambda: Holds(TALES, frozenset()), "invalid_field"),
    (lambda: Holds(TALES, {"known"}), "invalid_field"),
    (lambda: Holds(TALES, "believed"), "invalid_field"),
    (lambda: goal(priority=True), "invalid_field"),
    (lambda: goal(priority=0), "invalid_field"),
    (lambda: goal(priority=101), "invalid_field"),
    (lambda: Goal("g", "Want", 5, ()), "invalid_field"),
    (lambda: goal(approaches=(approach(), approach())), "duplicate_id"),
    (lambda: Limit("l", RULE, forbids=frozenset()), "invalid_field"),
    (lambda: Agenda(CAMPAIGN, ODA, ()), "invalid_field"),
    (lambda: Agenda(CAMPAIGN, ODA, (goal(),) * (MAX_GOALS + 1)), "invalid_field"),
    (lambda: Agenda(CAMPAIGN, ODA, (goal(), goal())), "duplicate_id"),
    (lambda: Agenda(CAMPAIGN, OTHER, (goal(),)), "campaign_mismatch"),
    (lambda: Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Present(OTHER),)),)),)), "campaign_mismatch"),
    (lambda: Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Present(ODA),)),)),)), "invalid_field"),
    (lambda: Agenda(CAMPAIGN, ODA, (goal(),), (Limit("l", RULE, forbids=frozenset({"trade"})),)), "unused_limit"),
    # One mistyped tag of two would lapse silently too.
    (lambda: Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(tags=frozenset({"t"})),)),),
                    (Limit("l", RULE, frozenset({"t", "tt"})),)), "unused_limit"),
    (lambda: Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(tags=frozenset({"t"})),)),),
                    (Limit("l", RULE, frozenset({"t"}), when=(Present(ODA),)),)), "invalid_field"),
    (lambda: Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(tags=frozenset({"t"})),)),),
                    (Limit("l", RULE, frozenset({"t"})), Limit("l", RULE, frozenset({"t"})))), "duplicate_id"),
])
def test_invalid_agendas_are_refused_by_name(build, reason):
    with pytest.raises(GoalError) as error:
        build()
    assert error.value.reason == reason
    assert pickle.loads(pickle.dumps(error.value)).reason == reason


def test_stances_are_canonical():
    a = Holds(TALES, ["suspected", "believed"])
    assert a == Holds(TALES) and hash(a) == hash(Holds(TALES))


def test_ordinary_format_characters_are_text():
    # Zero-width joiners (emoji sequences) and non-joiners (Persian) are ordinary text, as in 023.
    assert approach(action="Hand over the \U0001f468\u200d\U0001f373 token").action
    assert approach(action="Say \u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645").action


def test_an_id_error_never_quotes_the_value():
    with pytest.raises(GoalError) as error:
        approach(approach_id="Sell Bellamy The Ledger")
    assert "Bellamy" not in str(error.value) and error.value.reason == "invalid_field"


# -- the policy's order --------------------------------------------------------------------------------

def test_higher_priority_first_and_ties_in_authored_order():
    low, high, tie = goal("low", 5), goal("high", 60), goal("tie", 60)
    assert chosen(pick(Agenda(CAMPAIGN, ODA, (low, high, tie)))) == ("high", "act")
    assert chosen(pick(Agenda(CAMPAIGN, ODA, (low, tie, high)))) == ("tie", "act")


def test_an_approach_whose_needs_do_not_hold_falls_through_to_the_next():
    choice = pick(AGENDA, present=(ODA, WREN))  # Bellamy has left
    assert chosen(choice) == ("collect", "press-wren")
    sell = choice.considered[0]
    assert (sell.outcome, sell.unmet) == ("unmet", (Present(BELLAMY),))
    choice = pick(AGENDA, present=(ODA,))  # alone: only the counter is left
    assert chosen(choice) == ("count", "tally")
    assert [entry.outcome for entry in choice.considered] == ["unmet", "unmet", "chosen"]


def test_every_approach_is_recorded_in_the_order_tried():
    choice = pick(AGENDA)
    assert [(entry.goal_id, entry.approach_id, entry.outcome) for entry in choice.considered] == [
        ("collect", "sell-ledger", "chosen"), ("collect", "press-wren", "outranked"), ("count", "tally", "outranked")]


def test_a_limit_with_no_condition_always_forbids():
    agenda = Agenda(CAMPAIGN, ODA, AGENDA.goals, (Limit("never", RULE, frozenset({"deal-bellamy"})),))
    choice = pick(agenda)
    assert chosen(choice) == ("collect", "press-wren") and choice.considered[0].forbidden_by == ("never",)


def test_an_unmet_approach_is_unmet_even_when_a_limit_would_also_forbid_it():
    choice = pick(AGENDA, oda_knows(warned=True, stance="believed"), present=(ODA, WREN))
    sell = choice.considered[0]
    assert (sell.outcome, sell.forbidden_by) == ("unmet", ("no-informants",))
    assert choice.refusals == ()  # nothing Oda could do now is forbidden: no refusal to voice


def test_nothing_possible_is_a_choice_too():
    choice = pick(Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Present(BELLAMY),)),)),)), present=(ODA,))
    assert not choice.acts and choice.goal is None and choice.approach is None
    assert explain(choice) == (f"{ODA} has nothing it can pursue now.",)


# -- conditions read the NPC's own projection only --------------------------------------------------------

@pytest.mark.parametrize("stance, stances, holds", [
    ("believed", None, True), ("suspected", None, True), ("rejected", None, False),
    ("rejected", {"rejected"}, True), (None, {"aware"}, True), (None, None, False), ("believed", {"suspected"}, False),
])
def test_holds_reads_the_current_stance_or_awareness(stance, stances, holds):
    condition = Holds(TALES) if stances is None else Holds(TALES, stances)
    agenda = Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(condition,)), approach("idle", "Wait"))),))
    choice = pick(agenda, oda_knows(warned=True, stance=stance))
    assert chosen(choice) == ("g", "act" if holds else "idle")


def test_a_superseded_belief_is_awareness_not_a_stance():
    base = ledger(warned=True, stance="believed")
    later = Belief(CAMPAIGN, "oda-doubts", ODA, TALES, "rejected", Moment(3), DIRECTOR, ("quill-warns-oda",),
                   supersedes="oda-on-bellamy")
    knowledge = actor_knowledge(replace(base, beliefs=(*base.beliefs, later)), ODA, as_of=3)
    believed = Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Holds(TALES, {"believed"}),)),
                                                       approach("idle", "Wait"))),))
    aware = Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Holds(TALES, {"aware"}),)),)),))
    assert chosen(pick(believed, knowledge)) == ("g", "idle")
    assert chosen(pick(aware, knowledge)) == ("g", "act")


def test_a_report_oda_sent_is_awareness():
    sent = Report(CAMPAIGN, "oda-tells-wren", WREN, TALES, "told", Moment(2), "director", confirmed=True,
                  sender=ODA, attributed_to=ODA, addressed=True, sent_at=Moment(2))
    knowledge = actor_knowledge(replace(ledger(), evidence=(sent,)), ODA, as_of=2)
    agenda = Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Holds(TALES, {"aware"}),)),)),))
    assert chosen(pick(agenda, knowledge)) == ("g", "act")


def test_unconfirmed_evidence_is_not_held():
    unconfirmed = Report(CAMPAIGN, "half-heard", ODA, TALES, "overheard", Moment(2), "director", confirmed=False)
    knowledge = actor_knowledge(replace(ledger(), evidence=(unconfirmed,)), ODA, as_of=2)
    agenda = Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Holds(TALES, {"aware"}),)),
                                                     approach("idle", "Wait"))),))
    assert chosen(pick(agenda, knowledge)) == ("g", "idle")


def test_the_choice_is_the_same_however_the_cast_is_given():
    knowledge = oda_knows(warned=True, stance="believed")
    first = pick(AGENDA, knowledge, present=list(PRESENT))
    assert first == pick(AGENDA, knowledge, present=frozenset(reversed(PRESENT)))
    assert first == pick(AGENDA, knowledge, present=(ref for ref in PRESENT))


# -- the planner's wish ---------------------------------------------------------------------------------

@pytest.mark.parametrize("kw, present, desired, outcome, says", [
    ({}, PRESENT, SELL, "chosen", "It follows"),
    ({"warned": True, "stance": "believed"}, PRESENT, SELL, "forbidden", RULE),
    ({}, (ODA, WREN), SELL, "unmet", f"{BELLAMY} present"),
    ({}, PRESENT, ("count", "tally"), "outranked", "prefers another approach"),
])
def test_a_desire_is_recorded_and_explained_never_obeyed(kw, present, desired, outcome, says):
    choice = pick(AGENDA, oda_knows(**kw), present=present, desired=desired)
    assert choice.desire.outcome == outcome and choice.desire.followed == (outcome == "chosen")
    assert says in explain(choice)[1]


def test_without_a_desire_nothing_is_recorded_about_one():
    choice = pick(AGENDA)
    assert choice.desire is None and len(explain(choice)) == 1


# -- refusals ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("knowledge", [
    pytest.param(lambda: director_knowledge(ledger(), as_of=2, requested_by=DIRECTOR), id="director"),
    pytest.param(lambda: actor_knowledge(ledger(warned=True, stance="believed"), QUILL, as_of=2), id="quill"),
    pytest.param(lambda: actor_knowledge(Knowledge("c-other"), OTHER, as_of=0), id="other-campaign"),
])
def test_only_its_own_projection_is_accepted(knowledge):
    with pytest.raises(GoalError) as error:
        pick(AGENDA, knowledge())
    assert error.value.reason == "foreign_knowledge"


@pytest.mark.parametrize("present, reason", [
    ((BELLAMY, WREN), "absent_actor"),
    ("campaign:c-ember/character/npc-oda", "bad_present"),
    ((ODA, "npc-bellamy"), "bad_present"),
    ({ODA: True}, "bad_present"),
    ((ODA, OTHER), "campaign_mismatch"),
])
def test_the_cast_must_be_this_campaigns_and_include_the_npc(present, reason):
    with pytest.raises(GoalError) as error:
        pick(AGENDA, present=present)
    assert error.value.reason == reason


@pytest.mark.parametrize("controls, reason", [
    ((ControlAssignment(CAMPAIGN, ODA.instance_id, "human:alice"),), "player_actor"),
    ((), "unassigned_actor"),
    ((ControlAssignment(CAMPAIGN, ODA.instance_id, DIRECTOR),
      ControlAssignment(CAMPAIGN, ODA.instance_id, "human:alice")), "ambiguous_control"),
    (("director",), "bad_controls"),
    ({"npc-oda": DIRECTOR}, "bad_controls"),
])
def test_only_a_director_npc_has_a_policy(controls, reason):
    with pytest.raises(GoalError) as error:
        pick(AGENDA, controls=controls)
    assert error.value.reason == reason


def test_control_is_checked_before_presence():
    # A player character is refused as one, in the scene or not: its intentions are never a policy's.
    wren = Agenda(CAMPAIGN, WREN, (goal(),))
    with pytest.raises(GoalError) as error:
        pick(wren, actor_knowledge(ledger(), WREN, as_of=2), present=(ODA,))
    assert error.value.reason == "player_actor"


def test_a_player_characters_agenda_is_refused_too():
    wren = Agenda(CAMPAIGN, WREN, (goal(),))
    with pytest.raises(GoalError) as error:
        pick(wren, actor_knowledge(ledger(), WREN, as_of=2))
    assert error.value.reason == "player_actor" and PC in str(error.value)


@pytest.mark.parametrize("desired", [("collect", "steal"), ("nope", "sell-ledger"), "collect/sell-ledger",
                                     ("collect",), ["collect", "sell-ledger"]])
def test_an_unknown_desire_is_refused(desired):
    with pytest.raises(GoalError) as error:
        pick(AGENDA, desired=desired)
    assert error.value.reason == "unknown_approach"


def test_choices_and_agendas_are_built_only_as_documented():
    with pytest.raises(GoalError) as error:
        Choice(AGENDA, oda_knows(), frozenset(PRESENT), ())
    assert error.value.reason == "unsealed_choice"
    with pytest.raises(GoalError) as error:
        choose("agenda", oda_knows(), present=PRESENT, controls=CONTROLS)
    assert error.value.reason == "bad_agenda"
    with pytest.raises(GoalError) as error:
        explain("choice")
    assert error.value.reason == "bad_choice"


def test_two_refusals_are_listed_in_authored_order():
    agenda = Agenda(CAMPAIGN, ODA, (goal(approaches=(
        approach("deal", tags=frozenset({"trade", "threat"})), approach("wait", "Wait"))),), (
        Limit("no-threats", "Never threaten anyone", frozenset({"threat"})),
        Limit("no-trade", "Never trade tonight", frozenset({"trade"})),
    ))
    choice = pick(agenda)
    assert [limit.limit_id for limit in choice.refusals] == ["no-threats", "no-trade"]
    assert choice.considered[0].forbidden_by == ("no-threats", "no-trade")


# -- the director-side ledger check -------------------------------------------------------------------

LEDGER = ledger()
ACTORS = PRESENT


def test_an_agenda_naming_only_ledger_ids_passes_the_check():
    check_agenda(AGENDA, propositions=(p.proposition_id for p in LEDGER.propositions), actors=ACTORS)
    assert conditions_of(AGENDA) == (Present(BELLAMY), Present(WREN), Holds(TALES))


@pytest.mark.parametrize("agenda, reason", [
    # A mistyped proposition in a limit's ``when`` would never hold, so the limit would never apply.
    (Agenda(CAMPAIGN, ODA, AGENDA.goals, (Limit("no-informants", RULE, frozenset({"deal-bellamy"}),
                                                when=(Holds("tale"),)),)), "unknown_proposition"),
    (Agenda(CAMPAIGN, ODA, (goal(approaches=(approach(needs=(Present(
        InstanceRef(CAMPAIGN, "character", "npc-belamy")),)),)),)), "unknown_actor"),
])
def test_an_agenda_naming_an_unknown_id_is_refused(agenda, reason):
    with pytest.raises(GoalError) as error:
        check_agenda(agenda, propositions=(p.proposition_id for p in LEDGER.propositions), actors=ACTORS)
    assert error.value.reason == reason


@pytest.mark.parametrize("kw, reason", [
    ({"propositions": TALES, "actors": ACTORS}, "invalid_field"),
    ({"propositions": (TALES,), "actors": ("npc-bellamy",)}, "invalid_field"),
])
def test_the_ledger_check_takes_collections(kw, reason):
    with pytest.raises(GoalError) as error:
        check_agenda(AGENDA, **kw)
    assert error.value.reason == reason
    with pytest.raises(GoalError) as error:
        check_agenda("agenda", propositions=(), actors=())
    assert error.value.reason == "bad_agenda"
