"""A rival's plan progresses while the party travels; nobody else is simulated or told (076)."""
import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from offscreen_fixtures import (
    DAY,
    GUILD,
    LEADER,
    START,
    C,
    calendar,
    decided_guild,
    rival_plan,
    travel,
)

from grimoire.calendars import Advance as CalendarAdvance
from grimoire.calendars import Deadline, Duration, Schedule
from grimoire.institutions import Institution, InstitutionState
from grimoire.offscreen import (
    Adopt,
    DeadlineOutcome,
    Development,
    OffscreenError,
    PlanStep,
    Skip,
    apply,
    pending_skips,
    state_from_payload,
    state_to_payload,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "grimoire"


def adopted(plan=None, cal=None, operation_id="adopt-docks"):
    cal = cal or calendar()
    state, receipt, again = apply(C, None, Adopt(operation_id, plan or rival_plan()), calendar=cal,
                                  institution=decided_guild())
    assert not again and receipt.at == START
    return state


def test_rival_plan_progresses_during_twenty_days_of_travel():
    state = adopted()
    cal = calendar(travel())
    assert pending_skips(state, cal) == ("travel",)
    after, receipt, again = apply(C, state, Skip("travel", 4), calendar=cal)
    assert not again and receipt.before == START and receipt.after == START.advance(20 * DAY)
    # Two of three steps are done, each at its own due moment, not at the end of the trip.
    assert receipt.developments == (Development("docks", "coin", START.advance(5 * DAY)),
                                    Development("docks", "magistrate", START.advance(12 * DAY)))
    progress = after.progress("docks")
    assert progress.completed == 2 and not progress.complete
    assert progress.pending[0][0].step_id == "seize" and progress.pending[0][1] == START.advance(22 * DAY)
    assert receipt.deferred == () and receipt.deadlines == ()
    assert pending_skips(after, cal) == () and after.as_of == cal.now
    # The original state is untouched: progress is a new value, not a mutation.
    assert state.progress("docks").completed == 0


def test_crossing_the_deadline_reports_the_race_once_and_later_steps_keep_their_due_moments():
    cal = calendar(travel(), travel("more", 3))
    state, _, _ = apply(C, adopted(), Skip("travel", 1), calendar=cal)
    final, receipt, _ = apply(C, state, Skip("more", 1), calendar=cal)
    assert receipt.developments == (Development("docks", "seize", START.advance(22 * DAY)),)
    # The festival (day 21) came before the docks fell (day 22).
    assert receipt.deadlines == (DeadlineOutcome("docks", "festival", "missed"),)
    assert final.progress("docks").complete
    early = rival_plan(steps=(PlanStep("coin", "Gather the bribe money", 5 * DAY),))
    other = calendar(travel())
    _, receipt, _ = apply(C, adopted(early), Skip("travel", 1), calendar=other)
    assert receipt.deadlines == ()  # the festival is not crossed by a 20-day trip
    long_trip = calendar(travel("long", 30))
    _, receipt, _ = apply(C, adopted(early), Skip("long", 1), calendar=long_trip)
    assert receipt.deadlines == (DeadlineOutcome("docks", "festival", "met"),)


def test_retry_returns_the_original_receipt_and_never_counts_time_twice():
    cal = calendar(travel())
    state, receipt, _ = apply(C, adopted(), Skip("travel", 4), calendar=cal)
    again, same, already = apply(C, state, Skip("travel", 4), calendar=cal)
    assert already and same is receipt and again is state
    with pytest.raises(OffscreenError, match="operation_mismatch"):
        apply(C, state, Skip("travel", 2), calendar=cal)
    # An adoption retry rechecks the decision; a receipt lookup is not an authority bypass.
    first = adopted()
    assert apply(C, first, Adopt("adopt-docks", rival_plan()), calendar=calendar(),
                 institution=decided_guild())[2]
    with pytest.raises(OffscreenError, match="decision_missing"):
        apply(C, first, Adopt("adopt-docks", rival_plan()), calendar=calendar(), institution=None)


def test_an_adoption_id_never_shadows_a_later_calendar_advance_of_the_same_name():
    state = adopted(operation_id="day-2")
    cal = calendar(travel("day-2", 20))
    assert pending_skips(state, cal) == ("day-2",)
    state, receipt, again = apply(C, state, Skip("day-2", 4), calendar=cal)
    assert not again and len(receipt.developments) == 2 and pending_skips(state, cal) == ()
    assert state.adoption("day-2").operation.plan.plan_id == "docks" and state.skip("day-2") is receipt
    assert state_from_payload(C, state_to_payload(state), cal) == state
    # Each kind still retries, and still refuses a changed request, within its own namespace.
    assert apply(C, state, Skip("day-2", 4), calendar=cal)[2]
    with pytest.raises(OffscreenError, match="operation_mismatch"):
        apply(C, state, Adopt("day-2", rival_plan(plan_id="other")), calendar=cal, institution=decided_guild())


def test_one_decision_authorizes_one_plan():
    with pytest.raises(OffscreenError, match="decision_used"):
        apply(C, adopted(), Adopt("again", rival_plan(plan_id="docks-again", decision_id="seize-docks")), calendar=calendar(),
              institution=decided_guild())


def test_skips_apply_in_calendar_order_and_only_for_real_advances():
    cal = calendar(travel("a", 2), travel("b", 3))
    state = adopted()
    for operation, reason in ((Skip("b", 1), "skip_out_of_order"), (Skip("missing", 1), "unknown_skip"),
                              (Skip("schedule-festival", 1), "not_an_advance"), (Skip("start", 1), "not_an_advance")):
        with pytest.raises(OffscreenError, match=reason):
            apply(C, state, operation, calendar=cal)
    assert pending_skips(state, cal) == ("a", "b")
    with pytest.raises(OffscreenError, match="no_plans"):
        apply(C, None, Skip("a", 1), calendar=cal)
    # Starting another plan "now" while two advances are unapplied would skip that time.
    with pytest.raises(OffscreenError, match="pending_skips"):
        apply(C, state, Adopt("adopt-2", rival_plan(plan_id="later")), calendar=cal, institution=decided_guild())
    state, _, _ = apply(C, state, Skip("a", 1), calendar=cal)
    state, _, _ = apply(C, state, Skip("b", 1), calendar=cal)
    later, receipt, _ = apply(C, state, Adopt("adopt-2", rival_plan(plan_id="later")), calendar=cal,
                              institution=decided_guild())
    assert receipt.at == START.advance(5 * DAY) and later.progress("later").dues[0] == START.advance(10 * DAY)


def test_relevance_budget_defers_plans_without_dropping_their_steps():
    guild = decided_guild()
    cal = calendar(travel("week", 7), travel("next", 1))
    state = None
    plans = (rival_plan(plan_id="low", priority=10, deadline_id=None),
             rival_plan(plan_id="high", priority=90, deadline_id=None),
             rival_plan(plan_id="idle", deadline_id=None, steps=(PlanStep("later", "Wait for winter", 60 * DAY),)))
    for i, plan in enumerate(plans):
        state, _, _ = apply(C, state, Adopt(f"adopt-{i}", plan), calendar=calendar(), institution=guild)
    state, receipt, _ = apply(C, state, Skip("week", 1), calendar=cal)
    # Priority picks one of the two due plans; the idle plan is not due, so it is not even deferred.
    assert [d.plan_id for d in receipt.developments] == ["high"] and receipt.deferred == ("low",)
    state, receipt, _ = apply(C, state, Skip("next", 1), calendar=cal)
    # The deferred plan's step is flagged later with its original due moment.
    assert receipt.developments == (Development("low", "coin", START.advance(5 * DAY)),)
    assert state.progress("idle").completed == 0


def test_a_crossed_deadline_makes_its_plan_relevant_before_priority():
    guild = decided_guild()
    cal = calendar(travel("trip", 23))
    state, _, _ = apply(C, None, Adopt("a1", rival_plan(plan_id="racing", priority=1)), calendar=calendar(),
                        institution=guild)
    state, _, _ = apply(C, state, Adopt("a2", rival_plan(plan_id="busy", priority=100, deadline_id=None)),
                        calendar=calendar(), institution=guild)
    _, receipt, _ = apply(C, state, Skip("trip", 1), calendar=cal)
    assert {d.plan_id for d in receipt.developments} == {"racing"} and receipt.deferred == ("busy",)
    assert receipt.deadlines == (DeadlineOutcome("racing", "festival", "missed"),)


@pytest.mark.parametrize("change, institution, reason", [
    ({"decision_id": "other"}, None, "decision_missing"),
    ({"choice": "Burn the docks"}, None, "decision_mismatch"),
    ({}, InstitutionState(Institution(GUILD.__class__(C, "group", "other"), LEADER, ("x",), "y")), "decision_mismatch"),
    ({"deadline_id": "nope"}, None, "unknown_deadline"),
])
def test_adoption_needs_the_recorded_decision_and_a_real_future_deadline(change, institution, reason):
    plan = rival_plan(**change)
    institution = institution or decided_guild()
    with pytest.raises(OffscreenError, match=reason):
        apply(C, None, Adopt("adopt", plan), calendar=calendar(), institution=institution)


def test_adoption_refuses_duplicates_past_deadlines_and_out_of_range_plans():
    state = adopted()
    with pytest.raises(OffscreenError, match="duplicate_plan"):
        apply(C, state, Adopt("again", rival_plan()), calendar=calendar(), institution=decided_guild())
    passed = calendar(travel("long", 30))
    with pytest.raises(OffscreenError, match="past_deadline"):
        apply(C, None, Adopt("adopt", rival_plan()), calendar=passed, institution=decided_guild())
    huge = rival_plan(deadline_id=None, steps=tuple(PlanStep(f"s{i}", "Endure", 100 * 366 * DAY) for i in range(16)))
    near_end = calendar(CalendarAdvance("far", Duration((3652059 - START.fixed_day - 1) * DAY)))
    with pytest.raises(OffscreenError, match="out_of_range"):
        apply(C, None, Adopt("adopt", huge), calendar=near_end, institution=decided_guild())


@pytest.mark.parametrize("build, reason", [
    (lambda: rival_plan(steps=()), "invalid_field"),
    (lambda: rival_plan(steps=(PlanStep("a", "x", 1), PlanStep("a", "y", 1))), "duplicate_step"),
    (lambda: rival_plan(steps=tuple(PlanStep(f"s{i}", "x", 1) for i in range(17))), "limit"),
    (lambda: rival_plan(group=LEADER), "identity"),
    (lambda: rival_plan(priority=True), "invalid_field"),
    (lambda: rival_plan(goal="line\nbreak"), "invalid_field"),
    (lambda: PlanStep("a", "x", 0), "invalid_field"),
    (lambda: Skip("a", 0), "invalid_field"),
    (lambda: Skip("a", 33), "invalid_field"),
    (lambda: Adopt("a", "plan"), "invalid_field"),
])
def test_invalid_values_are_refused(build, reason):
    with pytest.raises(OffscreenError, match=reason):
        build()


def test_inputs_are_frozen_at_admission():
    steps = [PlanStep("coin", "Gather the bribe money", DAY)]
    plan = rival_plan(steps=steps, deadline_id=None)
    steps.clear()
    assert len(plan.steps) == 1


def test_codec_round_trips_and_replay_refuses_tampering():
    cal = calendar(travel())
    state, _, _ = apply(C, adopted(), Skip("travel", 4), calendar=cal)
    payload = state_to_payload(state)
    assert state_from_payload(C, payload, cal) == state
    for tamper in (
        lambda p: p["receipts"][1]["developments"].pop(),
        lambda p: p["receipts"][1].update(after={"fixed_day": START.fixed_day + 30, "minute": 600}),
        lambda p: p["receipts"][0]["plan"]["steps"][0].update(minutes=DAY),
        lambda p: p["receipts"][0]["plan"].update(group="campaign:elsewhere/group/rival-guild"),
        lambda p: p.update(as_of={"fixed_day": START.fixed_day, "minute": 600}),
        lambda p: p["receipts"].append(dict(p["receipts"][1])),
        lambda p: p["receipts"].pop(0),
        lambda p: p.update(receipts=[]),
    ):
        broken = state_to_payload(state)
        tamper(broken)
        with pytest.raises(OffscreenError, match="invalid_history"):
            state_from_payload(C, broken, cal)
    for tamper, reason in ((lambda p: p.update(schema="grimoire.offscreen/2"), "unsupported_schema"),
                           (lambda p: p.update(extra=1), "invalid_record"),
                           (lambda p: p.update(campaign_id="elsewhere"), "campaign_mismatch")):
        broken = state_to_payload(state)
        tamper(broken)
        with pytest.raises(OffscreenError, match=reason):
            state_from_payload(C, broken, cal)
    # A calendar whose history no longer holds the skip's advance fails closed too.
    with pytest.raises(OffscreenError, match="invalid_history"):
        state_from_payload(C, payload, calendar(travel("travel", 19)))
    with pytest.raises(OffscreenError, match="calendar_mismatch"):
        state_from_payload(C, payload, None)


def test_replay_accepts_later_unapplied_advances_and_later_adoptions():
    cal = calendar(travel("a", 2))
    state, _, _ = apply(C, adopted(), Skip("a", 1), calendar=cal)
    state, _, _ = apply(C, state, Adopt("adopt-2", rival_plan(plan_id="later")), calendar=cal,
                        institution=decided_guild())
    longer = calendar(travel("a", 2), travel("b", 5))
    assert state_from_payload(C, state_to_payload(state), longer) == state
    assert pending_skips(state, longer) == ("b",)


def test_scheduler_takes_no_roster_and_calls_no_model():
    """The shopkeeper costs nothing: a skip takes no roster, and the package loads no model code."""
    direct = ("grimoire.providers", "grimoire.runtime", "grimoire.generation", "grimoire.core.roster",
              "grimoire.core.perspective", "grimoire.decisions", "grimoire.goals")
    for path in (SRC / "offscreen").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert not name.startswith(direct), f"{path.name} imports {name}"
    # Transitively too: importing the scheduler pulls in no provider, runtime or inference module.
    probe = ("import sys, grimoire.offscreen; print('\\n'.join(sorted(m for m in sys.modules "
             "if m.startswith(('grimoire.providers', 'grimoire.runtime', 'grimoire.generation', "
             "'grimoire.actor_inference', 'grimoire.ensemble', 'grimoire.decisions', 'grimoire.goals', "
             "'grimoire.core.inference')))))")
    loaded = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    assert loaded.strip() == ""
    assert "roster" not in inspect.signature(apply).parameters
    # Adding a deadline that only an unrelated character cares about changes nothing for the guild.
    cal = calendar(Schedule("shop", Deadline("shop-restock", START.advance(3 * DAY), "Shop restock")), travel())
    _, receipt, _ = apply(C, adopted(cal=calendar(Schedule("shop", Deadline("shop-restock", START.advance(3 * DAY),
                                                                            "Shop restock")))),
                          Skip("travel", 4), calendar=cal)
    assert {d.plan_id for d in receipt.developments} == {"docks"} and receipt.deadlines == ()
