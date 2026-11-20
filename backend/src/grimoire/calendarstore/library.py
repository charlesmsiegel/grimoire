"""055 calendar authority in one native campaign scope record.

Only explicitly committed changes advance time. Exact-byte preview, authorization, compare-and-
swap and crash recovery are the native store's; a receipt and every deadline flag land together.
The reader is director-facing, never an actor's knowledge feed. No wall clock or model is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping

from grimoire.calendars import (
    CALENDAR_KEY, Advance, CalendarDefinition, CalendarError, CalendarState, Deadline, Duration,
    Initialize, Moment, Receipt, Schedule, apply, state_from_payload, state_to_payload,
)
from grimoire.chats import scope_from_payload
from grimoire.core.identity import ScopeRef
from grimoire.peersync.pairing import pairing_ended
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts
from grimoire.sync.turns import open_conflicts


@dataclass(frozen=True)
class CalendarView:
    scope: ScopeRef
    state: CalendarState | None
    etag: str


@dataclass(frozen=True)
class CalendarChange:
    """An immutable plan. Receipt describes this operation; commit returns the campaign's current view.

    On a retry, ``already_applied`` is true and preview is empty. Reopening/replanning the same
    request after a crash has the same behavior. An abandoned plan does not reserve its operation id.
    """
    library: CalendarLibrary = field(repr=False, compare=False)
    scope: ScopeRef
    receipt: Receipt
    already_applied: bool
    _envelope: ScopeRecord | None = field(repr=False)
    _expected: str = field(repr=False)

    def _transaction(self):
        txn = self.library.store.transaction()
        if self._envelope is not None:
            txn.put(self._envelope, self._expected)
        return txn

    def preview(self) -> dict[str, bytes]:
        return {} if self.already_applied else self._transaction().preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> CalendarView:
        library = self.library
        with library.store.lock:
            # Recheck even a no-write retry: a conflict or explicit history replacement may have
            # arrived after planning. A historical receipt cannot masquerade as current authority.
            view = library.read(self.scope)
            old = None if view.state is None else view.state.receipt(self.receipt.operation.operation_id)
            if old is not None:
                if old != self.receipt:
                    raise CalendarError("operation receipt changed since planning", "operation_mismatch")
                return view
            if self.already_applied:
                raise CalendarError("operation no longer belongs to the retained history", "history_changed")
            self._transaction().commit(library.handle, approvals=approvals)
            return library.read(self.scope)


class CalendarLibrary:
    """Trusted application interface over an already open V2 store, using its scoped apply handle.

    The public view is canonical/director data. Future actor-facing consumers must separately
    establish perception. Reads create nothing and refuse ambiguity instead of guessing a time.
    """
    def __init__(self, store: LibraryStore, handle: object) -> None:
        self.store = store
        self.handle = handle

    def _read(self, scope: ScopeRef):
        if type(scope) is not ScopeRef:
            raise CalendarError("calendar scope must be a ScopeRef")
        path = "/".join(address_parts(scope))
        found, complete = open_conflicts(self.store, path, (path,),
                                         ended=lambda peer: pairing_ended(self.store, peer))
        if found or not complete:
            raise CalendarError("campaign scope has unresolved or unreadable sync conflicts", "scope_conflict")
        stored = self.store.read(scope)
        record = stored.record
        scope_from_payload(scope, record.schema, record.payload)
        state = None if CALENDAR_KEY not in record.payload else state_from_payload(scope.campaign_id,
                                                                                  record.payload[CALENDAR_KEY])
        return CalendarView(scope, state, stored.etag), record

    def read(self, scope: ScopeRef) -> CalendarView:
        with self.store.lock:
            return self._read(scope)[0]

    def initialize(self, scope: ScopeRef, operation_id: str, calendar: CalendarDefinition,
                   native_date: str, *, minute: int = 0, expected: str) -> CalendarChange:
        if type(calendar) is not CalendarDefinition:
            raise CalendarError("initialization requires a CalendarDefinition")
        operation = Initialize(operation_id, calendar, Moment(calendar.parse(native_date), minute))
        with self.store.lock:
            view, record = self._read(scope)
            return self._plan(view, record, operation, expected)

    def schedule(self, scope: ScopeRef, operation_id: str, deadline_id: str, due_native: str, *,
                 minute: int = 0, label: str, kind: str = "deadline", expected: str) -> CalendarChange:
        with self.store.lock:
            view, record = self._read(scope)
            if view.state is None:
                raise CalendarError("campaign calendar is not initialized", "uninitialized")
            due = Moment(view.state.calendar.parse(due_native), minute)
            return self._plan(view, record, Schedule(operation_id, Deadline(deadline_id, due, label, kind)), expected)

    def advance(self, scope: ScopeRef, operation_id: str, duration: Duration, *, expected: str) -> CalendarChange:
        with self.store.lock:
            view, record = self._read(scope)
            return self._plan(view, record, Advance(operation_id, duration), expected)

    def _plan(self, view: CalendarView, record: ScopeRecord, operation, expected: str) -> CalendarChange:
        state, receipt, already_applied = apply(view.scope.campaign_id, view.state, operation)
        if already_applied:
            return CalendarChange(self, view.scope, receipt, True, None, view.etag)
        if expected != view.etag:
            raise StaleWrite([("/".join(address_parts(view.scope)), "campaign changed; reread before planning")])
        payload = thaw(record.payload)
        payload[CALENDAR_KEY] = state_to_payload(state)
        envelope = ScopeRecord(view.scope, record.schema, payload, owner=record.owner, extra=record.extra)
        change = CalendarChange(self, view.scope, receipt, False, envelope, view.etag)
        # Preview enforces native size/encoding limits before returning a plan. Never evict a receipt
        # to make an overfull record fit: forgotten ids would make an old retry advance twice.
        change.preview()
        return change
