"""Scene Ledger: per-campaign persistent store of scene ideas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from grimoire.storage.db import Database


class SceneLedger:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self,
        *,
        campaign_id: str,
        summary: str,
        source: str,
        greeting_id: str | None = None,
        proposed_location: str | None = None,
        proposed_cast: str | None = None,
    ) -> str:
        item_id = f"ledger-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO scene_ledger
                (id, campaign_id, summary, greeting_id, source, status,
                 created_at, proposed_location, proposed_cast)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                item_id,
                campaign_id,
                summary,
                greeting_id,
                source,
                datetime.now(UTC).isoformat(),
                proposed_location,
                proposed_cast,
            ),
        )
        return item_id

    async def list_active(self, campaign_id: str) -> list[dict]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM scene_ledger
            WHERE campaign_id = ? AND status = 'active'
            ORDER BY created_at
            """,
            (campaign_id,),
        )
        return [dict(r) for r in rows]

    async def list_all(self, campaign_id: str) -> list[dict]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM scene_ledger
            WHERE campaign_id = ?
            ORDER BY created_at
            """,
            (campaign_id,),
        )
        return [dict(r) for r in rows]

    async def get(self, campaign_id: str, item_id: str) -> dict | None:
        row = await self._db.fetchone(
            "SELECT * FROM scene_ledger WHERE id = ? AND campaign_id = ?",
            (item_id, campaign_id),
        )
        return dict(row) if row else None

    async def set_status(self, campaign_id: str, item_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE scene_ledger SET status = ? WHERE id = ? AND campaign_id = ?",
            (status, item_id, campaign_id),
        )

    async def mark_used(self, campaign_id: str, item_id: str, scene_id: str) -> None:
        await self._db.execute(
            """
            UPDATE scene_ledger
            SET status = 'used', used_in_scene_id = ?
            WHERE id = ? AND campaign_id = ?
            """,
            (scene_id, item_id, campaign_id),
        )
