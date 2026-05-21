"""REST routes for character-card imports (spec 2026-05-19 §REST).

Preview / commit split: ``preview`` parses the card and stashes the
``IngestedCharacterCard`` in an in-memory TTL cache so the UI can render
the parsed character + greetings + lore + warnings before the user
hits commit. ``commit`` runs ``_finalize_import`` against the stashed
ingest and writes the import report.

The preview cache is process-local; the cache key returned to the
client is opaque. It expires after :data:`PREVIEW_TTL_SECONDS` (default
15 minutes).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from grimoire.api.deps import CharactersDep, LibraryDep, StateStoreDep
from grimoire.api.util import to_payload
from grimoire.library.classify import suggest_kind
from grimoire.library.reclassify import _lore_entry_from_ingested
from grimoire.state_store.paths import library_root
from grimoire.types.characters import IngestedCharacterCard, IngestOptions

router = APIRouter()


PREVIEW_TTL_SECONDS = 15 * 60


class _PreviewSlot:
    __slots__ = ("expires_at", "filename", "ingested", "world_id")

    def __init__(
        self,
        ingested: IngestedCharacterCard,
        world_id: str,
        filename: str,
        expires_at: float,
    ) -> None:
        self.ingested = ingested
        self.world_id = world_id
        self.filename = filename
        self.expires_at = expires_at


_PREVIEW_CACHE: dict[str, _PreviewSlot] = {}


def _gc_expired() -> None:
    now = time.time()
    expired = [k for k, v in _PREVIEW_CACHE.items() if v.expires_at <= now]
    for key in expired:
        _PREVIEW_CACHE.pop(key, None)


class CommitPayload(BaseModel):
    preview_id: str
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/library/worlds/{world_id}/imports/sillytavern/preview")
async def preview_sillytavern_import(
    world_id: str,
    characters: CharactersDep,
    library: LibraryDep,
    file: UploadFile,
) -> dict[str, Any]:
    """Parse a card without writing it; cache the ingest for a later commit."""
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        # _ingest is intentionally exposed via the protected name because
        # the parse + optional LLM enrichment is the same work commit
        # would do — running it now keeps the preview accurate.
        ingested = await characters._ingest(payload, options=IngestOptions())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"could not parse card: {exc}") from exc

    _gc_expired()
    preview_id = uuid.uuid4().hex
    _PREVIEW_CACHE[preview_id] = _PreviewSlot(
        ingested=ingested,
        world_id=world_id,
        filename=file.filename or "card",
        expires_at=time.time() + PREVIEW_TTL_SECONDS,
    )
    # Avatar bytes don't survive JSON encoding and aren't useful to the
    # client (the preview UI shows the raw upload). Drop them before
    # serialising; the cached slot still has the bytes for commit.
    summary = ingested.model_dump(mode="json", exclude={"avatar_bytes"})

    threshold = library.config.reclassification.suggestion_threshold
    lore_suggestions: list[dict[str, Any]] = []
    for entry in ingested.lore_entries:
        proxy = _lore_entry_from_ingested(entry, world_id=world_id)
        suggestion = suggest_kind(proxy, threshold=threshold)
        lore_suggestions.append(
            {
                "source_index": entry.source_index,
                "kind": suggestion.kind.value,
                "confidence": suggestion.confidence,
                "reason": suggestion.reason,
            }
        )

    return {
        "preview_id": preview_id,
        "expires_in_seconds": PREVIEW_TTL_SECONDS,
        "ingested": summary,
        "lore_suggestions": lore_suggestions,
    }


@router.post("/library/worlds/{world_id}/imports/sillytavern/commit", status_code=201)
async def commit_sillytavern_import(
    world_id: str,
    payload: CommitPayload,
    characters: CharactersDep,
) -> dict[str, Any]:
    """Commit a previously previewed card to ``world_id``."""
    _gc_expired()
    slot = _PREVIEW_CACHE.pop(payload.preview_id, None)
    if slot is None:
        raise HTTPException(status_code=404, detail="preview not found or expired")
    if slot.world_id != world_id:
        raise HTTPException(
            status_code=400,
            detail=f"preview was created for {slot.world_id!r}, not {world_id!r}",
        )
    try:
        options = IngestOptions.model_validate(payload.options or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bad options: {exc}") from exc
    result = await characters._finalize_import(world_id, slot.ingested, options=options)
    return {"result": to_payload(result)}


@router.get("/library/imports")
async def list_import_reports(store: StateStoreDep) -> dict[str, Any]:
    reports_dir = library_root(store.data_root) / "imports"
    if not reports_dir.exists():
        return {"reports": []}
    rows: list[dict[str, Any]] = []
    for path in sorted(reports_dir.glob("*.md")):
        stat = path.stat()
        rows.append(
            {
                "id": path.stem,
                "filename": path.name,
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
            }
        )
    return {"reports": rows}


@router.get("/library/imports/{report_id}")
async def get_import_report(report_id: str, store: StateStoreDep) -> dict[str, Any]:
    # Block ``..`` traversal — Path strips parents but we also check the
    # joined path stays inside the imports directory.
    reports_dir = library_root(store.data_root) / "imports"
    candidate = (reports_dir / f"{report_id}.md").resolve()
    if not candidate.is_file() or not str(candidate).startswith(str(reports_dir.resolve())):
        raise HTTPException(status_code=404, detail="report not found")
    return {"id": report_id, "body": candidate.read_text(encoding="utf-8")}
