"""039 acceptance: one NPC pursues a credible goal inside its one usual generation call.

Oda (a director NPC) chooses what to pursue from its own agenda and its own knowledge, with no model
call; the choice reaches Oda's single ``npc_generation`` call through its brief. The proving scenario
is the sprint's: a merchant refuses a profitable but, as it believes, dangerous trade, although a
narrative planner wishes for the confrontation. Every provider call is a scripted fake.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from generation_fixtures import BELLAMY, ODA, QUILL, WREN, Harness, say
from goals_fixtures import (
    AGENDA, COLLECT_AIM, PRESENT, PRESS_ACTION, RULE, SELL, SELL_ACTION, TALES, TALES_TEXT, ledger, oda_knows,
)
from voice_fixtures import CAMPAIGN, SECRETS, emberfall

from grimoire.core import Knowledge, Moment, Proposition, Report, Ruling, actor_knowledge
from grimoire.core.inference import InferenceStage
from grimoire.core.model import ActorView
from grimoire.generation import GenerationError, npc_brief
from grimoire.core.identity import InstanceRef
from grimoire.goals import Agenda, Approach, Goal, Holds, Limit, Present, choose, explain
from grimoire.voice import SecretFact

CONTROLS = emberfall()[3]
VOICES = emberfall()[4]


def oda_view(knowledge) -> ActorView:
    return ActorView(CAMPAIGN, ODA.instance_id, "Oda", "The guild's counting-house keeper.", knowledge=knowledge)


def oda_turn(knowledge, reply: str, *, agenda=AGENDA, desired=SELL):
    """Oda's choice, then the turn: Oda speaks because it has something to pursue, nobody addressed it."""
    choice = choose(agenda, knowledge, present=PRESENT, controls=CONTROLS, desired=desired)
    assert choice.acts
    harness = Harness([say(reply)])
    plan = harness.plan(CONTROLS, present=tuple(ref.instance_id for ref in PRESENT), speakers=(ODA.instance_id,))
    meter = harness.meter(plan)
    brief = npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)
    outcome = harness.runtime.run_turn(meter, [brief], controls=CONTROLS)
    return choice, outcome, meter, harness.prompts()


# -- A. the proof: a credible goal, no extra generation ------------------------------------------

def test_a_oda_refuses_the_dangerous_trade_and_pursues_the_goal_in_its_one_call():
    knowledge = oda_knows(warned=True, stance="believed")
    choice, outcome, meter, prompts = oda_turn(knowledge, "Ledger's not for sale. Wren. Your tally. Now.")

    assert (choice.goal.goal_id, choice.approach.approach_id) == ("collect", "press-wren")
    assert not choice.desire.followed and choice.desire.outcome == "forbidden"
    assert [limit.limit_id for limit in choice.refusals] == ["no-informants"]
    assert any("refuses the desired collect/sell-ledger" in line and RULE in line for line in explain(choice))

    assert outcome.status == "complete" and [line.actor for line in outcome.lines] == [ODA]
    (prompt,) = prompts
    assert COLLECT_AIM in prompt and PRESS_ACTION in prompt and RULE in prompt
    assert SELL_ACTION not in prompt  # the refused approach is never suggested to the model

    # No extra default generation: the one baseline call, nothing optional, no planning or decision.
    summary = meter.summary()
    assert summary.baseline.attempts == 1 and summary.optional.attempts == 0
    assert set(summary.by_stage) == {InferenceStage.NPC_GENERATION}
    assert set(summary.by_actor) == {ODA.instance_id}


