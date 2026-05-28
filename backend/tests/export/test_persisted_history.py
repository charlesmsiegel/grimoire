"""Persisted-history coverage for ExportService.

When wired to a State Store, ExportService writes each completed export to
``export_records`` and reloads on demand, so history survives process
restarts. The in-memory path stays unaffected (see ``test_service.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from grimoire.export import ExportService, ExportServiceConfig
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.export import ExportOptions, ExportResult, ExportSelection

from .conftest import make_sources


class _FakeAdapter:
    id: ClassVar[str] = "fake"
    name: ClassVar[str] = "Fake"
    extensions: ClassVar[list[str]] = ["bin"]
    mime_type: ClassVar[str] = "application/octet-stream"

    async def export(
        self,
        campaign_id: str,
        selection: ExportSelection,
        options: ExportOptions,
        output_path: Path,
    ) -> ExportResult:
        output_path.write_bytes(b"hello")
        return ExportResult(
            format="bin",
            size_bytes=5,
            scene_count=0,
            word_count=0,
            image_count=0,
            file_path=str(output_path),
            created_at=datetime.now(UTC),
        )

    def default_options(self) -> ExportOptions:
        return ExportOptions()

    def option_schema(self) -> dict:
        return {"type": "object"}


def _build_service(store: StateStore, tmp_path: Path) -> ExportService:
    sources = make_sources()
    config = ExportServiceConfig(output_directory=tmp_path / "exports")
    return ExportService(
        sources=sources,
        adapters=[_FakeAdapter()],
        config=config,
        state_store=store,
    )


@pytest.mark.asyncio
async def test_persisted_history_round_trip(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    try:
        await apply_migrations(db)
        store = StateStore(db, data_root)

        service = _build_service(store, tmp_path)
        selection = ExportSelection()
        options = ExportOptions(title="Once")
        await service.export("campaign-a", "fake", selection, options)
        await service.export("campaign-a", "fake", selection, ExportOptions(title="Twice"))

        history = await service.history("campaign-a")
        assert len(history) == 2
        assert {r.options.title for r in history} == {"Once", "Twice"}

        # A fresh service over the same DB reloads from disk lazily.
        reborn = _build_service(store, tmp_path)
        reloaded = await reborn.history("campaign-a")
        assert len(reloaded) == 2
        assert {r.options.title for r in reloaded} == {"Once", "Twice"}
        assert all(r.adapter_id == "fake" for r in reloaded)
    finally:
        await db.close()
