"""The read-only toolbox an investigation runs through (082): ``list`` and ``read`` over one source.

Each tool asks the job's **untrusted** worker handle for ``inspect`` on the address it is about to read,
before reading anything, so the library's authority audits every read and a job reads only what its grant
covers. The toolbox has no write method, and the handle's grant names only ``inspect``: a job that wanted
to change a record would need a separate, human-approved World Change Set (043). Reads go through the
store's own readers and heads resolve as ``load()`` resolves them (``ChangeSetLibrary.head``), and an entity
``load()`` drops is refused (``entity_dropped``), so a job never sees a head the rest of the app would not.
"""

from __future__ import annotations

from grimoire.changesets import ChangeSetError
from grimoire.changesetstore import ChangeSetLibrary
from grimoire.core.identity import SourceRef
from grimoire.core.json_value import JsonValueError, freeze
from grimoire.investigations import Listed, RecordRead, ToolResult
from grimoire.security import Permission, WorkerHandle
from grimoire.store import LibraryStore, StoreError
from grimoire.store.layout import RECORDS, source_parts

_REFUSALS = frozenset({"missing_source", "excluded_source", "missing", "no_head", "unreadable"})
#: ``check_definition`` reasons that mean ``load()`` would not admit the entity this toolbox found a head for
_DROPPED = "entity_dropped"


def source_address(source: str) -> str:
    """The logical address of one source's own files (``sources/world/harbor/_source``): the scope of a job's
    grant. It does not cover the source's sub-sources, which live beside its ``_source``."""
    return "/".join(source_parts(source))


class WorldToolbox:
    """``list`` and ``read`` for one source, every call authorized ``inspect`` on ``handle`` first."""

    def __init__(self, store: LibraryStore, handle: WorkerHandle, source: str) -> None:
        if not isinstance(handle, WorkerHandle) or handle.trusted is not False:
            raise ValueError("an investigation reads for an untrusted worker: its handle must be untrusted")
        self._store = store
        self._handle = handle
        self._source = source
        self._heads = ChangeSetLibrary(store, None)  # head resolution only; it never writes here

    def _allowed(self, address: str) -> tuple[bool, str]:
        decision = self._handle.authorize(Permission.INSPECT, address)
        return decision.allowed, decision.reason.value

    def list(self) -> ToolResult:
        """Every entity of the source with its revision files and the head ``read`` resolves."""
        allowed, reason = self._allowed(source_address(self._source))
        if not allowed:
            return ToolResult("denied", detail=reason)
        store = self._store
        try:
            with store.lock:
                stored = store.check_source(self._source)
                if isinstance(stored, str):
                    return ToolResult(stored)  # type: ignore[arg-type]
                entities = store.source_entities(self._source)
                dropped = {key for key in entities
                           if store.check_definition(SourceRef(self._source, *key)) == _DROPPED}
        except (StoreError, OSError, ValueError):
            return ToolResult("unreadable")
        heads = stored.manifest.heads
        listed = []
        for (kind, entity_id), revisions in sorted(entities.items()):
            declared = heads.get(f"{kind}/{entity_id}")
            if (kind, entity_id) in dropped:
                head = None  # load() drops it (an id reused across kinds, say), so read refuses it too
            elif declared is not None:
                head = declared if declared in revisions else None
            else:
                head = revisions[0] if len(revisions) == 1 else None
            listed.append(Listed(kind, entity_id, revisions, head))
        return ToolResult("ok", listed=tuple(listed))

    def read(self, kind: str, entity_id: str) -> ToolResult:
        """One entity's head revision: its exact revision, etag, display name and payload."""
        try:
            SourceRef(self._source, kind, entity_id)
        except ValueError:
            return ToolResult("invalid_target")
        entity = "/".join((*source_parts(self._source), RECORDS, kind, entity_id))
        allowed, reason = self._allowed(entity)
        if not allowed:
            return ToolResult("denied", detail=reason)
        try:
            with self._store.lock:
                if self._store.check_definition(SourceRef(self._source, kind, entity_id)) == _DROPPED:
                    return ToolResult(_DROPPED)
                revision, envelope, etag = self._heads.head(self._source, kind, entity_id)
        except ChangeSetError as error:
            outcome = error.reason if error.reason in _REFUSALS else "unreadable"
            return ToolResult(outcome)  # type: ignore[arg-type]
        except (StoreError, OSError, ValueError):
            return ToolResult("unreadable")
        try:
            payload = freeze(envelope.payload)
        except JsonValueError:
            return ToolResult("unreadable")
        return ToolResult("ok", read=RecordRead(kind, entity_id, revision, etag, envelope.name, payload))


__all__ = ["WorldToolbox", "source_address"]