def test_a_only_an_npc_with_something_to_pursue_takes_the_turn():
    # Initiative comes from the choices themselves: Quill has an agenda too, but believes nothing that
    # would let it act, so the scene rule (035's) gives the turn to Oda alone and Quill costs nothing.
    quill_agenda = Agenda(CAMPAIGN, QUILL, (Goal("warn", "Keep the guild clear of informants", 40, (
        Approach("accuse", "Name the informant to the guild master", needs=(Holds(TALES),)),)),))
    knowledge = ledger(warned=True, stance="believed")
    oda = choose(AGENDA, actor_knowledge(knowledge, ODA, as_of=2), present=PRESENT, controls=CONTROLS)
    quill = choose(quill_agenda, actor_knowledge(knowledge, QUILL, as_of=2), present=PRESENT, controls=CONTROLS)
    speakers = tuple(choice.actor.instance_id for choice in (oda, quill) if choice.acts)
    assert speakers == (ODA.instance_id,)

    harness = Harness([say("Wren. Your tally. Now.")])
    plan = harness.plan(CONTROLS, present=tuple(ref.instance_id for ref in PRESENT), speakers=speakers)
    meter = harness.meter(plan)
    brief = npc_brief(oda_view(oda.knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=oda)
    outcome = harness.runtime.run_turn(meter, [brief], controls=CONTROLS)
    assert outcome.status == "complete" and [line.actor for line in outcome.lines] == [ODA]
    assert meter.summary().baseline.attempts == 1 and set(meter.summary().by_actor) == {ODA.instance_id}


# -- B. no omniscience: only what Oda holds moves it -------------------------------------------------

def test_b_a_truth_oda_never_heard_does_not_stop_the_trade():
    # Canon: Bellamy really is an informant. Oda was never told, so it takes Bellamy's money.
    knowledge = oda_knows(true=True)
    choice, outcome, _meter, (prompt,) = oda_turn(knowledge, "Coin on the counter. Ledger's yours.")
    assert choice.approach.approach_id == "sell-ledger" and choice.desire.followed
    assert choice.refusals == ()
    assert SELL_ACTION in prompt and RULE not in prompt and TALES_TEXT not in prompt
    assert outcome.status == "complete"


def test_b_a_refuted_rumour_oda_believes_still_stops_it():
    refuted = Ruling(CAMPAIGN, "tales-false", TALES, "refuted", Moment(1), "director")
    base = ledger(warned=True, stance="believed")
    knowledge = actor_knowledge(replace(base, rulings=(refuted,)), ODA, as_of=2)
    choice = choose(AGENDA, knowledge, present=PRESENT, controls=CONTROLS, desired=SELL)
    assert choice.approach.approach_id == "press-wren" and choice.desire.outcome == "forbidden"


def test_b_hearing_a_rumour_oda_doubts_does_not_stop_it():
    for kw in ({"warned": True}, {"warned": True, "stance": "rejected"}):
        choice = choose(AGENDA, oda_knows(**kw), present=PRESENT, controls=CONTROLS)
        assert choice.approach.approach_id == "sell-ledger", kw


def test_b_the_choice_is_as_of_its_moment():
    warned = ledger(warned=True, stance="believed")
    before = choose(AGENDA, actor_knowledge(warned, ODA, as_of=1), present=PRESENT, controls=CONTROLS)
    after = choose(AGENDA, actor_knowledge(warned, ODA, as_of=2), present=PRESENT, controls=CONTROLS)
    assert before.approach.approach_id == "sell-ledger" and after.approach.approach_id == "press-wren"


# -- C. the brief's checks ---------------------------------------------------------------------------

def test_c_the_brief_refuses_an_intention_that_is_not_this_actors_or_this_projection():
    knowledge = oda_knows(warned=True, stance="believed")
    voice = VOICES[ODA.instance_id]
    with pytest.raises(GenerationError) as error:
        npc_brief(oda_view(knowledge), voice, secrets=SECRETS, intention="press Wren")
    assert error.value.reason == "bad_intention"

    bellamy_agenda = Agenda(CAMPAIGN, BELLAMY, (Goal("gossip", "Hear every whisper tonight", 5,
                                                     (Approach("listen", "Listen at the duke's table"),)),))
    bellamy = choose(bellamy_agenda, actor_knowledge(Knowledge(CAMPAIGN), BELLAMY, as_of=2), present=PRESENT,
                     controls=CONTROLS)
    with pytest.raises(GenerationError) as error:
        npc_brief(oda_view(knowledge), voice, secrets=SECRETS, intention=bellamy)
    assert error.value.reason == "foreign_intention"

    earlier = choose(AGENDA, oda_knows(), present=PRESENT, controls=CONTROLS)
    with pytest.raises(GenerationError) as error:  # chosen before Oda was warned: it would sell
        npc_brief(oda_view(knowledge), voice, secrets=SECRETS, intention=earlier)
    assert error.value.reason == "intention_mismatch"


def test_c_an_intention_stating_a_secret_the_npc_never_learned_is_refused():
    leaky = Agenda(CAMPAIGN, ODA, (Goal("expose", "Ruin the duke", 5, (
        Approach("tell", "Tell Wren the duke sold the guild"),)),))
    knowledge = oda_knows()
    choice = choose(leaky, knowledge, present=PRESENT, controls=CONTROLS)
    with pytest.raises(GenerationError) as error:
        npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)
    assert error.value.reason == "intention_leak" and "duke-betrayal" in str(error.value)


def test_c_an_approach_gated_on_the_secret_is_shown_once_the_npc_holds_it():
    gated = Agenda(CAMPAIGN, ODA, (Goal("expose", "Ruin the duke", 5, (
        Approach("tell", "Tell Wren the duke sold the guild", needs=(Holds("duke-betrayal", {"aware"}),)),
        Approach("wait", "Keep counting and say nothing"),)),))
    told = Knowledge(CAMPAIGN, propositions=[Proposition(CAMPAIGN, "duke-betrayal", "The duke sold the guild",
                                                         Moment(1), "director")],
                     evidence=[Report(CAMPAIGN, "heard-it", ODA, "duke-betrayal", "overheard", Moment(2), "director",
                                      confirmed=True)])
    unaware = choose(gated, oda_knows(), present=PRESENT, controls=CONTROLS)
    assert unaware.approach.approach_id == "wait"
    knowledge = actor_knowledge(told, ODA, as_of=2)
    choice = choose(gated, knowledge, present=PRESENT, controls=CONTROLS)
    assert choice.approach.approach_id == "tell"
    brief = npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)
    assert "Tell Wren the duke sold the guild" in brief.system


