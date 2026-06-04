"""ForkCoordinator — campaign forking for the orchestrator."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator.errors import (
    CampaignIdExists,
    OrchestratorError,
    UnknownCampaignError,
)
from grimoire.orchestrator.fork_images import fork_image_files
from grimoire.scenes.manager import SceneManager
from grimoire.state_store.fork import bulk_copy, fingerprint, replay_to_turn
from grimoire.types.common import CampaignId
from grimoire.types.orchestrator import ForkCampaignResult
from grimoire.util import now_iso

logger = logging.getLogger(__name__)


class ForkCoordinator:
    """Manages campaign forking, lineage, and pending fork queues."""

    def __init__(
        self,
        *,
        host: Any,
        scenes: SceneManager,
        state_store: Any,
        event_bus: EventBus,
        clock: Callable[[], datetime],
    ) -> None:
        self._host = host
        self._scenes = scenes
        self._store = state_store
        self._bus = event_bus
        self._clock = clock

    async def _require_campaign(self, campaign_id: CampaignId) -> None:
        row = await self._store.db.fetchone("SELECT id FROM campaigns WHERE id = ?", (campaign_id,))
        if row is None:
            raise UnknownCampaignError(campaign_id)

    async def _campaign_exists(self, campaign_id: str) -> bool:
        row = await self._store.db.fetchone("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,))
        return row is not None

    def _is_streaming(self, campaign_id: str) -> bool:
        state = self._host._campaigns.get(campaign_id)
        return state is not None and state.active is not None

    async def fork_campaign(
        self,
        *,
        campaign_id: CampaignId,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: str | None = None,
        description: str | None = None,
        make_active: bool = False,
    ) -> ForkCampaignResult:
        await self._require_campaign(campaign_id)

        if await self._campaign_exists(new_campaign_id):
            raise CampaignIdExists(new_campaign_id)

        if self._is_streaming(campaign_id):
            return await self._enqueue_fork(
                campaign_id=campaign_id,
                new_campaign_id=new_campaign_id,
                new_name=new_name,
                fork_at_post_id=fork_at_post_id,
                description=description,
                make_active=make_active,
            )

        return await self._execute_fork(
            campaign_id=campaign_id,
            new_campaign_id=new_campaign_id,
            new_name=new_name,
            fork_at_post_id=fork_at_post_id,
            description=description,
            make_active=make_active,
        )

    async def _execute_fork(
        self,
        *,
        campaign_id: CampaignId,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: str | None,
        description: str | None,
        make_active: bool,
    ) -> ForkCampaignResult:
        db = self._store.db
        data_root = self._store.data_root
        src_dir = data_root / "campaigns" / campaign_id
        new_dir = data_root / "campaigns" / new_campaign_id

        cutoff_iso: str | None = None
        cutoff_turn_id: str | None = None
        deltas_replayed = 0
        fingerprint_match = True
        degraded = False

        if fork_at_post_id is not None:
            row = await db.fetchone(
                "SELECT created_at, turn_id FROM posts WHERE id = ? AND campaign_id = ?",
                (fork_at_post_id, campaign_id),
            )
            if row is None:
                raise OrchestratorError(
                    f"fork_at_post_id {fork_at_post_id!r} not found in campaign {campaign_id!r}"
                )
            cutoff_iso = row["created_at"]
            cutoff_turn_id = row["turn_id"]

        await self._bus.emit(
            Event(
                type=events.CAMPAIGN_FORK_STARTED,
                payload={
                    "source": campaign_id,
                    "new": new_campaign_id,
                    "at_post": fork_at_post_id,
                },
            )
        )

        try:
            await self._clone_campaign_row(
                source_id=campaign_id,
                new_id=new_campaign_id,
                new_name=new_name,
                description=description,
                fork_at_post_id=fork_at_post_id,
                cutoff_turn_id=cutoff_turn_id,
            )

            if cutoff_iso is None:
                await bulk_copy(db, original=campaign_id, new=new_campaign_id, cutoff_iso=None)
            else:
                fp_origin = await fingerprint(db, campaign_id, cutoff_iso=cutoff_iso)
                deltas_replayed = await replay_to_turn(
                    db,
                    original=campaign_id,
                    new=new_campaign_id,
                    cutoff_iso=cutoff_iso,
                )
                fp_new = await fingerprint(db, new_campaign_id)
                if fp_origin != fp_new:
                    fingerprint_match = False
                    degraded = True

            try:
                self._copy_campaign_files(src_dir, new_dir)
            except Exception as exc:
                logger.warning("fork file copy failed: %s", exc, exc_info=True)

            new_dir.mkdir(parents=True, exist_ok=True)
            img_result = await fork_image_files(src_dir, new_dir)
            await db.execute(
                "UPDATE campaigns SET forked_image_handling = ? WHERE id = ?",
                (img_result.handling, new_campaign_id),
            )

            if make_active:
                await db.execute(
                    "UPDATE campaigns SET last_played_at = ? WHERE id = ?",
                    (now_iso(), new_campaign_id),
                )
        except sqlite3.IntegrityError as exc:
            await self._bus.emit(
                Event(
                    type=events.CAMPAIGN_FORK_FAILED,
                    payload={
                        "source": campaign_id,
                        "new": new_campaign_id,
                        "error": "collision (concurrent fork won)",
                    },
                )
            )
            raise CampaignIdExists(new_campaign_id) from exc
        except Exception as exc:
            await self._wipe_failed_fork(new_campaign_id, new_dir)
            await self._bus.emit(
                Event(
                    type=events.CAMPAIGN_FORK_FAILED,
                    payload={
                        "source": campaign_id,
                        "new": new_campaign_id,
                        "error": str(exc),
                    },
                )
            )
            raise

        await self._bus.emit(
            Event(
                type=events.CAMPAIGN_FORKED,
                payload={
                    "source": campaign_id,
                    "new": new_campaign_id,
                    "at_post": fork_at_post_id,
                    "image_handling": img_result.handling,
                    "deltas_replayed": deltas_replayed,
                    "degraded": degraded,
                },
            )
        )

        return ForkCampaignResult(
            new_campaign_id=new_campaign_id,
            new_name=new_name,
            forked_from_campaign_id=campaign_id,
            forked_at_post_id=fork_at_post_id,
            image_handling=img_result.handling,
            files_copied=img_result.files_copied,
            deltas_replayed=deltas_replayed,
            fingerprint_match=fingerprint_match,
            degraded=degraded,
            queued=False,
            created_at=self._clock(),
        )

    async def _clone_campaign_row(
        self,
        *,
        source_id: str,
        new_id: str,
        new_name: str,
        description: str | None,
        fork_at_post_id: str | None,
        cutoff_turn_id: str | None,
    ) -> None:
        db = self._store.db
        src = await db.fetchone("SELECT * FROM campaigns WHERE id = ?", (source_id,))
        if src is None:
            raise UnknownCampaignError(source_id)
        async with db.acquire() as conn:
            cur = await conn.execute("PRAGMA table_info(campaigns)")
            cols = [r["name"] for r in await cur.fetchall()]
            await cur.close()
        overrides = {
            "id": new_id,
            "name": new_name,
            "description": description if description is not None else src["description"],
            "created_at": now_iso(),
            "last_played_at": None,
            "forked_from_campaign_id": source_id,
            "forked_at_post_id": fork_at_post_id,
            "forked_at_turn_id": cutoff_turn_id,
            "forked_image_handling": None,
        }
        values = []
        for col in cols:
            if col in overrides:
                values.append(overrides[col])
            else:
                values.append(src[col])
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        await db.execute(
            f"INSERT INTO campaigns ({col_list}) VALUES ({placeholders})",
            tuple(values),
        )

    async def _enqueue_fork(
        self,
        *,
        campaign_id: str,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: str | None,
        description: str | None,
        make_active: bool,
    ) -> ForkCampaignResult:
        pending_id = f"pf_{uuid.uuid4().hex[:16]}"
        await self._store.db.execute(
            """
            INSERT INTO pending_forks (
                id, source_campaign_id, new_campaign_id, new_name,
                fork_at_post_id, description, make_active, enqueued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pending_id,
                campaign_id,
                new_campaign_id,
                new_name,
                fork_at_post_id,
                description,
                1 if make_active else 0,
                now_iso(),
            ),
        )
        await self._bus.emit(
            Event(
                type=events.CAMPAIGN_FORK_QUEUED,
                payload={
                    "source": campaign_id,
                    "new": new_campaign_id,
                    "pending_id": pending_id,
                },
            )
        )
        return ForkCampaignResult(
            new_campaign_id=new_campaign_id,
            new_name=new_name,
            forked_from_campaign_id=campaign_id,
            forked_at_post_id=fork_at_post_id,
            image_handling="pending",
            queued=True,
            created_at=self._clock(),
        )

    async def list_pending_forks(self, campaign_id: str) -> list[dict]:
        rows = await self._store.db.fetchall(
            """
            SELECT id, new_campaign_id, new_name, fork_at_post_id,
                   description, make_active, enqueued_at, started_at,
                   completed_at, error
              FROM pending_forks
             WHERE source_campaign_id = ?
               AND completed_at IS NULL
             ORDER BY enqueued_at
            """,
            (campaign_id,),
        )
        return [dict(r) for r in rows]

    async def process_pending_forks(self, campaign_id: str) -> list[ForkCampaignResult]:
        if self._is_streaming(campaign_id):
            return []
        results: list[ForkCampaignResult] = []
        while True:
            row = await self._store.db.fetchone(
                """
                SELECT id, new_campaign_id, new_name, fork_at_post_id,
                       description, make_active
                  FROM pending_forks
                 WHERE source_campaign_id = ?
                   AND completed_at IS NULL
                   AND started_at IS NULL
                 ORDER BY enqueued_at
                 LIMIT 1
                """,
                (campaign_id,),
            )
            if row is None:
                break
            pending_id = row["id"]
            async with self._store.db.acquire() as conn:
                cur = await conn.execute(
                    "UPDATE pending_forks SET started_at = ? WHERE id = ? AND started_at IS NULL",
                    (now_iso(), pending_id),
                )
                claimed = cur.rowcount
                await cur.close()
            if claimed == 0:
                continue
            try:
                result = await self._execute_fork(
                    campaign_id=campaign_id,
                    new_campaign_id=row["new_campaign_id"],
                    new_name=row["new_name"],
                    fork_at_post_id=row["fork_at_post_id"],
                    description=row["description"],
                    make_active=bool(row["make_active"]),
                )
                await self._store.db.execute(
                    "UPDATE pending_forks SET completed_at = ? WHERE id = ?",
                    (now_iso(), pending_id),
                )
                results.append(result)
            except Exception as exc:
                await self._store.db.execute(
                    "UPDATE pending_forks SET completed_at = ?, error = ? WHERE id = ?",
                    (now_iso(), str(exc), pending_id),
                )
                logger.warning("pending fork %s failed: %s", pending_id, exc)
        return results

    async def get_lineage(self, campaign_id: str) -> dict:
        ancestors = await self.get_lineage_ancestors(campaign_id)
        rows = await self._store.db.fetchall(
            """
            WITH RECURSIVE descendants(id, depth) AS (
                SELECT id, 0 FROM campaigns WHERE id = ?
                UNION ALL
                SELECT c.id, descendants.depth + 1
                  FROM campaigns c
                  JOIN descendants ON c.forked_from_campaign_id = descendants.id
            )
            SELECT c.id, c.name, c.forked_from_campaign_id,
                   c.forked_at_post_id, c.forked_at_turn_id, c.created_at,
                   descendants.depth AS depth
              FROM descendants
              JOIN campaigns c ON c.id = descendants.id
             ORDER BY depth, c.id
            """,
            (campaign_id,),
        )
        return {
            "root": campaign_id,
            "ancestors": ancestors,
            "descendants": [dict(r) for r in rows],
        }

    async def get_lineage_ancestors(self, campaign_id: str) -> list[dict]:
        chain: list[dict] = []
        current: str | None = campaign_id
        seen: set[str] = set()
        while current is not None and current not in seen:
            seen.add(current)
            row = await self._store.db.fetchone(
                """
                SELECT id, name, forked_from_campaign_id,
                       forked_at_post_id, forked_at_turn_id, created_at
                  FROM campaigns
                 WHERE id = ?
                """,
                (current,),
            )
            if row is None:
                break
            chain.append(dict(row))
            current = row["forked_from_campaign_id"]
        return chain

    def _copy_campaign_files(self, src_dir, new_dir) -> None:
        import shutil

        if not src_dir.exists():
            return
        new_dir.mkdir(parents=True, exist_ok=True)
        for child in src_dir.iterdir():
            if child.name == "images":
                continue
            target = new_dir / child.name
            if child.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

    async def _wipe_failed_fork(self, new_campaign_id: str, new_dir) -> None:
        import shutil

        db = self._store.db
        from grimoire.state_store.fork import CAMPAIGN_SCOPED_TABLES

        for spec in CAMPAIGN_SCOPED_TABLES:
            try:
                await db.execute(
                    f"DELETE FROM {spec['table']} WHERE campaign_id = ?",
                    (new_campaign_id,),
                )
            except Exception:
                continue
        with contextlib.suppress(Exception):
            await db.execute("DELETE FROM campaigns WHERE id = ?", (new_campaign_id,))
        if new_dir.exists():
            shutil.rmtree(new_dir, ignore_errors=True)
