"""ExportService — the spec-13 Export façade.

Holds the per-id adapter registry, runs `export()` / `preview()`, and
maintains an in-memory history. Persisted history (`ExportRecord` rows in
the State Store) is a follow-on the orchestrator wires up; here we keep
the interface stable so it slots in later.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from grimoire.export.errors import UnknownAdapterError
from grimoire.export.snapshot import build_snapshot
from grimoire.export.sources import DataSources
from grimoire.types.common import CampaignId, JsonSchema
from grimoire.types.export import (
    ExportOptions,
    ExportPreview,
    ExportRecord,
    ExportResult,
    ExportSelection,
)


@runtime_checkable
class ExportAdapter(Protocol):
    id: str
    name: str
    extensions: list[str]
    mime_type: str

    async def export(
        self,
        campaign_id: CampaignId,
        selection: ExportSelection,
        options: ExportOptions,
        output_path: Path,
    ) -> ExportResult: ...

    def default_options(self) -> ExportOptions: ...

    def option_schema(self) -> JsonSchema: ...


@dataclass(slots=True)
class ExportServiceConfig:
    output_directory: Path = Path("./exports")
    default_adapter: str = "epub"
    history_limit: int = 100


@dataclass(slots=True)
class _HistoryEntry:
    record: ExportRecord


class ExportService:
    def __init__(
        self,
        *,
        sources: DataSources,
        adapters: Iterable[ExportAdapter] | None = None,
        config: ExportServiceConfig | None = None,
    ) -> None:
        self.sources = sources
        self.config = config or ExportServiceConfig()
        self._adapters: dict[str, ExportAdapter] = {}
        self._history: dict[CampaignId, list[_HistoryEntry]] = {}
        self._locks: dict[CampaignId, asyncio.Lock] = {}
        for adapter in adapters or []:
            self.register(adapter)

    # -- registry -------------------------------------------------------- #

    def register(self, adapter: ExportAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def unregister(self, adapter_id: str) -> None:
        self._adapters.pop(adapter_id, None)

    def list_adapters(self) -> list[ExportAdapter]:
        return list(self._adapters.values())

    def get_adapter(self, adapter_id: str) -> ExportAdapter:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise UnknownAdapterError(adapter_id)
        return adapter

    # -- API ------------------------------------------------------------- #

    async def export(
        self,
        campaign_id: CampaignId,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
        *,
        output_path: Path | None = None,
    ) -> ExportResult:
        adapter = self.get_adapter(adapter_id)
        async with self._lock_for(campaign_id):
            path = output_path or self._default_output_path(campaign_id, adapter, options)
            path.parent.mkdir(parents=True, exist_ok=True)
            result = await adapter.export(campaign_id, selection, options, path)
            self._record_history(campaign_id, adapter_id, selection, options, result)
            return result

    async def preview(
        self,
        campaign_id: CampaignId,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
    ) -> ExportPreview:
        # `preview` runs the snapshot pipeline only — no XHTML / EPUB
        # packaging. The estimate is a coarse bytes-per-word average so the
        # UI can show "≈ N MB" without a real export.
        adapter = self.get_adapter(adapter_id)
        snapshot = await build_snapshot(campaign_id, selection, options, self.sources)
        bytes_per_word = 8 if adapter.id == "epub" else 6
        estimate = max(4096, snapshot.word_count * bytes_per_word + snapshot.image_count * 256_000)
        warnings = list(snapshot.warnings)
        if snapshot.scene_count == 0:
            warnings.append("selection produced no exportable scenes")
        return ExportPreview(
            adapter_id=adapter.id,
            scene_count=snapshot.scene_count,
            word_count=snapshot.word_count,
            image_count=snapshot.image_count,
            estimated_size_bytes=estimate,
            warnings=warnings,
        )

    async def history(self, campaign_id: CampaignId) -> list[ExportRecord]:
        entries = self._history.get(campaign_id) or []
        return [entry.record for entry in entries]

    # -- helpers --------------------------------------------------------- #

    def _lock_for(self, campaign_id: CampaignId) -> asyncio.Lock:
        lock = self._locks.get(campaign_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[campaign_id] = lock
        return lock

    def _default_output_path(
        self,
        campaign_id: CampaignId,
        adapter: ExportAdapter,
        options: ExportOptions,
    ) -> Path:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        stem = _slugify(options.title or campaign_id) or campaign_id
        ext = adapter.extensions[0] if adapter.extensions else "bin"
        return self.config.output_directory / campaign_id / f"{stem}-{ts}.{ext}"

    def _record_history(
        self,
        campaign_id: CampaignId,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
        result: ExportResult,
    ) -> None:
        record = ExportRecord(
            id=f"export_{uuid.uuid4().hex[:12]}",
            campaign_id=campaign_id,
            adapter_id=adapter_id,
            selection=selection,
            options=options,
            result=result,
            created_at=result.created_at or datetime.now(UTC),
        )
        bucket = self._history.setdefault(campaign_id, [])
        bucket.append(_HistoryEntry(record=record))
        if len(bucket) > self.config.history_limit:
            del bucket[: -self.config.history_limit]


def _slugify(text: str) -> str:
    out: list[str] = []
    last_dash = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-")


__all__ = [
    "ExportAdapter",
    "ExportService",
    "ExportServiceConfig",
]
