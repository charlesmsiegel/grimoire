"""SQLite indexer for the ``scenes`` and ``posts`` tables (§1).

The on-disk markdown + YAML sidecar is the source of truth for scenes; this
indexer mirrors a queryable projection into the State Store's SQLite database
so cross-module reads (`StateStore.list_scenes`, retcon-by-post-id, audit
queries) don't have to walk the filesystem.

Lifecycle:

* :meth:`SceneIndexer.start` subscribes to Scene Manager events on the bus
  (``scene_started``, ``scene_ended``, ``post_appended``, ``post_edited``,
  ``post_deleted``, ``scene_file_changed``) and writes them through as
  ``INSERT OR REPLACE`` against ``scenes`` / ``posts``.
* :meth:`SceneIndexer.backfill` walks ``data/campaigns/<id>/scenes/*.yaml``
  and reconciles every row with disk — this catches direct-edit-while-down
  deltas the live subscription would otherwise miss.

The indexer only depends on a small ``_DB`` protocol so tests can drop in an
aiosqlite connection without standing up the full ``StateStore``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Protocol

from grimoire.scenes.events import (
    POST_APPENDED,
    POST_DELETED,
    POST_EDITED,
    SCENE_DELETED,
    SCENE_ENDED,
    SCENE_FILE_CHANGED,
    SCENE_STARTED,
    SceneEvent,
)
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.storage import (
    content_hash,
    read_posts,
    read_sidecar,
    read_sidecar_post_records,
    scene_paths,
)
from grimoire.scenes.types import AuthorKind, Scene
from grimoire.util import safe_json_dumps

logger = logging.getLogger(__name__)


_BODY_EXCERPT_CHARS = 200


class _DB(Protocol):
    async def execute(self, sql: str, params: tuple = ()) -> None: ...


class _SingleConnDB:
    """Adapts a raw connection to the :class:`_DB` protocol.

    Used by :meth:`SceneIndexer.backfill` so all writes run on a single
    connection inside one transaction instead of acquiring/releasing a
    pooled connection per statement.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await self._conn.execute(sql, params)  # type: ignore[union-attr]


def _excerpt(body: str) -> str:
    body = body.strip()
    if len(body) <= _BODY_EXCERPT_CHARS:
        return body
    return body[: _BODY_EXCERPT_CHARS - 1].rstrip() + "…"


def _threads_to_json(threads: list) -> str | None:
    """Serialize ``list[Thread]`` to JSON for the index row.

    We persist the full thread shape (``text``/``introduced_at_post``/
    ``paid_off_at_post``) — string-list compatibility lives in the sidecar
    parser, not here.
    """
    if not threads:
        return None
    return json.dumps(
        [
            {
                "text": t.text,
                "introduced_at_post": t.introduced_at_post,
                "paid_off_at_post": t.paid_off_at_post,
            }
            for t in threads
        ],
        sort_keys=True,
    )


def _author_kind_str(kind: AuthorKind | str) -> str:
    return kind.value if hasattr(kind, "value") else str(kind)


