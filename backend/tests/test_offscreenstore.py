"""The rival's plan lands beside the calendar in the native scope record, once per skip (076)."""
import json

import pytest

from grimoire.calendars import CalendarError, Duration
from grimoire.calendarstore import CalendarLibrary
from grimoire.offscreen import Development, OffscreenError
from grimoire.offscreenstore import OffscreenLibrary
from grimoire.store import LibraryStore, ProcessLocker, StaleWrite
from grimoire.store.layout import address_parts
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, untouched

from calendar_fixtures import OLD, OTHER, SCOPE, initialized, seed_calendar_scope
from offscreen_fixtures import DAY, decided_guild, rival_plan
from store_helpers import drop_empty_dirs, owner_handle

PATH = "lib/campaigns/saltmarch/scope.json"


@pytest.fixture
def table(tmp_path):
    with LibraryStore.create(tmp_path / "library") as store:
        seed_calendar_scope(store)
        yield store


def bytes_of(store, ref=SCOPE):
    return (store.root / "/".join(address_parts(ref))).read_bytes()


def planned(store):
    clock = initialized(store)
    clock.schedule(SCOPE, "schedule-festival", "festival", "2024-03-12", minute=600, label="Harbour festival",
                   expected=clock.read(SCOPE).etag).commit()
    offscreen = OffscreenLibrary(store, owner_handle())
    offscreen.adopt(SCOPE, "adopt-docks", rival_plan(), institution=decided_guild(),
                    expected=offscreen.read(SCOPE).etag).commit()
    return clock, offscreen


def test_twenty_day_trip_advances_the_rival_plan_once_and_survives_reopen(table):
    clock, offscreen = planned(table)
    old, other = bytes_of(table, OLD), bytes_of(table, OTHER)
    clock.advance(SCOPE, "travel", Duration(20 * DAY), expected=clock.read(SCOPE).etag).commit()
    view = offscreen.read(SCOPE)
    assert view.pending == ("travel",) and view.state.progress("docks").completed == 0
    before = bytes_of(table)
    change = offscreen.advance(SCOPE, "travel", budget=4, expected=view.etag)
    assert bytes_of(table) == before  # planning writes nothing
    assert set(change.preview()) == {"campaigns/saltmarch/scope.json"}
    start = view.state.as_of
    assert change.receipt.developments == (Development("docks", "coin", start.advance(5 * DAY)),
                                           Development("docks", "magistrate", start.advance(12 * DAY)))
    after = change.commit()
    assert after.pending == () and after.state.progress("docks").completed == 2
    assert after.state.as_of == after.calendar.now
    assert bytes_of(table, OLD) == old and bytes_of(table, OTHER) == other
    # The calendar keeps writing its own extension without disturbing the plans.
    clock.advance(SCOPE, "later", Duration(15), expected=after.etag).commit()
    record = table.read(SCOPE).record
    assert record.payload["x-notes"]["keep"] is True and record.extra["x-editor"] == "keep"
    final = bytes_of(table)
    root = table.root
    table.close()
    with LibraryStore.open(root) as reopened:
        library = OffscreenLibrary(reopened, owner_handle())
        retry = library.advance(SCOPE, "travel", budget=4, expected=view.etag)
        assert retry.already_applied and retry.preview() == {} and retry.receipt == change.receipt
        assert retry.commit().pending == ("later",)
        assert bytes_of(reopened) == final


def test_stale_plans_wrong_order_and_uninitialized_calendars_write_nothing(table):
    offscreen = OffscreenLibrary(table, owner_handle())
    view = offscreen.read(SCOPE)
    assert view.calendar is None and view.state is None and view.pending == ()
    with pytest.raises(OffscreenError, match="uninitialized"):
        offscreen.adopt(SCOPE, "adopt", rival_plan(deadline_id=None), institution=decided_guild(), expected=view.etag)
    clock, offscreen = planned(table)
    clock.advance(SCOPE, "a", Duration(DAY), expected=clock.read(SCOPE).etag).commit()
    clock.advance(SCOPE, "b", Duration(DAY), expected=clock.read(SCOPE).etag).commit()
    original = bytes_of(table)
    view = offscreen.read(SCOPE)
    with pytest.raises(OffscreenError, match="skip_out_of_order"):
        offscreen.advance(SCOPE, "b", budget=1, expected=view.etag)
    pending = offscreen.advance(SCOPE, "a", budget=1, expected=view.etag)
    clock.advance(SCOPE, "c", Duration(1), expected=view.etag).commit()
    with pytest.raises(StaleWrite):
        pending.commit()
    with pytest.raises(StaleWrite):
        offscreen.advance(SCOPE, "a", budget=1, expected=view.etag)
    assert offscreen.read(SCOPE).state == view.state
    assert json.loads(bytes_of(table))["payload"]["offscreen"] == json.loads(original)["payload"]["offscreen"]


def test_a_tampered_receipt_fails_closed_for_readers_and_writers(table):
    clock, offscreen = planned(table)
    clock.advance(SCOPE, "travel", Duration(20 * DAY), expected=clock.read(SCOPE).etag).commit()
    offscreen.advance(SCOPE, "travel", budget=4, expected=offscreen.read(SCOPE).etag).commit()
    path = table.root / "campaigns/saltmarch/scope.json"
    document = json.loads(path.read_bytes())
    document["payload"]["offscreen"]["receipts"][1]["developments"].pop()
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(OffscreenError, match="invalid_history"):
        offscreen.read(SCOPE)
    document["payload"]["calendar"]["now"]["minute"] += 1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(OffscreenError, match="calendar_mismatch"):
        offscreen.read(SCOPE)
    with pytest.raises(CalendarError):
        CalendarLibrary(table, owner_handle()).read(SCOPE)


def test_offscreen_skip_survives_native_crashes_and_io_errors(tmp_path):
    locker = ProcessLocker()

    def setup(ops, root):
        with LibraryStore.create(root / "lib", ops=ops, locker=locker) as store:
            seed_calendar_scope(store)
            clock, _ = planned(store)
            clock.advance(SCOPE, "travel", Duration(20 * DAY), expected=clock.read(SCOPE).etag).commit()

    def advance(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            library = OffscreenLibrary(store, owner_handle())
            library.advance(SCOPE, "travel", budget=4, expected=library.read(SCOPE).etag).commit()

    def exactly_once(outcome):
        before = json.loads(outcome.before[PATH])["payload"]["offscreen"]["receipts"]
        after = json.loads(outcome.recovered[PATH])["payload"]["offscreen"]["receipts"]
        if len(after) != len(before) + 1 or len(after[-1]["developments"]) != 2:
            return "recovery/retry duplicated or lost the skip"
        return None

    scenario = CrashScenario("offscreen skip and same-id retry", setup, advance, advance,
                             (Invariant("exactly once", exactly_once), idempotent_recovery(),
                              no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
                              untouched("lib/campaigns/saltmarch/transcripts/*"), untouched("lib/campaigns/elsewhere/*")),
                             normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path, torn=False, errors=True)
    report.assert_ok()
    assert report.points_explored > 0
