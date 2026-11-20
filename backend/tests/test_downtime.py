"""Pure long-activity rules and their strict readable codec (074)."""
from datetime import date

import pytest

from grimoire.calendars import Advance, CalendarDefinition, Duration, Initialize, Moment
from grimoire.calendars import apply as apply_calendar
from grimoire.core.identity import InstanceRef
from grimoire.downtime import (
    Abandon, Activity, Begin, DowntimeError, Pursue, apply, empty, state_from_payload, state_to_payload,
)

from downtime_fixtures import DAY, MARA, STUDY

START = Moment(date(2024, 2, 20).toordinal())


def clock():
    return apply_calendar("saltmarch", None, Initialize("start", CalendarDefinition(), START))[0]


@pytest.mark.parametrize("build", [
    lambda: Activity("Upper", MARA, "study", "Read", 60),
    lambda: Activity("a", "campaign:saltmarch/character/mara", "study", "Read", 60),
    lambda: Activity("a", MARA, "napping", "Read", 60),
    lambda: Activity("a", MARA, "study", "  ", 60),
    lambda: Activity("a", MARA, "study", "Read", 0),
    lambda: Activity("a", MARA, "study", "Read", True),
    lambda: Pursue("p", "a", 60),
    lambda: Pursue("p", "a", Duration(60), effort_minutes=0),
    lambda: Pursue("p", "a", Duration(30, 60, note="Half an hour or so"), effort_minutes=61),
    lambda: Abandon("p", "a", note=7),
])
def test_malformed_requests_are_refused(build):
    with pytest.raises(DowntimeError):
        build()


def test_replay_round_trips_and_a_retry_returns_both_states_unchanged():
    state, calendar = empty("saltmarch"), clock()
    for operation in (Begin("b1", STUDY), Pursue("p1", "tide-cant", Duration(5 * DAY), note="Basics"),
                      Begin("b2", Activity("mend", MARA, "recovery", "Let the wrist heal", 3 * DAY)),
                      Abandon("a1", "mend", note="Back to work")):
        state, calendar, _, duplicate = apply(state, calendar, operation)
        assert not duplicate
    payload = state_to_payload(state)
    assert state_from_payload("saltmarch", payload, calendar) == state
    again, calendar_again, receipt, duplicate = apply(state, calendar, Pursue("p1", "tide-cant", Duration(5 * DAY),
                                                                             note="Basics"))
    assert duplicate and again is state and calendar_again is calendar and receipt == state.receipt("p1")
    with pytest.raises(DowntimeError) as other:
        state_from_payload("elsewhere", payload, calendar)
    assert other.value.reason == "campaign_mismatch"


def test_a_pursuit_needs_its_own_calendar_and_actor_of_this_campaign():
    state = empty("saltmarch")
    stranger = Activity("x", InstanceRef("elsewhere", "character", "mara"), "study", "Read", 60)
    with pytest.raises(DowntimeError) as foreign:
        apply(state, None, Begin("b", stranger))
    assert foreign.value.reason == "campaign_mismatch"
    state = apply(state, None, Begin("b", STUDY))[0]
    elsewhere = apply_calendar("elsewhere", None, Initialize("start", CalendarDefinition(), START))[0]
    with pytest.raises(DowntimeError) as mismatch:
        apply(state, elsewhere, Pursue("p", "tide-cant", Duration(DAY)))
    assert mismatch.value.reason == "campaign_mismatch"
    moved = apply_calendar("saltmarch", clock(), Advance("p", Duration(DAY)))[0]
    with pytest.raises(DowntimeError) as reused:
        apply(state, moved, Pursue("p", "tide-cant", Duration(DAY)))
    assert reused.value.reason == "operation_mismatch"
