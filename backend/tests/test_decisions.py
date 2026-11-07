"""045 acceptance: a bounded NPC decision is recorded with its evidence, and retried narration keeps it.

The sprint's proving scenario: Oda chooses to retreat from the counting-house because it fears the
raiders at the door (a belief), knows the back stair is clear (an exit it believes in) and no limit
forbids flight now (nobody it has sworn to protect is present). The choice is recorded for the turn;
a reroll of the narration replays it and never decides again. A model-selected decision is an
optional, capped ``decision`` call that may pick only among the approaches the policy allows.
Every provider call is a scripted fake; every name is synthetic.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from generation_fixtures import BELLAMY, ODA, QUILL, WREN, Harness, say
from voice_fixtures import CAMPAIGN, SECRETS, emberfall

from grimoire.core import Belief, Knowledge, Moment, Proposition, Report, actor_knowledge
from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import TurnRef
from grimoire.core.inference import InferencePolicy, InferenceStage
from grimoire.core.model import ActorView
from grimoire.decisions import (
    Candidate, DecisionError, DecisionRecord, Evidence, agenda_digest, decision_from_payload, decision_to_payload, record_decision,
    replay,
)
from grimoire.generation import (
    DECISION_PHASE, GenerationError, HeardLine, decision_messages, npc_brief, plan_generation_turn, read_pick,
)
from grimoire.providers.cancel import CancelToken
from grimoire.providers.errors import CallRefused
from grimoire.goals import Agenda, Approach, Goal, GoalError, Holds, Limit, Present, choose, explain
from grimoire.voice import SecretFact

CONTROLS = emberfall()[3]
VOICES = emberfall()[4]
RAIDERS, STAIR = "raiders", "back-stair"
RETREAT_ACTION = "Slip down the back stair with the strongbox before the raiders break in"
BAR_ACTION = "Bar the door with the counting table and shout for the watch"
TALLY_ACTION = "Tally the evening's takings at the counter"
RULE = "Never leave Quill behind while there is danger"
AGENDA = Agenda(CAMPAIGN, ODA, goals=(
    Goal("survive", "Get out of this alive with the guild's coin", 80, (
        Approach("retreat", RETREAT_ACTION, needs=(Holds(RAIDERS), Holds(STAIR)), tags=frozenset({"flight"})),
        Approach("bar-door", BAR_ACTION, needs=(Holds(RAIDERS),), tags=frozenset({"stand"})),
    )),
    Goal("count", "Keep the counting-house books straight", 10, (Approach("tally", TALLY_ACTION),)),
), limits=(Limit("no-abandon", RULE, forbids=frozenset({"flight"}), when=(Present(QUILL),)),))
#: Quill has gone home: the scene is Oda, Bellamy and Wren (the player's character).
PRESENT = (ODA, BELLAMY, WREN)
TURN = TurnRef(CAMPAIGN, "dinner.000004")
DECISIONS = InferencePolicy(per_turn_call_caps={InferenceStage.DECISION: 1})


def _proposition(pid: str, text: str) -> Proposition:
    return Proposition(CAMPAIGN, pid, text, Moment(1), "director")


def oda_knows(*, fear: bool = True, exit_: bool = True, as_of: int = 2):
    """Oda's projection: it believes the raiders are at the door, and that the back stair is clear."""
    beliefs, evidence = [], []
    for held, pid in ((fear, RAIDERS), (exit_, STAIR)):
        if held:
            evidence.append(Report(CAMPAIGN, f"oda-sees-{pid}", ODA, pid, "observed", Moment(2), "scene:dinner/t3",
                                   confirmed=True))
            beliefs.append(Belief(CAMPAIGN, f"oda-on-{pid}", ODA, pid, "believed", Moment(2), DIRECTOR,
                                  (f"oda-sees-{pid}",)))
    ledger = Knowledge(CAMPAIGN, propositions=[_proposition(RAIDERS, "Raiders are at the counting-house door"),
                                               _proposition(STAIR, "The back stair is clear")],
                       evidence=evidence, beliefs=beliefs)
    return actor_knowledge(ledger, ODA, as_of=as_of)


