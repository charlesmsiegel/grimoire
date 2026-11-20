"""Crash every native write boundary of a study's pursuit, recover and retry it once (074)."""
import json

from grimoire.calendars import Duration
from grimoire.calendars import state_from_payload as calendar_from_payload
from grimoire.downtime import COMPLETE, state_from_payload
from grimoire.downtimestore import DowntimeLibrary
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import CrashScenario, Invariant, explore_crashes, idempotent_recovery, no_residue, untouched

from downtime_fixtures import DAY, SCOPE, begun, seed_downtime
from store_helpers import drop_empty_dirs, owner_handle

PATH = "lib/campaigns/saltmarch/scope.json"


def _states(data: bytes):
    payload = json.loads(data)["payload"]
    calendar = calendar_from_payload(SCOPE.campaign_id, payload["calendar"])
    return calendar, state_from_payload(SCOPE.campaign_id, payload["downtime"], calendar)


def test_study_pursuit_survives_native_crashes_and_io_errors(tmp_path):
    locker = ProcessLocker()

    def setup(ops, root):
        with LibraryStore.create(root / "lib", ops=ops, locker=locker) as store:
            seed_downtime(store)
            begun(store)

    def pursue(ops, root):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            library = DowntimeLibrary(store, owner_handle())
            library.pursue(SCOPE, "study", "tide-cant", Duration(20 * DAY), note="Fluent",
                           expected=library.read(SCOPE).etag).commit()

    def exactly_once(outcome):
        before_clock, _ = _states(outcome.before[PATH])
        clock, downtime = _states(outcome.recovered[PATH])
        study = downtime.activity("tide-cant")
        if clock.now != before_clock.now.advance(20 * DAY) or len(clock.receipts) != len(before_clock.receipts) + 1:
            return "recovery/retry did not spend exactly twenty days once"
        if (study.status, study.progress_minutes, study.outcome) != (COMPLETE, 20 * DAY, "Fluent"):
            return "recovery/retry lost or duplicated the study's progress"
        return None

    scenario = CrashScenario("study pursuit and same-id retry", setup, pursue, pursue,
                             (Invariant("exactly once", exactly_once), idempotent_recovery(),
                              no_residue("lib/.txn/*.new", "lib/.txn/journal.json"),
                              untouched("lib/campaigns/saltmarch/transcripts/*"), untouched("lib/campaigns/elsewhere/*")),
                             normalize=drop_empty_dirs)
    report = explore_crashes(scenario, tmp_path, torn=False, errors=True)
    report.assert_ok()
    assert report.points_explored > 0