async def upsert_scene_row(
    db: _DB,
    *,
    scene: Scene,
    file_path: Path,
) -> None:
    """Write or replace the ``scenes`` row for ``scene``.

    The file path is stored relative-style (the manager's md path), so callers
    that resolve to disk don't have to guess the layout. We deliberately don't
    update ``posts`` here — that's a separate per-post call so partial scene
    updates stay cheap.
    """
    await db.execute(
        """
        INSERT INTO scenes (
            id, campaign_id, ordinal, slug, file_path,
            location_ref, in_game_start, in_game_end, pov_character_ref,
            present_character_refs, present_pc_refs,
            summary, running_summary, key_beats, tags, emotional_arc,
            post_count, threads_introduced, threads_paid_off,
            title, greeting_id, closed, closed_at_turn
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            campaign_id = excluded.campaign_id,
            ordinal = excluded.ordinal,
            slug = excluded.slug,
            file_path = excluded.file_path,
            location_ref = excluded.location_ref,
            in_game_start = excluded.in_game_start,
            in_game_end = excluded.in_game_end,
            pov_character_ref = excluded.pov_character_ref,
            present_character_refs = excluded.present_character_refs,
            present_pc_refs = excluded.present_pc_refs,
            summary = excluded.summary,
            running_summary = excluded.running_summary,
            key_beats = excluded.key_beats,
            tags = excluded.tags,
            emotional_arc = excluded.emotional_arc,
            post_count = excluded.post_count,
            threads_introduced = excluded.threads_introduced,
            threads_paid_off = excluded.threads_paid_off,
            title = excluded.title,
            greeting_id = excluded.greeting_id,
            closed = excluded.closed,
            closed_at_turn = excluded.closed_at_turn
        """,
        (
            scene.id,
            scene.campaign_id,
            scene.ordinal,
            scene.slug,
            str(file_path),
            scene.location_ref,
            scene.in_game_start.isoformat() if scene.in_game_start else None,
            scene.in_game_end.isoformat() if scene.in_game_end else None,
            scene.pov_character_ref,
            safe_json_dumps(list(scene.present_character_refs)),
            safe_json_dumps(list(scene.present_pc_refs)),
            scene.final_summary,
            scene.running_summary,
            safe_json_dumps(list(scene.key_beats)),
            safe_json_dumps(list(scene.tags)),
            None,  # emotional_arc not modelled on dataclass Scene
            scene.post_count,
            _threads_to_json(scene.threads_introduced),
            _threads_to_json(scene.threads_paid_off),
            scene.title,
            scene.greeting_id,
            1 if scene.closed else 0,
            scene.closed_at_turn,
        ),
    )


async def upsert_post_row(
    db: _DB,
    *,
    post_id: str,
    scene_id: str,
    campaign_id: str,
    turn_id: str | None,
    order_in_scene: int,
    author_kind: AuthorKind | str,
    author_pc_ref: str | None,
    author_npc_ref: str | None = None,
    body: str,
    is_player: bool,
    created_at: object | None,
) -> None:
    body_excerpt = _excerpt(body)
    body_hash = content_hash(body)
    created_at_str: str | None
    if created_at is None:
        created_at_str = None
    elif hasattr(created_at, "isoformat"):
        created_at_str = created_at.isoformat()
    else:
        created_at_str = str(created_at)
    await db.execute(
        """
        INSERT INTO posts (
            id, scene_id, campaign_id, turn_id, order_in_scene,
            author_kind, author_pc_ref, author_npc_ref, body, body_excerpt, body_hash,
            is_player, created_at, retconned_from
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
            scene_id = excluded.scene_id,
            campaign_id = excluded.campaign_id,
            turn_id = excluded.turn_id,
            order_in_scene = excluded.order_in_scene,
            author_kind = excluded.author_kind,
            author_pc_ref = excluded.author_pc_ref,
            author_npc_ref = excluded.author_npc_ref,
            body = excluded.body,
            body_excerpt = excluded.body_excerpt,
            body_hash = excluded.body_hash,
            is_player = excluded.is_player,
            created_at = excluded.created_at
        """,
        (
            post_id,
            scene_id,
            campaign_id,
            turn_id,
            order_in_scene,
            _author_kind_str(author_kind),
            author_pc_ref,
            author_npc_ref,
            body,
            body_excerpt,
            body_hash,
            1 if is_player else 0,
            created_at_str,
        ),
    )


async def delete_post_row(db: _DB, post_id: str) -> None:
    await db.execute("DELETE FROM posts WHERE id = ?", (post_id,))


async def delete_posts_for_scene(db: _DB, scene_id: str) -> None:
    await db.execute("DELETE FROM posts WHERE scene_id = ?", (scene_id,))