def oda_view(knowledge) -> ActorView:
    return ActorView(CAMPAIGN, ODA.instance_id, "Oda", "The guild's counting-house keeper.", knowledge=knowledge)


def brief(choice):
    return npc_brief(oda_view(choice.knowledge), VOICES[ODA.instance_id], secrets=SECRETS, intention=choice)


def ids(present=PRESENT):
    return tuple(ref.instance_id for ref in present)


def narrate(choice, reply: str, *, turn: str):
    """One take of the turn's narration: Oda's one generation call, carrying ``choice``."""
    harness = Harness([say(reply)])
    plan = harness.plan(CONTROLS, present=ids(), speakers=(ODA.instance_id,), turn=turn)
    meter = harness.meter(plan)
    outcome = harness.runtime.run_turn(meter, [brief(choice)], controls=CONTROLS)
    return outcome, meter, harness.prompts()


def oda_choice(knowledge=None, present=PRESENT, **kw):
    return choose(AGENDA, knowledge or oda_knows(), present=present, controls=CONTROLS, **kw)


# -- A. the proof: a retreat decided on fear, constraints and exits, kept through a reroll --------

def test_a_oda_retreats_and_the_recorded_choice_survives_retried_narration():
    choice = oda_choice()
    assert (choice.goal.goal_id, choice.approach.approach_id) == ("survive", "retreat")
    record = record_decision(choice, TURN)

    assert record.method == "policy" and record.chosen == record.default == ("survive", "retreat")
    # The evidence is what the NPC itself held: its fear and the exit it believes in.
    assert [(item.supports, item.proposition_id, item.stance) for item in record.evidence] == [
        ("survive/retreat", RAIDERS, "believed"), ("survive/retreat", STAIR, "believed")]
    assert [(c.approach_id, c.outcome) for c in record.candidates] == [
        ("retreat", "chosen"), ("bar-door", "outranked"), ("tally", "outranked")]

    # The record is what a store keeps; it reads back identically.
    record = decision_from_payload(decision_to_payload(record))

    first, meter, (prompt,) = narrate(choice, "Strongbox. Stair. Go.", turn="dinner.000004.1")
    assert first.status == "complete" and RETREAT_ACTION in prompt
    assert meter.summary().optional.attempts == 0  # the deterministic policy costs no call

    # A reroll of the narration replays the recorded decision: same choice, no decision call, any reply.
    for take, reply in ((2, "Back stair, now, before they're through."), (3, "We leave. Quietly.")):
        again = replay(record, AGENDA, oda_knows(), present=PRESENT, controls=CONTROLS)
        assert (again.goal.goal_id, again.approach.approach_id) == record.chosen
        outcome, meter, (prompt,) = narrate(again, reply, turn=f"dinner.000004.{take}")
        assert outcome.status == "complete" and RETREAT_ACTION in prompt
        assert set(meter.summary().by_stage) == {InferenceStage.NPC_GENERATION}


def test_a_without_fear_an_exit_or_freedom_to_flee_oda_decides_otherwise():
    # Not afraid: nothing to survive, so it tallies.
    assert oda_choice(oda_knows(fear=False)).approach.approach_id == "tally"
    # Afraid but no exit it knows of: it stands and bars the door.
    no_exit = oda_choice(oda_knows(exit_=False))
    assert no_exit.approach.approach_id == "bar-door"
    assert record_decision(no_exit, TURN).candidates[0].unmet == (f"holds:{STAIR}:believed|suspected",)
    # Quill is still here: the limit forbids flight, so it bars the door; the limit's trigger is evidence.
    constrained = oda_choice(present=(*PRESENT, QUILL))
    assert constrained.approach.approach_id == "bar-door"
    record = record_decision(constrained, TURN)
    assert record.candidates[0].forbidden_by == ("no-abandon",)
    assert [(item.supports, item.actor) for item in record.evidence if item.actor is not None] == [
        ("limit:no-abandon", QUILL)]


