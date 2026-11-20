"""074 long activities in the campaign scope record, beside the 055 calendar they spend time from.

A pursuit's progress and its calendar advance are one write of one record: the native store's
exact-byte preview, authorization, compare-and-swap, journal and crash recovery cover both, so a
crash leaves either neither or both, and a retry of the same operation id returns the original
receipt without spending time or adding progress again. Like the calendar, the reader is
director-facing canonical data, never an actor's knowledge feed. No wall clock or model is used.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.calendars import CALENDAR_KEY, CalendarError, CalendarState, Duration
from grimoire.calendars import state_from_payload as calendar_from_payload
from grimoire.calendars import state_to_payload as calendar_to_payload
from grimoire.chats import scope_from_payload
from grimoire.core.identity import InstanceRef, ScopeRef, paired_instances
from grimoire.downtime import (
    DOWNTIME_KEY, Abandon, Activity, Begin, DowntimeError, DowntimeReceipt, DowntimeState, Pursue, apply, empty,
    state_from_payload, state_to_payload,
)
from grimoire.peersync.pairing import pairing_ended
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts
from grimoire.sync.turns import open_conflicts


@dataclass(frozen=True)
class DowntimeView:
    """The campaign's activities and the calendar they are measured against, at one etag."""
    scope: ScopeRef
    state: DowntimeState
    calendar: CalendarState | None
    etag: str


@dataclass(frozen=True)
class DowntimeChange:
    """An immutable plan. ``receipt`` describes this operation; ``commit`` returns the current view.

    On a retry, ``already_applied`` is true and the preview is empty. Replanning the same request
    after a crash or reopen behaves the same. An abandoned plan reserves nothing.
    """
    library: DowntimeLibrary = field(repr=False, compare=False)
    scope: ScopeRef
    receipt: DowntimeReceipt
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

    def commit(self, approvals: Mapping[str, str] | None = None) -> DowntimeView:
        library = self.library
        with library.store.lock:
            # Recheck even a no-write retry: a conflict or a replaced history may have arrived after
            # planning, and a receipt that is no longer retained cannot stand for current authority.
            view = library.read(self.scope)
            old = view.state.receipt(self.receipt.operation.operation_id)
            if old is not None:
                if old != self.receipt:
                    raise DowntimeError("operation receipt changed since planning", "operation_mismatch")
                return view
            if self.already_applied:
                raise DowntimeError("operation no longer belongs to the retained history", "history_changed")
            self._transaction().commit(library.handle, approvals=approvals)
            return library.read(self.scope)


class DowntimeLibrary:
    """Trusted application interface over an open V2 store, using its scoped apply handle.

    Reads create nothing. An activity begins only for an actor the campaign has, and a pursuit needs
    the campaign calendar to have been initialized (055) and an exact span of time.
    """
    def __init__(self, store: LibraryStore, handle: object) -> None:
        self.store = store
        self.handle = handle

    def _read(self, scope: ScopeRef) -> tuple[DowntimeView, ScopeRecord]:
        if type(scope) is not ScopeRef:
            raise DowntimeError("downtime scope must be a ScopeRef")
        path = "/".join(address_parts(scope))
        found, complete = open_conflicts(self.store, path, (path,),
                                         ended=lambda peer: pairing_ended(self.store, peer))
        if found or not complete:
            raise DowntimeError("campaign scope has unresolved or unreadable sync conflicts", "scope_conflict")
        stored = self.store.read(scope)
        record = stored.record
        scope_from_payload(scope, record.schema, record.payload)
        try:
            calendar = None if CALENDAR_KEY not in record.payload else calendar_from_payload(
                scope.campaign_id, record.payload[CALENDAR_KEY])
        except CalendarError as error:
            # Callers handle one refusal type; the calendar's reason (e.g. invalid_history) is kept.
            raise DowntimeError(f"campaign calendar is unreadable: {error}", error.reason) from None
        state = empty(scope.campaign_id) if DOWNTIME_KEY not in record.payload else state_from_payload(
            scope.campaign_id, record.payload[DOWNTIME_KEY], calendar)
        return DowntimeView(scope, state, calendar, stored.etag), record

    def read(self, scope: ScopeRef) -> DowntimeView:
        with self.store.lock:
            return self._read(scope)[0]

    def begin(self, scope: ScopeRef, operation_id: str, activity: Activity, *, expected: str) -> DowntimeChange:
        operation = Begin(operation_id, activity)
        with self.store.lock:
            view, record = self._read(scope)
            return self._plan(view, record, operation, expected)

    def pursue(self, scope: ScopeRef, operation_id: str, activity_id: str, elapsed: Duration, *,
               effort_minutes: int | None = None, note: str = "", expected: str) -> DowntimeChange:
        operation = Pursue(operation_id, activity_id, elapsed, effort_minutes, note)
        with self.store.lock:
            view, record = self._read(scope)
            return self._plan(view, record, operation, expected)

    def abandon(self, scope: ScopeRef, operation_id: str, activity_id: str, *, note: str = "",
                expected: str) -> DowntimeChange:
        with self.store.lock:
            view, record = self._read(scope)
            return self._plan(view, record, Abandon(operation_id, activity_id, note), expected)

    def _require_actor(self, actor: InstanceRef) -> None:
        present = set(self.store.campaign_instances(actor.campaign_id))
        # An instance id two kinds share is no one actor (IDENTITY.md); neither may study as it.
        if actor not in present or actor in paired_instances(present):
            raise DowntimeError("the campaign has no such actor", "unknown_actor")

    def _plan(self, view: DowntimeView, record: ScopeRecord, operation, expected: str) -> DowntimeChange:
        state, calendar, receipt, already_applied = apply(view.state, view.calendar, operation)
        if already_applied:
            return DowntimeChange(self, view.scope, receipt, True, None, view.etag)
        if isinstance(operation, Begin):
            self._require_actor(operation.activity.actor)
        if expected != view.etag:
            raise StaleWrite([("/".join(address_parts(view.scope)), "campaign changed; reread before planning")])
        payload = thaw(record.payload)
        payload[DOWNTIME_KEY] = state_to_payload(state)
        if calendar is not view.calendar:
            payload[CALENDAR_KEY] = calendar_to_payload(calendar)
        envelope = ScopeRecord(view.scope, record.schema, payload, owner=record.owner, extra=record.extra)
        change = DowntimeChange(self, view.scope, receipt, False, envelope, view.etag)
        # Preview enforces the native size/encoding limits before a plan is returned. Never evict an old
        # receipt to make a full record fit: a forgotten id would let an old retry study and travel twice.
        change.preview()
        return change
