"""A campaign has no unambiguous present while two devices disagree (055)."""
import pytest

from grimoire.calendars import CalendarError, Duration
from grimoire.calendarstore import CalendarLibrary
from grimoire.store import etag
from grimoire.sync import resolve, sync

from calendar_fixtures import SCOPE, scheduled, seed_calendar_scope
from sync_helpers import DESKTOP, PHONE, handles, handle, kept, make_pair

PATH = "campaigns/saltmarch/scope.json"


def test_divergent_clocks_wait_for_explicit_resolution_and_do_not_sum(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        seed_calendar_scope(desktop)
        left = scheduled(desktop)
        sync(desktop, phone, handles())
        right = CalendarLibrary(phone, handle())
        start = left.read(SCOPE)
        pending = left.advance(SCOPE, "prepared", Duration(1), expected=start.etag)
        left.advance(SCOPE, "twenty", Duration(20 * 1440), expected=start.etag).commit()
        right.advance(SCOPE, "five", Duration(5 * 1440), expected=right.read(SCOPE).etag).commit()
        discarded_retry = right.advance(SCOPE, "five", Duration(5 * 1440), expected=start.etag)
        assert discarded_retry.already_applied
        versions = {DESKTOP: (desktop.root / PATH).read_bytes(), PHONE: (phone.root / PATH).read_bytes()}
        report = sync(desktop, phone, handles())
        assert report.conflict(PATH).code == "concurrent"
        assert versions[PHONE] in kept(desktop) and versions[DESKTOP] in kept(phone)
        for call in (lambda: left.read(SCOPE), lambda: right.read(SCOPE), pending.commit,
                     lambda: left.advance(SCOPE, "fresh", Duration(1), expected=start.etag)):
            with pytest.raises(CalendarError) as caught:
                call()
            assert caught.value.reason == "scope_conflict"
        resolve(desktop, phone, PATH, etag(versions[DESKTOP]),
                {device: etag(data) for device, data in versions.items()}, handles())
        with pytest.raises(CalendarError) as lost:
            discarded_retry.commit()
        assert lost.value.reason == "history_changed"
        for library in (left, right):
            view = library.read(SCOPE)
            assert view.state.now == start.state.now.advance(20 * 1440)
            assert view.state.receipt("five") is None
            assert view.state.deadlines[0].triggered_by == "twenty"
            retry = library.advance(SCOPE, "twenty", Duration(20 * 1440), expected=start.etag)
            assert retry.already_applied and retry.commit() == view
    finally:
        desktop.close()
        phone.close()


def test_missing_conflict_metadata_blocks_authority_without_touching_scope(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        seed_calendar_scope(desktop)
        left = scheduled(desktop)
        sync(desktop, phone, handles())
        start = left.read(SCOPE)
        change = left.advance(SCOPE, "prepared", Duration(1), expected=start.etag)
        (desktop.root / ".sync" / "peers" / PHONE / "keeps.json").unlink()
        before = (desktop.root / PATH).read_bytes()
        for call in (lambda: left.read(SCOPE), change.commit):
            with pytest.raises(CalendarError) as caught:
                call()
            assert caught.value.reason == "scope_conflict"
        assert (desktop.root / PATH).read_bytes() == before
    finally:
        desktop.close()
        phone.close()