def test_a_replay_refuses_rather_than_decides_again():
    record = record_decision(oda_choice(), TURN)
    # Oda has since stopped believing in the stair: the recorded retreat is no longer the policy's pick.
    with pytest.raises(DecisionError) as error:
        replay(record, AGENDA, oda_knows(exit_=False), present=PRESENT, controls=CONTROLS)
    assert error.value.reason == "decision_changed"
    # The director edited the agenda: a new turn decides under it, the old record does not stretch.
    edited = replace(AGENDA, goals=(replace(AGENDA.goals[0], priority=5), AGENDA.goals[1]))
    with pytest.raises(DecisionError) as error:
        replay(record, edited, oda_knows(), present=PRESENT, controls=CONTROLS)
    assert error.value.reason == "agenda_changed"
    bellamy = Agenda(CAMPAIGN, BELLAMY, (Goal("g", "Listen", 5, (Approach("a", "Listen at the door"),)),))
    with pytest.raises(DecisionError) as error:
        replay(record, bellamy, oda_knows(), present=PRESENT, controls=CONTROLS)
    assert error.value.reason == "foreign_record"


# -- B. the optional decision call: only when the mode permits, only among what the policy allows -----

def decision_turn(replies, *, policy=DECISIONS, turn="dinner.000004.1", present=PRESENT):
    harness = Harness(list(replies))
    plan = plan_generation_turn(CAMPAIGN, turn, present=ids(present), speakers=(ODA.instance_id,),
                                controls=CONTROLS, policy=policy, decisions=(ODA.instance_id,))
    return harness, plan, harness.meter(plan)


def test_b_a_model_pick_among_permitted_approaches_is_recorded_and_replayed():
    harness, plan, meter = decision_turn([say("2"), say("Table against the door! Watch! Watch!")])
    (call,) = [call for call in plan.calls if call.stage is InferenceStage.DECISION]
    assert call.call_id.phase == DECISION_PHASE

    default = oda_choice()
    outcome = harness.runtime.decide(meter, call, brief(default), controls=CONTROLS)
    assert outcome.status == "accepted" and outcome.pick == ("survive", "bar-door")
    picked = oda_choice(selected=outcome.pick)
    assert picked.approach.approach_id == "bar-door" and picked.default.approach_id == "retreat"
    assert any("in place of survive/retreat" in line for line in explain(picked))

    turn = harness.runtime.run_turn(meter, [brief(picked)], controls=CONTROLS)
    assert turn.status == "complete"
    decision_prompt, speech_prompt = harness.prompts()
    # The decision call offers exactly the possible approaches, numbered; the narration carries the pick.
    assert f"1. Get out of this alive with the guild's coin: {RETREAT_ACTION}" in decision_prompt
    assert f"2. Get out of this alive with the guild's coin: {BAR_ACTION}" in decision_prompt
    assert f"3. Keep the counting-house books straight: {TALLY_ACTION}" in decision_prompt
    assert BAR_ACTION in speech_prompt
    summary = meter.summary()
    assert summary.optional.attempts == 1 and summary.by_stage[InferenceStage.DECISION].attempts == 1

    record = record_decision(picked, TURN, call=str(call.call_id))
    assert record.method == "model" and record.selected and record.chosen == ("survive", "bar-door")
    record = decision_from_payload(decision_to_payload(record))
    again = replay(record, AGENDA, oda_knows(), present=PRESENT, controls=CONTROLS)
    assert again.approach.approach_id == "bar-door"  # the reroll keeps the model's pick, with no call


def test_b_a_mode_without_the_decision_stage_declines_it_and_costs_nothing_extra():
    harness, plan, meter = decision_turn([say("Strongbox. Stair. Go.")], policy=InferencePolicy())
    plain = plan_generation_turn(CAMPAIGN, "dinner.000004.1", present=ids(), speakers=(ODA.instance_id,),
                                 controls=CONTROLS, policy=InferencePolicy())
    assert plan.calls == plain.calls  # the very plan a turn without decisions has
    assert [(d.request.stage, d.reason) for d in plan.declined] == [(InferenceStage.DECISION, "disabled")]
    choice = oda_choice()
    outcome = harness.runtime.run_turn(meter, [brief(choice)], controls=CONTROLS)
    assert outcome.status == "complete" and meter.summary().optional.attempts == 0
    assert record_decision(choice, TURN).method == "policy"


