"""076 offscreen plan authority in the campaign's native scope record, beside its 055 calendar.

Plans and skip receipts live in one ``offscreen`` extension of ``campaigns/<id>/scope.json``. A skip
names a calendar advance that is already committed, so the calendar and the plans never need a
two-record transaction: until its skip is applied, an advance is listed in ``pending``, and applying
it later flags exactly the steps it crossed. Exact-byte preview, authorization, compare-and-swap and
crash recovery are the native store's. The reader is director-facing, never an actor's knowledge.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.calendars import CALENDAR_KEY, CalendarError, CalendarState
from grimoire.calendars import state_from_payload as calendar_from_payload
from grimoire.chats import scope_from_payload
from grimoire.core.identity import ScopeRef
from grimoire.institutions import InstitutionState
from grimoire.offscreen import (
    OFFSCREEN_KEY,
    Adopt,
    FactionPlan,
    OffscreenError,
    OffscreenState,
    Skip,
    apply,
    pending_skips,
    state_from_payload,
    state_to_payload,
)
from grimoire.peersync.pairing import pairing_ended
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts
from grimoire.sync.turns import open_conflicts


@dataclass(frozen=True)
class OffscreenView:
    """The campaign's calendar, its adopted plans and the calendar advances not yet applied to them."""

    scope: ScopeRef
    calendar: CalendarState | None
    state: OffscreenState | None
    pending: tuple[str, ...]
    etag: str


@dataclass(frozen=True)
class OffscreenChange:
    """An immutable plan for one adoption or skip; ``receipt`` describes that operation.

    ``commit`` returns the campaign's current view. A retry is ``already_applied`` with an empty
    preview. An abandoned plan reserves nothing.
    """

    library: OffscreenLibrary = field(repr=False, compare=False)
    scope: ScopeRef
    receipt: object
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

    def commit(self, approvals: Mapping[str, str] | None = None) -> OffscreenView:
        library = self.library
        with library.store.lock:
            view = library.read(self.scope)
            old = None if view.state is None else view.state.receipt(self.receipt.operation)
            if old is not None:
                if old != self.receipt:
                    raise OffscreenError("operation_mismatch", "operation receipt changed since planning")
                return view
            if self.already_applied:
                raise OffscreenError("history_changed", "operation no longer belongs to the retained history")
            self._transaction().commit(library.handle, approvals=approvals)
            return library.read(self.scope)


class OffscreenLibrary:
    """Trusted application interface over an open V2 store and its scoped apply handle."""

    def __init__(self, store: LibraryStore, handle: object) -> None:
        self.store = store
        self.handle = handle

    def _read(self, scope: ScopeRef):
        if type(scope) is not ScopeRef:
            raise OffscreenError("invalid_field", "offscreen scope must be a ScopeRef")
        path = "/".join(address_parts(scope))
        found, complete = open_conflicts(self.store, path, (path,),
                                         ended=lambda peer: pairing_ended(self.store, peer))
        if found or not complete:
            raise OffscreenError("scope_conflict", "campaign scope has unresolved or unreadable sync conflicts")
        stored = self.store.read(scope)
        record = stored.record
        scope_from_payload(scope, record.schema, record.payload)
        try:
            calendar = (None if CALENDAR_KEY not in record.payload
                        else calendar_from_payload(scope.campaign_id, record.payload[CALENDAR_KEY]))
        except CalendarError as error:
            raise OffscreenError("calendar_mismatch", f"the campaign calendar is unreadable ({error.reason})") from None
        state = (None if OFFSCREEN_KEY not in record.payload
                 else state_from_payload(scope.campaign_id, record.payload[OFFSCREEN_KEY], calendar))
        view = OffscreenView(scope, calendar, state, pending_skips(state, calendar), stored.etag)
        return view, record

    def read(self, scope: ScopeRef) -> OffscreenView:
        with self.store.lock:
            return self._read(scope)[0]

    def adopt(self, scope: ScopeRef, operation_id: str, plan: FactionPlan, *, institution: InstitutionState,
              expected: str) -> OffscreenChange:
        """Start ``plan`` at the calendar's current time, under ``institution``'s recorded decision.

        The institution is a caller-owned 057 snapshot (institutions have no store yet); its decision
        id and choice are retained in the receipt as provenance.
        """
        with self.store.lock:
            view, record = self._read(scope)
            return self._plan(view, record, Adopt(operation_id, plan), expected, institution)

    def advance(self, scope: ScopeRef, operation_id: str, *, budget: int, expected: str) -> OffscreenChange:
        """Progress every adopted plan across the committed calendar advance ``operation_id``."""
        with self.store.lock:
            view, record = self._read(scope)
            return self._plan(view, record, Skip(operation_id, budget), expected, None)

    def _plan(self, view, record, operation, expected, institution) -> OffscreenChange:
        if view.calendar is None:
            raise OffscreenError("uninitialized", "the campaign calendar is not initialized")
        state, receipt, already_applied = apply(view.scope.campaign_id, view.state, operation,
                                                calendar=view.calendar, institution=institution)
        if already_applied:
            return OffscreenChange(self, view.scope, receipt, True, None, view.etag)
        if expected != view.etag:
            raise StaleWrite([("/".join(address_parts(view.scope)), "campaign changed; reread before planning")])
        payload = thaw(record.payload)
        payload[OFFSCREEN_KEY] = state_to_payload(state)
        envelope = ScopeRecord(view.scope, record.schema, payload, owner=record.owner, extra=record.extra)
        change = OffscreenChange(self, view.scope, receipt, False, envelope, view.etag)
        # Preview enforces native size limits now. Never prune a receipt to fit: a forgotten skip id
        # would let a retry flag the same steps twice.
        change.preview()
        return change