class SceneIndexer:
    """Bus subscriber that keeps ``scenes`` / ``posts`` in sync with the manager.

    The indexer is intentionally write-only — reads still go through
    :class:`SceneManager` or :class:`StateStore` (which now have non-empty
    backing tables to query).
    """

    def __init__(self, manager: SceneManager, db: _DB, bus: object) -> None:
        self._manager = manager
        self._db = db
        self._bus = bus
        self._subs: list[object] = []

    def start(self) -> None:
        if self._subs:
            return
        subscribe = getattr(self._bus, "subscribe", None)
        if subscribe is None:
            return
        for event_type in (
            SCENE_STARTED,
            SCENE_ENDED,
            SCENE_DELETED,
            POST_APPENDED,
            POST_EDITED,
            POST_DELETED,
            SCENE_FILE_CHANGED,
        ):
            self._subs.append(subscribe(event_type, self._on_event))

    async def stop(self) -> None:
        for sub in self._subs:
            unsubscribe = getattr(sub, "unsubscribe", None)
            if callable(unsubscribe):
                unsubscribe()
        self._subs.clear()

    async def _on_event(self, event: SceneEvent) -> None:
        try:
            await self._dispatch(event)
        except Exception:  # pragma: no cover - defensive; bus shouldn't halt
            logger.exception("scene indexer failed for event %s", event.type)

    async def _dispatch(self, event: SceneEvent) -> None:
        scene_id = event.scene_id

        if event.type == SCENE_DELETED:
            await delete_posts_for_scene(self._db, scene_id)
            await self._db.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
            return

        scene = await self._manager.get_scene(scene_id)
        md_path, _ = scene_paths(
            self._manager.data_root,
            scene,
            naming_pattern=self._manager.config.files.scene_naming_pattern,
        )
        await upsert_scene_row(self._db, scene=scene, file_path=md_path)

        if event.type == POST_APPENDED:
            posts = await self._manager.get_posts(scene_id)
            order = int(event.payload.get("order", 0) or 0)
            target = next(
                (p for p in posts if p.order_in_scene == order),
                None,
            )
            if target is None:
                return
            await upsert_post_row(
                self._db,
                post_id=target.id,
                scene_id=scene.id,
                campaign_id=scene.campaign_id,
                turn_id=target.turn_id or None,
                order_in_scene=target.order_in_scene,
                author_kind=target.author_kind,
                author_pc_ref=target.author_pc_ref,
                author_npc_ref=target.author_npc_ref,
                body=target.body,
                is_player=target.is_player,
                created_at=target.created_at,
            )
        elif event.type == POST_EDITED:
            # Edits keep the post id but change the body — rewrite the row.
            posts = await self._manager.get_posts(scene_id)
            order = int(event.payload.get("order", 0) or 0)
            target = next(
                (p for p in posts if p.order_in_scene == order),
                None,
            )
            if target is None:
                return
            await upsert_post_row(
                self._db,
                post_id=target.id,
                scene_id=scene.id,
                campaign_id=scene.campaign_id,
                turn_id=target.turn_id or None,
                order_in_scene=target.order_in_scene,
                author_kind=target.author_kind,
                author_pc_ref=target.author_pc_ref,
                body=target.body,
                is_player=target.is_player,
                created_at=target.created_at,
            )
        elif event.type == POST_DELETED:
            # Deleting one post renumbers everything after it; the simplest
            # consistent path is to drop the scene's rows and re-insert from
            # the live ``get_posts`` list.
            await delete_posts_for_scene(self._db, scene.id)
            for p in await self._manager.get_posts(scene_id):
                await upsert_post_row(
                    self._db,
                    post_id=p.id,
                    scene_id=scene.id,
                    campaign_id=scene.campaign_id,
                    turn_id=p.turn_id or None,
                    order_in_scene=p.order_in_scene,
                    author_kind=p.author_kind,
                    author_pc_ref=p.author_pc_ref,
                    author_npc_ref=p.author_npc_ref,
                    body=p.body,
                    is_player=p.is_player,
                    created_at=p.created_at,
                )
        elif event.type == SCENE_FILE_CHANGED:
            # External edit landed; mirror the full post list to be safe.
            await delete_posts_for_scene(self._db, scene.id)
            for p in await self._manager.get_posts(scene_id):
                await upsert_post_row(
                    self._db,
                    post_id=p.id,
                    scene_id=scene.id,
                    campaign_id=scene.campaign_id,
                    turn_id=p.turn_id or None,
                    order_in_scene=p.order_in_scene,
                    author_kind=p.author_kind,
                    author_pc_ref=p.author_pc_ref,
                    author_npc_ref=p.author_npc_ref,
                    body=p.body,
                    is_player=p.is_player,
                    created_at=p.created_at,
                )

    async def backfill(self) -> None:
        """Walk every scene sidecar on disk and bring the index up to date.

        Called once at startup so direct-edit-while-down deltas (e.g., the
        user edited an .md file outside the app) don't go unindexed. Also
        catches scenes the live subscription missed during a previous crash.

        Writes are batched per-campaign so the write lock is released between
        campaigns, avoiding SQLITE_BUSY for concurrent writers (health
        monitor, embedding worker).
        """
        campaigns_root = self._manager.data_root / "campaigns"
        campaign_dirs = await asyncio.to_thread(
            lambda: (
                [d for d in campaigns_root.iterdir() if d.is_dir()]
                if campaigns_root.exists()
                else []
            )
        )

        acquire = getattr(self._db, "acquire", None)
        for campaign_dir in campaign_dirs:
            scenes_dir_path = campaign_dir / "scenes"
            if acquire is not None:
                async with acquire() as conn:
                    await conn.execute("BEGIN IMMEDIATE")
                    try:
                        db: _DB = _SingleConnDB(conn)
                        await self._backfill_campaign(scenes_dir_path, db)
                        await conn.execute("COMMIT")
                    except Exception:
                        await conn.execute("ROLLBACK")
                        raise
            else:
                await self._backfill_campaign(scenes_dir_path, self._db)

    async def _backfill_campaign(self, scenes_dir_path: Path, db: _DB) -> None:
        yaml_paths = await asyncio.to_thread(
            lambda: sorted(scenes_dir_path.glob("*.yaml")) if scenes_dir_path.exists() else []
        )
        for yaml_path in yaml_paths:
            try:
                scene = await asyncio.to_thread(read_sidecar, yaml_path)
            except Exception:  # pragma: no cover - tolerate corrupt sidecar
                logger.warning("backfill: failed to read sidecar %s", yaml_path)
                continue
            md_path = yaml_path.with_suffix(".md")
            await upsert_scene_row(db, scene=scene, file_path=md_path)
            await delete_posts_for_scene(db, scene.id)
            if not await asyncio.to_thread(md_path.exists):
                continue
            records = await asyncio.to_thread(read_sidecar_post_records, yaml_path)
            posts = await asyncio.to_thread(read_posts, md_path, scene.id)
            for order, kind, pc_ref, npc_ref, body in posts:
                record = records.get(str(order))
                post_id = record.id if record else f"{scene.id}#post-{order}"
                await upsert_post_row(
                    db,
                    post_id=post_id,
                    scene_id=scene.id,
                    campaign_id=scene.campaign_id,
                    turn_id=(record.turn_id if record else None) or None,
                    order_in_scene=order,
                    author_kind=kind,
                    author_pc_ref=pc_ref,
                    author_npc_ref=npc_ref,
                    body=body,
                    is_player=record.is_player if record else False,
                    created_at=record.created_at if record else None,
                )


__all__ = [
    "SceneIndexer",
    "delete_post_row",
    "delete_posts_for_scene",
    "upsert_post_row",
    "upsert_scene_row",
]