def test_b_an_unreadable_pick_falls_back_to_the_authored_order_and_says_so():
    harness, plan, meter = decision_turn([say("The second one, I think."), say("Strongbox. Stair. Go.")])
    (call, _speech) = plan.calls
    outcome = harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS)
    assert outcome.pick is None and outcome.reason == "unreadable_pick"
    choice = oda_choice()
    assert harness.runtime.run_turn(meter, [brief(choice)], controls=CONTROLS).status == "complete"
    record = record_decision(choice, TURN, call=str(call.call_id), fallback=outcome.reason)
    assert record.method == "model" and not record.selected and record.chosen == ("survive", "retreat")
    assert replay(record, AGENDA, oda_knows(), present=PRESENT, controls=CONTROLS).approach.approach_id == "retreat"


def test_b_the_narration_must_carry_what_was_decided():
    harness, plan, meter = decision_turn([say("2"), say("Stair!")])
    (call, _speech) = plan.calls
    with pytest.raises(GenerationError) as error:
        harness.runtime.run_turn(meter, [brief(oda_choice())], controls=CONTROLS)
    assert error.value.reason == "decision_not_run"
    harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS)
    with pytest.raises(GenerationError) as error:  # the model picked bar-door; the brief still says retreat
        harness.runtime.run_turn(meter, [brief(oda_choice())], controls=CONTROLS)
    assert error.value.reason == "decision_ignored"
    with pytest.raises(GenerationError) as error:
        harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS)
    assert error.value.reason == "call_already_run"


def test_b_a_pick_can_never_reach_a_forbidden_or_impossible_approach():
    # With Quill present, flight is forbidden: it is not offered, and a selection of it is refused.
    constrained = oda_choice(present=(*PRESENT, QUILL))
    harness, plan, meter = decision_turn([say("1")], present=(*PRESENT, QUILL))
    (call, _speech) = plan.calls
    (_system, user) = decision_messages(brief(constrained))
    assert RETREAT_ACTION not in user[1] and BAR_ACTION in user[1]
    outcome = harness.runtime.decide(meter, call, brief(constrained), controls=CONTROLS)
    assert outcome.pick == ("survive", "bar-door")  # "1" is the first *permitted* option
    # Forbidden (Quill is here) or impossible (no exit Oda knows of): no selection reaches it.
    for knowledge, present in ((oda_knows(), (*PRESENT, QUILL)), (oda_knows(exit_=False), PRESENT)):
        with pytest.raises(GoalError) as error:
            choose(AGENDA, knowledge, present=present, controls=CONTROLS, selected=("survive", "retreat"))
        assert error.value.reason == "infeasible_selection"
    with pytest.raises(GoalError) as error:
        oda_choice(selected=("survive", "fly"))
    assert error.value.reason == "unknown_approach"
    assert read_pick("4", (("a", "b"), ("c", "d"))) is None and read_pick(" 2. ", (("a", "b"), ("c", "d"))) == ("c", "d")


def test_b_no_call_without_a_real_choice_or_for_a_player_character():
    alone = oda_choice(oda_knows(fear=False))  # only tallying is possible: one option is no decision
    with pytest.raises(GenerationError) as error:
        decision_messages(brief(alone))
    assert error.value.reason == "nothing_to_decide"
    with pytest.raises(GenerationError) as error:
        plan_generation_turn(CAMPAIGN, "t9", present=ids(), speakers=(), controls=CONTROLS, policy=DECISIONS,
                             decisions=(WREN.instance_id,))
    assert error.value.reason == "player_speaker"


