"""Native synthetic campaign/history fixtures for Sprint 055."""
from grimoire.calendars import CalendarDefinition
from grimoire.calendarstore import CalendarLibrary
from grimoire.chats import PlayScope, SCOPE_SCHEMA, scope_to_payload
from grimoire.core.identity import ScopeRef, TranscriptRef
from grimoire.store import ScopeRecord
from grimoire.transcripts import Post, Speaker, TranscriptPage, Variant, TRANSCRIPT_SCHEMA, page_to_payload

from store_helpers import OWNER, owner_handle

SCOPE = ScopeRef("saltmarch")
OTHER = ScopeRef("elsewhere")
OLD = TranscriptRef("saltmarch", "old-scene", 1)


def seed_calendar_scope(store):
    txn = store.transaction()
    for scope in (SCOPE, OTHER):
        payload = scope_to_payload(PlayScope(scope, "2026-01-01T00:00:00Z", extras={"x-notes": {"keep": True}}))
        txn.put(ScopeRecord(scope, SCOPE_SCHEMA, payload, owner=OWNER, extra={"x-editor": "keep"}), None)
    page = TranscriptPage(OLD, (Post("old-post", Speaker("narrator"),
                                    (Variant("The gate opens.", "2024-01-01T09:00", extras={"scene_date": "2024-01-01"}),), 0),))
    txn.put(ScopeRecord(OLD, TRANSCRIPT_SCHEMA, page_to_payload(page)), None)
    txn.commit(owner_handle())


def initialized(store, handle=None):
    library = CalendarLibrary(store, handle or owner_handle())
    view = library.read(SCOPE)
    if view.state is None:
        library.initialize(SCOPE, "start", CalendarDefinition(), "2024-02-20", minute=1410,
                           expected=view.etag).commit()
    return library


def scheduled(store):
    library = initialized(store)
    library.schedule(SCOPE, "schedule", "meeting", "2024-03-01", minute=1410, label="Meet at the gate",
                     expected=library.read(SCOPE).etag).commit()
    return library
