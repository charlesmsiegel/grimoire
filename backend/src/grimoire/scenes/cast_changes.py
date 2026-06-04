"""Scene-owned store of pending cast changes awaiting confirmation (#464).

Pure SQLite queue state (no file source of truth), mirroring the
``SceneLedger`` pattern. The Scene Manager owns the cast, so it owns this
store; the table is rebuilt from scratch if the database is deleted because
pending changes are ephemeral review state, not durable campaign content.
"""

from __future__ import annotations

import uuid

from grimoire.storage.db import Database
from grimoire.types.scene import CastChange, PendingCastChange
from grimoire.util import now_iso


def _row_to_model(row) -> PendingCastChange:
    return PendingCastChange(
        id=row["id"],
        campaign_id=row["campaign_id"],
        scene_id=row["scene_id"],
        character_ref=row["character_ref"],
        change=CastChange(row["change"]),
        is_pc=bool(row["is_pc"]),
        evidence=row["evidence"] or "",
        confidence=float(row["confidence"]),
        turn_id=row["turn_id"],
        status=row["status"],
        created_at=row["created_at"],
    )


class CastChangeStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self,
        *,
        campaign_id: str,
        scene_id: str,
        character_ref: str,
        change: CastChange,
        is_pc: bool,
        evidence: str = "",
        confidence: float = 0.0,
        turn_id: str | None = None,
    ) -> str:
        item_id = f"cc-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO pending_cast_changes
                (id, campaign_id, scene_id, character_ref, change, is_pc,
                 evidence, confidence, turn_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                item_id,
                campaign_id,
                scene_id,
                character_ref,
                str(change),
                1 if is_pc else 0,
                evidence,
                confidence,
                turn_id,
                now_iso(),
            ),
        )
        return item_id

    async def find_pending(
        self, scene_id: str, character_ref: str, change: CastChange
    ) -> PendingCastChange | None:
        """Return an existing *pending* row matching the triple, if any (#464)."""
        row = await self._db.fetchone(
            """
            SELECT * FROM pending_cast_changes
            WHERE scene_id = ? AND character_ref = ? AND change = ? AND status = 'pending'
            LIMIT 1
            """,
            (scene_id, character_ref, str(change)),
        )
        return _row_to_model(row) if row else None

    async def list_pending(self, scene_id: str) -> list[PendingCastChange]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM pending_cast_changes
            WHERE scene_id = ? AND status = 'pending'
            ORDER BY created_at
            """,
            (scene_id,),
        )
        return [_row_to_model(r) for r in rows]

    async def list_confirmed(self, scene_id: str) -> list[PendingCastChange]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM pending_cast_changes
            WHERE scene_id = ? AND status = 'confirmed'
            ORDER BY created_at
            """,
            (scene_id,),
        )
        return [_row_to_model(r) for r in rows]

    async def get(self, item_id: str) -> PendingCastChange | None:
        row = await self._db.fetchone(
            "SELECT * FROM pending_cast_changes WHERE id = ?",
            (item_id,),
        )
        return _row_to_model(row) if row else None

    async def set_status(self, item_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE pending_cast_changes SET status = ? WHERE id = ?",
            (status, item_id),
        )