def test_c_nothing_to_pursue_and_nothing_refused_adds_nothing():
    knowledge = oda_knows()
    stuck = Agenda(CAMPAIGN, ODA, (Goal("collect", COLLECT_AIM, 5, (
        Approach("press-hask", "Press Hask for the gate toll",
                 needs=(Holds(TALES),)),)),))
    choice = choose(stuck, knowledge, present=PRESENT, controls=CONTROLS)
    assert not choice.acts and choice.goal is None and choice.refusals == ()
    plain = npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS)
    with_choice = npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)
    assert with_choice.system == plain.system


def test_c_a_refusal_alone_still_reaches_the_brief():
    knowledge = oda_knows(warned=True, stance="believed")
    only_bellamy = Agenda(CAMPAIGN, ODA, AGENDA.goals[:1], AGENDA.limits)
    choice = choose(only_bellamy, knowledge, present=(ODA, BELLAMY), controls=CONTROLS)
    assert not choice.acts and [limit.limit_id for limit in choice.refusals] == ["no-informants"]
    brief = npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)
    assert RULE in brief.system and COLLECT_AIM not in brief.system


def test_c_a_refusal_rule_stating_an_unlearned_secret_is_refused():
    knowledge = oda_knows()
    leaky = Agenda(CAMPAIGN, ODA, (Goal("talk", "Keep the dinner pleasant", 5, (
        Approach("gossip", "Trade gossip with Bellamy", tags=frozenset({"gossip"})),
        Approach("wait", "Keep counting and say nothing"),)),),
        (Limit("hush", "Never let slip that the duke sold the guild", frozenset({"gossip"})),))
    choice = choose(leaky, knowledge, present=PRESENT, controls=CONTROLS)
    assert choice.approach.approach_id == "wait" and [limit.limit_id for limit in choice.refusals] == ["hush"]
    with pytest.raises(GenerationError) as error:
        npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)
    assert error.value.reason == "intention_leak" and "duke-betrayal" in str(error.value)


HASK = InstanceRef(CAMPAIGN, "character", "npc-hask")
#: Hask is at the dinner disguised as a pilgrim; who the pilgrim is, is the director's secret.
DISGUISE = SecretFact("pilgrim-is-hask", ("Hask",))
TOLL = Agenda(CAMPAIGN, ODA, (Goal("collect", COLLECT_AIM, 50, (
    Approach("press-hask", "Press Hask for the unpaid gate toll", needs=(Present(HASK),)),
    Approach("press-wren", PRESS_ACTION, needs=(Present(WREN),)),)),))


def test_c_a_disguised_actor_is_not_in_the_cast_the_npc_knows():
    knowledge = oda_knows()
    secrets = (*SECRETS, DISGUISE)
    # Given the true cast, Oda would press the pilgrim by its real name: the brief refuses to say it.
    told_truth = choose(TOLL, knowledge, present=(*PRESENT, HASK), controls=CONTROLS)
    assert told_truth.approach.approach_id == "press-hask"
    with pytest.raises(GenerationError) as error:
        npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=secrets, intention=told_truth)
    assert error.value.reason == "intention_leak" and "pilgrim-is-hask" in str(error.value)

    # The cast Oda recognises leaves Hask out; the turn still has Hask present, and that is allowed.
    choice = choose(TOLL, knowledge, present=PRESENT, controls=CONTROLS)
    assert choice.approach.approach_id == "press-wren"
    harness = Harness([say("Wren. Your tally. Now.")])
    plan = harness.plan(CONTROLS, present=(*(ref.instance_id for ref in PRESENT), HASK.instance_id),
                        speakers=(ODA.instance_id,))
    brief = npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=secrets, intention=choice)
    assert harness.runtime.run_turn(harness.meter(plan), [brief], controls=CONTROLS).status == "complete"


def test_c_a_choice_for_a_cast_that_has_left_is_refused_before_any_call():
    knowledge = oda_knows()
    choice = choose(AGENDA, knowledge, present=PRESENT, controls=CONTROLS)
    assert choice.approach.approach_id == "sell-ledger"  # needs Bellamy, who then leaves
    harness = Harness([say("Coin on the counter.")])
    plan = harness.plan(CONTROLS, present=(ODA.instance_id, WREN.instance_id), speakers=(ODA.instance_id,))
    brief = npc_brief(oda_view(knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)
    with pytest.raises(GenerationError) as error:
        harness.runtime.run_turn(harness.meter(plan), [brief], controls=CONTROLS)
    assert error.value.reason == "intention_cast_mismatch"
    assert harness.prompts() == []
