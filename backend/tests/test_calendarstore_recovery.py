"""Crash every native write boundary, recover and retry one explicit time advancement (055)."""
import json

from grimoire.calendars import Duration, state_from_payload
from grimoire.calendarstore import CalendarLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, untouched

from calendar_fixtures import SCOPE, scheduled, seed_calendar_scope
from store_helpers import drop_empty_dirs, owner_handle

PATH = "lib/campaigns/saltmarch/scope.json"


def test_calendar_retry_survives_native_crashes_and_io_errors(tmp_path):
    locker = ProcessLocker()
    def setup(ops, root):
        with LibraryStore.create(root / "lib", ops=ops, locker=locker) as store:
            seed_calendar_scope(store)
            scheduled(store)

    def advance(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            library = CalendarLibrary(store, owner_handle())
            library.advance(SCOPE, "twenty", Duration(20 * 1440), expected=library.read(SCOPE).etag).commit()

    def exactly_once(outcome):
        before = state_from_payload(SCOPE.campaign_id, json.loads(outcome.before[PATH])["payload"]["calendar"])
        after = state_from_payload(SCOPE.campaign_id, json.loads(outcome.recovered[PATH])["payload"]["calendar"])
        if after.now != before.now.advance(20 * 1440):
            return "recovery/retry did not advance exactly twenty days"
        if len(after.receipts) != len(before.receipts) + 1 or after.deadlines[0].triggered_by != "twenty":
            return "recovery/retry duplicated or lost the deadline crossing"
        return None

    scenario = CrashScenario("calendar advance and same-id retry", setup, advance, advance,
                             (Invariant("exactly once", exactly_once), idempotent_recovery(),
                              no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
                              untouched("lib/campaigns/saltmarch/transcripts/*"), untouched("lib/campaigns/elsewhere/*")),
                             normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path, torn=False, errors=True)
    report.assert_ok()
    assert report.points_explored > 0
