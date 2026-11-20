"""A long activity spends campaign time once and records its progress in the same write (074)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from grimoire.calendars import Duration, Moment
from grimoire.calendarstore import CalendarLibrary
from grimoire.core.identity import InstanceRef
from grimoire.downtime import ABANDONED, ACTIVE, COMPLETE, Activity, DowntimeError
from grimoire.downtimestore import DowntimeLibrary
from grimoire.security import Authorizer
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite, WriteDenied
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts

from calendar_fixtures import seed_calendar_scope
from downtime_fixtures import DAY, MARA, OLD, OTHER, SCOPE, STUDY, begun, seed_actor, seed_downtime
from store_helpers import owner_handle

#: The scheduled meeting is 2024-03-01 23:30; the calendar starts 2024-02-20 23:30.
START = Moment(date(2024, 2, 20).toordinal(), 1410)


@pytest.fixture
def table(tmp_path):
    with LibraryStore.create(tmp_path / "library") as store:
        seed_downtime(store)
        yield store


def bytes_of(store, ref=SCOPE):
    return (store.root / "/".join(address_parts(ref))).read_bytes()


def rewrite(store, edit):
    """Hand-edit the scope payload, as another writer or a person might."""
    current = store.read(SCOPE)
    payload = thaw(current.record.payload)
    edit(payload)
    txn = store.transaction()
    txn.put(ScopeRecord(SCOPE, current.record.schema, payload, owner=current.record.owner,
                        extra=current.record.extra), current.etag)
    txn.commit(owner_handle())


def test_twenty_day_study_completes_and_advances_time_once_even_after_reopen(table):
    library = begun(table)
    old, other = bytes_of(table, OLD), bytes_of(table, OTHER)
    before = library.read(SCOPE)
    assert before.state.activity("tide-cant").status == ACTIVE and before.calendar.now == START
    unchanged = bytes_of(table)
    change = library.pursue(SCOPE, "study-twenty", "tide-cant", Duration(20 * DAY),
                            note="Mara can follow the tide cant at the quay", expected=before.etag)
    assert bytes_of(table) == unchanged
    assert set(change.preview()) == {"campaigns/saltmarch/scope.json"}
    receipt = change.receipt
    assert (receipt.credited, receipt.completed, receipt.triggered) == (20 * DAY, True, ("meeting",))
    after = change.commit()
    study = after.state.activity("tide-cant")
    assert (study.status, study.progress_minutes, study.settled_by) == (COMPLETE, 20 * DAY, "study-twenty")
    assert study.outcome == "Mara can follow the tide cant at the quay"
    assert after.calendar.now == START.advance(20 * DAY) == study.settled_at
    # The calendar's own reader sees the same single advance and deadline flag.
    clock = CalendarLibrary(table, owner_handle()).read(SCOPE).state
    assert clock.now == after.calendar.now and clock.deadlines[0].triggered_by == "study-twenty"
    assert bytes_of(table, OLD) == old and bytes_of(table, OTHER) == other
    later = CalendarLibrary(table, owner_handle()).advance(SCOPE, "later", Duration(15), expected=after.etag).commit()
    final = bytes_of(table)
    root = table.root
    table.close()
    with LibraryStore.open(root) as reopened:
        library = DowntimeLibrary(reopened, owner_handle())
        retry = library.pursue(SCOPE, "study-twenty", "tide-cant", Duration(20 * DAY),
                               note="Mara can follow the tide cant at the quay", expected=before.etag)
        assert retry.already_applied and retry.preview() == {} and retry.receipt == receipt
        current = retry.commit()
        assert current.calendar.now == later.state.now == START.advance(20 * DAY + 15)
        assert current.state.activity("tide-cant").progress_minutes == 20 * DAY
        assert bytes_of(reopened) == final and bytes_of(reopened, OLD) == old


def test_language_study_during_travel_credits_partial_effort_across_two_stretches(table):
    """The proving scenario: two hours a day of study on a road trip, resolved in two stretches."""
    library = begun(table, Activity("road-cant", MARA, "study", "Pick up the drovers' dialect", 40 * 60))
    view = library.read(SCOPE)
    first = library.pursue(SCOPE, "road-1", "road-cant", Duration(10 * DAY), effort_minutes=20 * 60,
                           note="Greetings and road signs", expected=view.etag)
    view = first.commit()
    halfway = view.state.activity("road-cant")
    assert (halfway.status, halfway.progress_minutes, halfway.remaining_minutes) == (ACTIVE, 1200, 1200)
    assert halfway.outcome == "" and first.receipt.completed is False
    # The second stretch runs longer than the effort that remains: progress stops at the requirement,
    # while the time the travel took still passes.
    second = library.pursue(SCOPE, "road-2", "road-cant", Duration(12 * DAY), effort_minutes=24 * 60,
                            note="Haggles at the fair", expected=view.etag)
    assert second.receipt.credited == 1200 and second.receipt.completed
    view = second.commit()
    done = view.state.activity("road-cant")
    assert (done.status, done.progress_minutes, done.outcome) == (COMPLETE, 2400, "Haggles at the fair")
    assert view.calendar.now == START.advance(22 * DAY)
    assert [r.operation.operation_id for r in view.calendar.receipts][-2:] == ["road-1", "road-2"]
    with pytest.raises(DowntimeError) as settled:
        library.pursue(SCOPE, "road-3", "road-cant", Duration(DAY), expected=view.etag)
    assert settled.value.reason == "activity_settled"


def test_an_estimated_stretch_needs_a_chosen_length_and_keeps_its_estimate(table):
    library = begun(table)
    view = library.read(SCOPE)
    data = bytes_of(table)
    with pytest.raises(DowntimeError) as unresolved:
        library.pursue(SCOPE, "rough", "tide-cant", Duration(5 * DAY, 9 * DAY, note="About a week"),
                       expected=view.etag)
    assert unresolved.value.reason == "unresolved_duration" and bytes_of(table) == data
    estimate = Duration(5 * DAY, 9 * DAY, 7 * DAY, "About a week")
    view = library.pursue(SCOPE, "rough", "tide-cant", estimate, expected=view.etag).commit()
    assert view.state.activity("tide-cant").progress_minutes == 7 * DAY
    assert view.calendar.receipt("rough").operation.duration == estimate


def test_refusals_change_no_bytes(table):
    library = begun(table)
    view = library.read(SCOPE)
    stranger = InstanceRef("saltmarch", "character", "nobody")
    elsewhere = InstanceRef("elsewhere", "character", "mara")
    cases = {
        "unknown_actor": lambda: library.begin(SCOPE, "b2", Activity("x", stranger, "study", "Read", 60),
                                               expected=view.etag),
        "campaign_mismatch": lambda: library.begin(SCOPE, "b3", Activity("y", elsewhere, "study", "Read", 60),
                                                   expected=view.etag),
        "duplicate_activity": lambda: library.begin(SCOPE, "b4", STUDY, expected=view.etag),
        "unknown_activity": lambda: library.pursue(SCOPE, "p1", "missing", Duration(DAY), expected=view.etag),
        "no_time": lambda: library.pursue(SCOPE, "p2", "tide-cant", Duration(0), expected=view.etag),
        "effort_exceeds_elapsed": lambda: library.pursue(SCOPE, "p3", "tide-cant", Duration(60), effort_minutes=61,
                                                         expected=view.etag),
        # 055's own operation ids: one already spent time, so reusing it would credit study for free.
        "operation_mismatch": lambda: library.pursue(SCOPE, "schedule", "tide-cant", Duration(DAY),
                                                     expected=view.etag),
    }
    data = bytes_of(table)
    for reason, call in cases.items():
        with pytest.raises(DowntimeError) as caught:
            call()
        assert caught.value.reason == reason, reason
        assert bytes_of(table) == data
    with pytest.raises(StaleWrite):
        library.pursue(SCOPE, "p4", "tide-cant", Duration(DAY), expected="stale")
    library.pursue(SCOPE, "p5", "tide-cant", Duration(DAY), expected=view.etag).commit()
    with pytest.raises(DowntimeError) as reused:
        library.pursue(SCOPE, "p5", "tide-cant", Duration(2 * DAY), expected=view.etag)
    assert reused.value.reason == "operation_mismatch"


def test_begin_needs_no_calendar_but_a_pursuit_does(tmp_path):
    with LibraryStore.create(tmp_path / "library") as store:
        seed_calendar_scope(store)
        seed_actor(store)
        library = begun(store)
        view = library.read(SCOPE)
        assert view.calendar is None and view.state.receipt("begin-tide-cant").before is None
        with pytest.raises(DowntimeError) as caught:
            library.pursue(SCOPE, "early", "tide-cant", Duration(DAY), expected=view.etag)
        assert caught.value.reason == "uninitialized"


def test_abandon_keeps_progress_and_spends_no_time(table):
    library = begun(table)
    view = library.pursue(SCOPE, "week", "tide-cant", Duration(7 * DAY), expected=library.read(SCOPE).etag).commit()
    change = library.abandon(SCOPE, "give-up", "tide-cant", note="Called home", expected=view.etag)
    view = change.commit()
    stopped = view.state.activity("tide-cant")
    assert (stopped.status, stopped.progress_minutes, stopped.outcome) == (ABANDONED, 7 * DAY, "Called home")
    assert stopped.settled_at == view.calendar.now == START.advance(7 * DAY)
    assert change.receipt.before == change.receipt.after == view.calendar.now
    with pytest.raises(DowntimeError) as caught:
        library.pursue(SCOPE, "resume", "tide-cant", Duration(DAY), expected=view.etag)
    assert caught.value.reason == "activity_settled"


def test_two_concurrent_pursuits_have_one_winner_and_a_calendar_edit_makes_a_plan_stale(table):
    library = begun(table)
    before = library.read(SCOPE)
    plans = [library.pursue(SCOPE, name, "tide-cant", Duration(3 * DAY), expected=before.etag) for name in "ab"]

    def commit(change):
        try:
            change.commit()
            return "applied"
        except StaleWrite:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, plans)) == ["applied", "stale"]
    current = library.read(SCOPE)
    assert current.calendar.now == START.advance(3 * DAY)
    assert current.state.activity("tide-cant").progress_minutes == 3 * DAY
    pending = library.pursue(SCOPE, "c", "tide-cant", Duration(DAY), expected=current.etag)
    clock = CalendarLibrary(table, owner_handle())
    moved = clock.advance(SCOPE, "nap", Duration(60), expected=current.etag).commit()
    with pytest.raises(StaleWrite):
        pending.commit()
    # A plain calendar write keeps the activities, and they still replay against the moved calendar.
    after = library.read(SCOPE)
    assert after.calendar == moved.state and after.state == current.state


def test_unauthorized_handle_cannot_commit(table):
    library = begun(table)
    before = library.read(SCOPE)
    unauthorized = Authorizer(["human:owner"]).handle_for("model:test", trusted=False)
    pending = DowntimeLibrary(table, unauthorized).pursue(SCOPE, "denied", "tide-cant", Duration(DAY),
                                                         expected=before.etag)
    with pytest.raises(WriteDenied):
        pending.commit()
    assert library.read(SCOPE) == before


@pytest.mark.parametrize("edit", [
    # Progress claimed without the study that earned it.
    lambda p: p["downtime"]["activities"][0].update(progress_minutes=5 * DAY),
    # Study kept, but the calendar's record of the time it spent renamed to an unrelated advance.
    lambda p: p["calendar"]["receipts"][-1]["operation"].update(operation_id="unrelated"),
    # A pursuit receipt that claims more effort than replay credits.
    lambda p: p["downtime"]["receipts"][-1].update(credited=1),
    # Unknown fields and schemas fail closed rather than drop a newer writer's rule.
    lambda p: p["downtime"].update(extra=True),
    lambda p: p["downtime"].update(schema="grimoire.downtime/9"),
    # An abandon dated to a time the calendar never reached.
    lambda p: p["downtime"]["receipts"].append({
        "operation": {"operation_id": "quit", "kind": "abandon", "activity_id": "tide-cant", "note": ""},
        "credited": 0, "completed": False, "before": {"fixed_day": 1, "minute": 0},
        "after": {"fixed_day": 1, "minute": 0}, "triggered": []}),
    # Two pursuits replayed out of the calendar's order.
    lambda p: p["downtime"]["receipts"].insert(1, p["downtime"]["receipts"].pop()),
])
def test_contradictory_histories_are_refused_without_writes(table, edit):
    library = begun(table)
    for name in ("one", "two"):
        library.pursue(SCOPE, name, "tide-cant", Duration(DAY), expected=library.read(SCOPE).etag).commit()
    rewrite(table, edit)
    data = bytes_of(table)
    with pytest.raises(ValueError):
        library.read(SCOPE)
    with pytest.raises(ValueError):
        library.pursue(SCOPE, "more", "tide-cant", Duration(DAY), expected="any")
    assert bytes_of(table) == data