def test_b_an_option_stating_a_secret_the_npc_has_not_learned_is_never_sent():
    secret = SecretFact("strongbox-forged", ("the strongbox coin is forged",))
    leaky = replace(AGENDA, goals=(replace(AGENDA.goals[0], approaches=(
        AGENDA.goals[0].approaches[0],
        replace(AGENDA.goals[0].approaches[1], action="Hide the fact that the strongbox coin is forged"),
    )), AGENDA.goals[1]))
    choice = choose(leaky, oda_knows(), present=PRESENT, controls=CONTROLS)
    leaky_brief = npc_brief(oda_view(choice.knowledge), VOICES[ODA.instance_id], secrets=(*SECRETS, secret),
                            intention=choice)
    with pytest.raises(GenerationError) as error:
        decision_messages(leaky_brief)
    assert error.value.reason == "option_leak"


# -- C. the record's own rules -------------------------------------------------------------------------

def test_c_records_validate_and_read_back_strictly():
    record = record_decision(oda_choice(), TURN)
    payload = decision_to_payload(record)
    assert payload["agenda_digest"] == agenda_digest(AGENDA)
    for bad, reason in (({**payload, "extra": 1}, "bad_record"), ({**payload, "format": "grimoire.decision/0"},
                                                                  "bad_record"),
                        ({**payload, "chosen": ["survive", "bar-door"]}, "invalid_field"),
                        ({**payload, "method": "model"}, "invalid_field"), ({**payload, "as_of": True}, "bad_record"),
                        ({k: v for k, v in payload.items() if k != "evidence"}, "bad_record")):
        with pytest.raises(DecisionError) as error:
            decision_from_payload(bad)
        assert error.value.reason == reason
    with pytest.raises(DecisionError) as error:
        record_decision(oda_choice(selected=("survive", "bar-door")), TURN)
    assert error.value.reason == "bad_choice"  # a selected choice is a model decision, with its call
    with pytest.raises(DecisionError) as error:
        record_decision(oda_choice(), TurnRef("other-campaign", "x.000001"))
    assert error.value.reason == "campaign_mismatch"
    with pytest.raises(DecisionError) as error:
        DecisionRecord(TURN, ODA, "policy", 2, "sha256:" + "0" * 64, (), ("a", "b"), None, ())
    assert error.value.reason == "invalid_field"


# -- D. the call's plumbing: what it is shown, what it costs, and what a turn can still run ------------

def test_d_the_call_is_not_told_the_default_and_hears_what_was_said():
    harness, plan, meter = decision_turn([say("2"), say("Table, now!")], present=(*PRESENT, QUILL))
    (call, _speech) = plan.calls
    heard = (HeardLine("Bellamy", "They're at the door!", BELLAMY),)
    constrained = oda_choice(present=(*PRESENT, QUILL))
    shown = npc_brief(oda_view(constrained.knowledge), VOICES[ODA.instance_id], secrets=SECRETS,
                      intention=constrained, heard=heard)
    harness.runtime.decide(meter, call, shown, controls=CONTROLS)
    (decision_prompt,) = harness.prompts()
    # What the authored order would do is left out, so option 1 is not the brief's own plan; a refusal
    # that holds is kept, and the call hears the turn's lines as the speech call would.
    assert "What you mean to do about it" not in decision_prompt and RULE in decision_prompt
    assert "They're at the door!" in decision_prompt
    assert "What you mean to do about it" in shown.system


def test_d_a_secret_heard_aloud_does_not_clear_an_option_the_npc_does_not_hold():
    secret = SecretFact("strongbox-forged", ("the strongbox coin is forged",))
    leaky = replace(AGENDA, goals=(replace(AGENDA.goals[0], approaches=(
        AGENDA.goals[0].approaches[0],
        replace(AGENDA.goals[0].approaches[1], action="Hide the fact that the strongbox coin is forged"),
    )), AGENDA.goals[1]))
    choice = choose(leaky, oda_knows(), present=PRESENT, controls=CONTROLS)
    heard = (HeardLine("Bellamy", "Someone said the strongbox coin is forged.", BELLAMY),)
    leaky_brief = npc_brief(oda_view(choice.knowledge), VOICES[ODA.instance_id], secrets=(*SECRETS, secret),
                            intention=choice, heard=heard)
    with pytest.raises(GenerationError) as error:  # the generation brief would refuse it as intention_leak
        decision_messages(leaky_brief)
    assert error.value.reason == "option_leak"


