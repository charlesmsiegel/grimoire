"""Synthetic fixed-day chronology, explicit uncertainty and replay contracts (055)."""
from dataclasses import replace
from datetime import date

import pytest

from grimoire.calendars import (
    Advance, CalendarDefinition, CalendarError, Deadline, Duration, Initialize, Moment, Month,
    Schedule, apply, state_from_payload, state_to_payload,
)


def test_gregorian_and_hebrew_share_fixed_days_and_keep_leap_months():
    gregorian = CalendarDefinition()
    hebrew = CalendarDefinition("hebrew")
    assert gregorian.format(gregorian.parse("2024-02-28") + 2) == "2024-03-01"
    assert hebrew.parse("5784-Adar1-01") == gregorian.parse("2024-02-10")
    assert hebrew.format(hebrew.parse("5784-Adar1-30") + 1) == "5784-Adar2-01"
    assert hebrew.parse("5784-Adar-01") == hebrew.parse("5784-Adar2-01")
    assert hebrew.format(hebrew.parse("5785-Adar-29") + 1) == "5785-Nisan-01"
    for calendar, native in ((gregorian, "2023-02-29"), (hebrew, "5785-Adar1-01"),
                             (hebrew, "5784-Adar2-30"), (gregorian, True)):
        with pytest.raises(CalendarError):
            calendar.parse(native)


def test_portable_custom_calendar_crosses_years_and_honors_anchor():
    months = [Month("First", 10), Month("Last", 5)]
    custom = CalendarDefinition("custom", "synthetic", months, date(2024, 1, 1).toordinal())
    months.append(Month("Ignored", 3))
    assert custom.parse("1-First-01") == date(2024, 1, 1).toordinal()
    assert custom.format(custom.parse("1-Last-05") + 1) == "2-First-01"
    assert custom.parse("2-Last-05") - custom.parse("1-First-01") == 29
    for factory in (lambda: CalendarDefinition("missing"),
                    lambda: CalendarDefinition("custom", "test", (Month("M", 1), Month("M", 2))),
                    lambda: CalendarDefinition("custom", "test", (), 0),
                    lambda: Month("Bad-Name", 10), lambda: Month("M", True),
                    lambda: custom.parse("0-First-01"), lambda: custom.format(custom.epoch_fixed - 1)):
        with pytest.raises(CalendarError):
            factory()


def test_moments_roll_minutes_and_uncertainty_needs_explicit_choice():
    now = Moment(date(2024, 2, 29).toordinal(), 1430)
    assert now.advance(20) == Moment(date(2024, 3, 1).toordinal(), 10)
    assert now < now.advance(1)
    assert Duration(20).exact_minutes == 20
    estimate = Duration(10, 30, note="About twenty minutes")
    with pytest.raises(CalendarError, match="selected"):
        _ = estimate.exact_minutes
    assert replace(estimate, selected=20).exact_minutes == 20
    for factory in (lambda: Duration(-1), lambda: Duration(True), lambda: Duration(1.2),
                    lambda: Duration(20, 10), lambda: Duration(1, 3, 4),
                    lambda: Moment(0), lambda: Moment(True), lambda: Moment(1, 1440),
                    lambda: Moment(3652059, 1439).advance(1)):
        with pytest.raises(CalendarError):
            factory()


def state():
    start = Moment(date(2024, 2, 20).toordinal(), 1410)
    value, _, _ = apply("saltmarch", None, Initialize("start", CalendarDefinition(), start))
    value, _, _ = apply("saltmarch", value, Schedule("schedule", Deadline("meeting", start.advance(10 * 1440),
                                                                                 "Meet at the gate", "reminder")))
    return value


def test_advance_flags_once_and_retry_returns_original_receipt_after_later_operation():
    before = state()
    operation = Advance("study", Duration(20 * 1440))
    after, receipt, replay = apply("saltmarch", before, operation)
    assert not replay and receipt.triggered == ("meeting",)
    assert after.now == before.now.advance(20 * 1440)
    assert after.deadlines[0].triggered_by == "study"
    later, _, _ = apply("saltmarch", after, Advance("later", Duration(5)))
    retried, original, replay = apply("saltmarch", later, operation)
    assert replay and retried == later and original == receipt
    assert original.after != retried.now
    assert state_from_payload("saltmarch", state_to_payload(later)) == later
    with pytest.raises(CalendarError, match="operation"):
        apply("saltmarch", later, Advance("study", Duration(1)))


@pytest.mark.parametrize("change", [
    lambda p: p.update(schema="future"), lambda p: p.update(campaign_id="other"),
    lambda p: p["now"].update(minute=0), lambda p: p.update(unknown=True),
    lambda p: p["receipts"].append(p["receipts"][-1]),
    lambda p: p["deadlines"][0].update(triggered_by="imagined"),
    lambda p: p["receipts"][0]["operation"].update(unknown=True),
])
def test_malformed_or_inconsistent_calendar_is_never_silently_repaired(change):
    payload = state_to_payload(state())
    change(payload)
    with pytest.raises(CalendarError):
        state_from_payload("saltmarch", payload)


def test_past_deadlines_duplicate_ids_and_pretriggered_deadlines_refused():
    value = state()
    for deadline in (Deadline("past", value.now, "Past", "deadline"),
                     Deadline("meeting", value.now.advance(1), "Duplicate", "deadline"),
                     Deadline("false", value.now.advance(1), "Already triggered", "trigger", "forged")):
        with pytest.raises(CalendarError):
            apply("saltmarch", value, Schedule("new", deadline))
    with pytest.raises(CalendarError):
        apply("other", value, Advance("new", Duration(1)))
    unchanged, receipt, _ = apply("saltmarch", value, Advance("zero", Duration(0)))
    assert unchanged.now == value.now and receipt.triggered == ()
