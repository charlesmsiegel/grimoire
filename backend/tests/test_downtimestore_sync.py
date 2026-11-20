"""Study done on two disconnected devices is never summed; it waits for an explicit resolution (074)."""
import pytest

from grimoire.calendars import Duration
from grimoire.downtime import DowntimeError
from grimoire.downtimestore import DowntimeLibrary
from grimoire.store import etag
from grimoire.sync import resolve, sync

from downtime_fixtures import DAY, SCOPE, begun, seed_downtime
from sync_helpers import DESKTOP, PHONE, handle, handles, make_pair

PATH = "campaigns/saltmarch/scope.json"


def test_divergent_study_waits_for_resolution_and_keeps_one_history(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        seed_downtime(desktop)
        left = begun(desktop)
        sync(desktop, phone, handles())
        right = DowntimeLibrary(phone, handle())
        start = left.read(SCOPE)
        left.pursue(SCOPE, "desk", "tide-cant", Duration(12 * DAY), expected=start.etag).commit()
        right.pursue(SCOPE, "road", "tide-cant", Duration(8 * DAY), expected=right.read(SCOPE).etag).commit()
        discarded = right.pursue(SCOPE, "road", "tide-cant", Duration(8 * DAY), expected=start.etag)
        assert discarded.already_applied
        versions = {DESKTOP: (desktop.root / PATH).read_bytes(), PHONE: (phone.root / PATH).read_bytes()}
        assert sync(desktop, phone, handles()).conflict(PATH).code == "concurrent"
        for call in (lambda: left.read(SCOPE), lambda: right.read(SCOPE),
                     lambda: left.pursue(SCOPE, "more", "tide-cant", Duration(DAY), expected=start.etag)):
            with pytest.raises(DowntimeError) as caught:
                call()
            assert caught.value.reason == "scope_conflict"
        resolve(desktop, phone, PATH, etag(versions[DESKTOP]),
                {device: etag(data) for device, data in versions.items()}, handles())
        with pytest.raises(DowntimeError) as lost:
            discarded.commit()
        assert lost.value.reason == "history_changed"
        for library in (left, right):
            view = library.read(SCOPE)
            # Twelve days of study, not twenty: the phone's eight were discarded with its history.
            assert view.state.activity("tide-cant").progress_minutes == 12 * DAY
            assert view.calendar.now == start.calendar.now.advance(12 * DAY)
            assert view.state.receipt("road") is None
    finally:
        desktop.close()
        phone.close()