def test_d_a_choice_made_for_another_scene_is_refused_before_any_call():
    # Quill came back after the choice was made: flight is forbidden now, so the old options are wrong.
    harness, plan, meter = decision_turn([say("1")], present=(*PRESENT, QUILL))
    (call, _speech) = plan.calls
    with pytest.raises(GenerationError) as error:
        harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS)
    assert error.value.reason == "intention_cast_mismatch" and harness.prompts() == []
    assert meter.summary().optional.attempts == 0


def test_d_nothing_to_decide_makes_no_call_and_the_turn_still_runs():
    alone = oda_choice(oda_knows(fear=False))
    harness, plan, meter = decision_turn([say("Forty-one, forty-two...")])
    (call, _speech) = plan.calls
    outcome = harness.runtime.decide(meter, call, brief(alone), controls=CONTROLS)
    assert (outcome.status, outcome.pick, outcome.reason) == ("skipped", None, "nothing_to_decide")
    assert harness.runtime.run_turn(meter, [brief(alone)], controls=CONTROLS).status == "complete"
    assert meter.summary().optional.attempts == 0 and len(harness.prompts()) == 1
    with pytest.raises(GenerationError) as error:
        harness.runtime.decide(meter, call, brief(alone), controls=CONTROLS)
    assert error.value.reason == "call_already_run"


def test_d_a_second_meter_on_the_same_plan_cannot_undo_the_first_meters_decision():
    harness, plan, meter = decision_turn([say("2"), say("Table, now!")])
    (call, _speech) = plan.calls
    assert harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS).pick == ("survive", "bar-door")
    other = harness.meter(plan)
    with pytest.raises(CallRefused):  # the adapter refuses a second meter for the turn before sending anything
        harness.runtime.decide(other, call, brief(oda_choice()), controls=CONTROLS)
    picked = oda_choice(selected=("survive", "bar-door"))
    assert harness.runtime.run_turn(meter, [brief(picked)], controls=CONTROLS).status == "complete"


def test_d_a_cancelled_call_falls_back_with_its_reason():
    harness, plan, meter = decision_turn([say("2"), say("Stair!")])
    (call, _speech) = plan.calls
    cancel = CancelToken()
    cancel.cancel()
    outcome = harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS, cancel=cancel)
    assert outcome.pick is None and outcome.reason == "cancelled"
    record = record_decision(oda_choice(), TURN, call=str(call.call_id), fallback=outcome.reason)
    assert record.chosen == record.default == ("survive", "retreat")


def test_d_a_replayed_model_pick_must_stand_on_the_same_ground():
    record = record_decision(oda_choice(selected=("count", "tally")), TURN, call="decision-1")
    assert replay(record, AGENDA, oda_knows(), present=PRESENT, controls=CONTROLS).approach.approach_id == "tally"
    # Quill came back: tallying is still possible, but the authored order and its refusals are not as recorded.
    with pytest.raises(DecisionError) as error:
        replay(record, AGENDA, oda_knows(), present=(*PRESENT, QUILL), controls=CONTROLS)
    assert error.value.reason == "decision_changed"


