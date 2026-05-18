"""ExportService — the spec-13 Export façade.

Holds the per-id adapter registry, runs `export()` / `preview()`, and
maintains a history of past exports. History is in-memory by default;
when a State Store is wired in, records are also persisted to the
``export_records`` table so ``GET /campaigns/{id}/exports`` survives
restarts.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from grimoire.export.config import ExportConfig, ExportFiltersConfig
from grimoire.export.errors import UnknownAdapterError
from grimoire.export.snapshot import build_snapshot
from grimoire.export.sources import DataSources
from grimoire.types.common import CampaignId, JsonSchema
from grimoire.types.export import (
    ExportCapabilities,
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
    capabilities: ExportCapabilities

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
    filters: ExportFiltersConfig = field(default_factory=ExportFiltersConfig)

    @classmethod
    def from_export_config(cls, cfg: ExportConfig) -> ExportServiceConfig:
        return cls(
            output_directory=cfg.output_directory,
            default_adapter=cfg.default_adapter,
            history_limit=cfg.history_limit,
            filters=cfg.filters,
        )


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
        state_store: Any = None,
    ) -> None:
        self.sources = sources
        self.config = config or ExportServiceConfig()
        self._state_store = state_store
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
            world_versions = await self._capture_world_versions(campaign_id)
            await self._record_history(
                campaign_id, adapter_id, selection, options, result, world_versions
            )
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
        snapshot = await build_snapshot(
            campaign_id,
            selection,
            options,
            self.sources,
            filter_defaults=self.config.filters,
        )
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
        bucket = self._history.get(campaign_id)
        if not bucket and self._state_store is not None:
            records = await self._load_persisted_history(campaign_id)
            self._history[campaign_id] = [_HistoryEntry(record=r) for r in records]
            return records
        return [entry.record for entry in (bucket or [])]

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

    async def _capture_world_versions(self, campaign_id: CampaignId) -> list[dict[str, Any]]:
        """Best-effort snapshot of the library versions used by this export.

        Spec 13 §Responsibilities lists "against what library versions"
        among the things history must record. We read from
        ``sources.world.get_composition_worlds`` and tolerate missing
        attributes or unimplemented methods.
        """

        world_source = getattr(self.sources, "world", None)
        if world_source is None:
            return []
        getter = getattr(world_source, "get_composition_worlds", None)
        if getter is None:
            return []
        try:
            worlds = await getter(campaign_id)
        except (AttributeError, NotImplementedError):
            return []
        captured: list[dict[str, Any]] = []
        for world in worlds or []:
            world_id = getattr(world, "id", None)
            version = getattr(world, "version", None)
            if world_id is None:
                continue
            captured.append({"world_id": world_id, "version": version})
        return captured

    async def _record_history(
        self,
        campaign_id: CampaignId,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
        result: ExportResult,
        world_versions: list[dict[str, Any]],
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
        if self._state_store is not None:
            await self._persist_record(record, world_versions)

    async def _persist_record(
        self,
        record: ExportRecord,
        world_versions: list[dict[str, Any]],
    ) -> None:
        await self._state_store.db.execute(
            """
            INSERT INTO export_records (
              id, campaign_id, adapter_id,
              selection_json, options_json, result_json,
              world_versions_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.campaign_id,
                record.adapter_id,
                record.selection.model_dump_json(),
                record.options.model_dump_json(),
                record.result.model_dump_json(),
                json.dumps(world_versions),
                record.created_at.isoformat(),
            ),
        )

    async def _load_persisted_history(self, campaign_id: CampaignId) -> list[ExportRecord]:
        rows = await self._state_store.db.fetchall(
            """
            SELECT id, campaign_id, adapter_id,
                   selection_json, options_json, result_json, created_at
              FROM export_records
             WHERE campaign_id = ?
             ORDER BY created_at ASC
            """,
            (campaign_id,),
        )
        records: list[ExportRecord] = []
        for row in rows:
            records.append(
                ExportRecord(
                    id=row["id"],
                    campaign_id=row["campaign_id"],
                    adapter_id=row["adapter_id"],
                    selection=ExportSelection.model_validate_json(row["selection_json"]),
                    options=ExportOptions.model_validate_json(row["options_json"]),
                    result=ExportResult.model_validate_json(row["result_json"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return records


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
