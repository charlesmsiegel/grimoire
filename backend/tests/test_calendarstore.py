"""Explicit calendar operations commit once through the native authorized scope writer (055)."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from grimoire.calendars import CalendarDefinition, CalendarError, Duration, Moment
from grimoire.calendarstore import CalendarLibrary
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite, WriteDenied
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest

from calendar_fixtures import OLD, OTHER, SCOPE, initialized, scheduled, seed_calendar_scope
from chat_fixtures import chat_library
from store_helpers import owner_handle


@pytest.fixture
def table(tmp_path):
    with LibraryStore.create(tmp_path / "library") as store:
        seed_calendar_scope(store)
        yield store


def bytes_of(store, ref=SCOPE):
    return (store.root / "/".join(address_parts(ref))).read_bytes()


def test_twenty_day_proof_preserves_old_scene_and_retries_after_reopen(table):
    library = scheduled(table)
    old, other = bytes_of(table, OLD), bytes_of(table, OTHER)
    before = library.read(SCOPE)
    uncommitted = bytes_of(table)
    change = library.advance(SCOPE, "twenty-days", Duration(20 * 1440), expected=before.etag)
    assert bytes_of(table) == uncommitted and change.receipt.triggered == ("meeting",)
    preview = change.preview()
    assert set(preview) == {"campaigns/saltmarch/scope.json"}
    after = change.commit()
    assert after.state.now == Moment(date(2024, 3, 11).toordinal(), 1410)
    assert after.state.deadlines[0].triggered_by == "twenty-days"
    assert bytes_of(table, OLD) == old and bytes_of(table, OTHER) == other
    library.advance(SCOPE, "later", Duration(15), expected=after.etag).commit()
    final_bytes = bytes_of(table)
    root = table.root
    table.close()
    with LibraryStore.open(root) as reopened:
        library = CalendarLibrary(reopened, owner_handle())
        retry = library.advance(SCOPE, "twenty-days", Duration(20 * 1440), expected=before.etag)
        assert retry.already_applied and retry.preview() == {}
        assert retry.receipt == change.receipt
        assert retry.commit().state.now == after.state.now.advance(15)
        assert bytes_of(reopened) == final_bytes
        assert bytes_of(reopened, OLD) == old


def test_read_and_abandoned_plan_never_initialize_or_advance(table):
    original = bytes_of(table)
    library = CalendarLibrary(table, owner_handle())
    view = library.read(SCOPE)
    assert view.state is None
    library.initialize(SCOPE, "start", CalendarDefinition(), "2024-02-20", expected=view.etag)
    assert bytes_of(table) == original
    with pytest.raises(CalendarError, match="initialized"):
        library.advance(SCOPE, "move", Duration(1), expected=view.etag)


def test_scope_fields_survive_and_normal_scope_edit_keeps_calendar(table):
    library = initialized(table)
    before = library.read(SCOPE)
    pending = library.advance(SCOPE, "stale", Duration(1), expected=before.etag)
    chat_library(table).name_scope(SCOPE, "A new title", expected=before.etag).commit()
    with pytest.raises(StaleWrite):
        pending.commit()
    assert library.read(SCOPE).state == before.state
    record = table.read(SCOPE).record
    assert record.payload["x-notes"]["keep"] is True and record.extra["x-editor"] == "keep"
    assert record.payload["title"] == "A new title"


def test_two_concurrent_plans_have_one_winner_and_retry_is_read_only(table):
    library = initialized(table)
    before = library.read(SCOPE)
    changes = [library.advance(SCOPE, name, Duration(10), expected=before.etag) for name in ("a", "b")]
    def commit(change):
        try:
            change.commit()
            return "applied"
        except StaleWrite:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, changes)) == ["applied", "stale"]
    current = library.read(SCOPE)
    assert current.state.now == before.state.now.advance(10)
    winner = current.state.receipts[-1].operation.operation_id
    data = bytes_of(table)
    retry = library.advance(SCOPE, winner, Duration(10), expected=before.etag)
    retry.commit()
    assert bytes_of(table) == data
    with pytest.raises(CalendarError, match="different request"):
        library.advance(SCOPE, winner, Duration(11), expected=before.etag)


def test_unauthorized_handle_cannot_commit(table):
    library = initialized(table)
    before = library.read(SCOPE)
    unauthorized = Authorizer(["human:owner"]).handle_for("model:test", trusted=False)
    pending = CalendarLibrary(table, unauthorized).advance(SCOPE, "denied", Duration(10), expected=before.etag)
    with pytest.raises(WriteDenied):
        pending.commit()
    assert library.read(SCOPE) == before


def test_calendar_and_scope_errors_preserve_bytes(table):
    library = initialized(table)
    before = library.read(SCOPE)
    data = bytes_of(table)
    for call in (lambda: library.advance(SCOPE, "estimate", Duration(10, 30, note="About twenty"), expected=before.etag),
                 lambda: library.schedule(SCOPE, "past", "past", "2024-01-01", label="Past", expected=before.etag),
                 lambda: library.initialize(SCOPE, "different", CalendarDefinition(), "2024-01-01", expected=before.etag)):
        with pytest.raises(CalendarError):
            call()
        assert bytes_of(table) == data
    estimated = Duration(10, 30, 20, "About twenty")
    library.advance(SCOPE, "estimate", estimated, expected=before.etag).commit()
    assert library.read(SCOPE).state.receipts[-1].operation.duration == estimated
    current = table.read(SCOPE)
    payload = thaw(current.record.payload)
    payload["calendar"]["now"]["minute"] = 0
    txn = table.transaction()
    txn.put(ScopeRecord(SCOPE, current.record.schema, payload), current.etag)
    txn.commit(owner_handle())
    malformed = bytes_of(table)
    with pytest.raises(CalendarError):
        library.read(SCOPE)
    assert bytes_of(table) == malformed


def test_initialization_and_schedule_retry_aliases_and_immutable_preview(table):
    library = initialized(table)
    view = library.read(SCOPE)
    retry = library.initialize(SCOPE, "start", CalendarDefinition(), "2024-2-20", minute=1410, expected="old")
    assert retry.already_applied and retry.commit() == view
    change = library.schedule(SCOPE, "schedule", "meeting", "2024-03-01", label="Meet", expected=view.etag)
    preview = change.preview()
    preview.clear()
    with pytest.raises(FrozenInstanceError):
        change.receipt = None
    committed = change.commit()
    retry = library.schedule(SCOPE, "schedule", "meeting", "2024-3-1", label="Meet", expected=view.etag)
    assert retry.already_applied and retry.commit() == committed
    with pytest.raises(CalendarError, match="different request"):
        library.schedule(SCOPE, "schedule", "meeting", "2024-3-1", label="Changed", expected=view.etag)


def test_exact_preview_approval_is_required_and_consumed(table):
    library = initialized(table)
    before = library.read(SCOPE)
    authority = Authorizer(["human:owner"])
    authority.add_grant(Grant("calendar", "app:calendar", frozenset({Permission.APPLY}),
                              ResourcePath.parse("campaigns/saltmarch"), "human:owner"))
    caller = CalendarLibrary(table, authority.handle_for("app:calendar"))
    change = caller.advance(SCOPE, "approved", Duration(1), expected=before.etag)
    path, data = next(iter(change.preview().items()))
    with pytest.raises(WriteDenied):
        change.commit()
    authority.register_approval(Approval("a1", "app:calendar", Permission.APPLY,
                                         ResourcePath.parse(path), payload_digest(data), "human:owner"))
    after = change.commit({path: "a1"})
    assert after.state.now == before.state.now.advance(1)
    assert authority.pending_approvals() == ()
    # Retrying completion never consumes another approval or advances again.
    assert change.commit() == after


def test_unsupported_scope_schema_cannot_be_overwritten(table):
    library = initialized(table)
    old = table.read(SCOPE)
    txn = table.transaction()
    txn.put(ScopeRecord(SCOPE, "future/scope", old.record.payload), old.etag)
    txn.commit(owner_handle())
    before = bytes_of(table)
    with pytest.raises(ValueError):
        library.advance(SCOPE, "move", Duration(1), expected=old.etag)
    assert bytes_of(table) == before