def test_d_a_record_cannot_contradict_itself():
    for bad in (lambda: Candidate("survive", 7, "chosen"),  # type: ignore[arg-type]
                lambda: Candidate("survive", "retreat", "chosen", forbidden_by=("no-abandon",)),
                lambda: Candidate("survive", "retreat", "forbidden"),
                lambda: Candidate("survive", "retreat", "unmet", unmet=["present:x"]),  # type: ignore[arg-type]
                lambda: Evidence("survive/retreat", proposition_id="", stance="believed")):
        with pytest.raises(DecisionError):
            bad()
    good = record_decision(oda_choice(), TURN)
    for bad in (lambda: replace(good, candidates=good.candidates + good.candidates[:1]),
                # a model decision with nothing to choose among, or that chose nothing and gave no reason
                lambda: record_decision(oda_choice(oda_knows(fear=False), selected=("count", "tally")), TURN,
                                        call="decision-1"),
                lambda: replace(record_decision(oda_choice(), TURN, call="d-1", fallback="cancelled"), fallback=None, chosen=None,
                                candidates=tuple(replace(c, outcome="outranked") for c in good.candidates))):
        with pytest.raises(DecisionError) as error:
            bad()
        assert error.value.reason == "invalid_field"


def test_d_the_reroll_of_a_model_pick_carries_it_with_no_decision_call():
    harness, plan, meter = decision_turn([say("2"), say("Table, now!")])
    (call, _speech) = plan.calls
    outcome = harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS)
    picked = oda_choice(selected=outcome.pick)
    harness.runtime.run_turn(meter, [brief(picked)], controls=CONTROLS)
    record = decision_from_payload(decision_to_payload(record_decision(picked, TURN, call=str(call.call_id))))
    # The reroll: the recorded pick, replayed into an ordinary narration plan with no decision call.
    again = replay(record, AGENDA, oda_knows(), present=PRESENT, controls=CONTROLS)
    outcome, meter, (prompt,) = narrate(again, "The table! Hold it there!", turn="dinner.000004.2")
    assert outcome.status == "complete" and BAR_ACTION in prompt and RETREAT_ACTION not in prompt


def test_d_the_decision_call_is_checked_as_planned():
    harness, plan, meter = decision_turn([say("2")])
    (call, speech) = plan.calls
    with pytest.raises(GenerationError) as error:  # the speech call is no decision
        harness.runtime.decide(meter, speech, brief(oda_choice()), controls=CONTROLS)
    assert error.value.reason == "decision_mismatch"
    with pytest.raises(GenerationError) as error:
        harness.runtime.decide(meter, call, brief(oda_choice(selected=("survive", "bar-door"))), controls=CONTROLS)
    assert error.value.reason == "already_selected"
    harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS)
    with pytest.raises(GenerationError) as error:  # decided on one meter, run on another
        harness.runtime.run_turn(harness.meter(plan), [brief(oda_choice(selected=("survive", "bar-door")))],
                                 controls=CONTROLS)
    assert error.value.reason == "decision_not_run"
    for kwargs, reason in (({"decisions": (ODA.instance_id, ODA.instance_id)}, "duplicate_decision"),
                           ({"decisions": (QUILL.instance_id,)}, "absent_decider"),
                           ({"decisions": (ODA.instance_id,), "phase": DECISION_PHASE}, "bad_input")):
        with pytest.raises(GenerationError) as error:
            plan_generation_turn(CAMPAIGN, "t9", present=ids(), speakers=(ODA.instance_id,), controls=CONTROLS,
                                 policy=DECISIONS, **kwargs)
        assert error.value.reason == reason
    # Two deciders and a cap of one: the second is declined as data.
    two = plan_generation_turn(CAMPAIGN, "t9", present=ids(), speakers=(ODA.instance_id,), controls=CONTROLS,
                               policy=DECISIONS, decisions=(ODA.instance_id, BELLAMY.instance_id))
    assert [(d.request.actor_id, d.reason) for d in two.declined] == [(BELLAMY.instance_id, "cap_reached")]


def test_d_a_fallback_leaves_no_room_for_a_selection():
    harness, plan, meter = decision_turn([say("perhaps"), say("Stair!")])
    (call, _speech) = plan.calls
    assert harness.runtime.decide(meter, call, brief(oda_choice()), controls=CONTROLS).pick is None
    with pytest.raises(GenerationError) as error:
        harness.runtime.run_turn(meter, [brief(oda_choice(selected=("survive", "bar-door")))], controls=CONTROLS)
    assert error.value.reason == "decision_ignored"
