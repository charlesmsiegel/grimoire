"""The :class:`StateStore` — write-side coordinator.

Every domain write goes through this class. The constructor wires a
:class:`grimoire.storage.Database` connection pool together with a data
root path; the methods below mediate file writes and SQLite writes so the
two halves stay coherent.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from grimoire.event_bus import Event, EventBus
from grimoire.files import (
    ParsedDocument,
    load_yaml,
    read_markdown,
    write_markdown,
    write_yaml,
)
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.state_store.delta_log import (
    DeltaRecord,
    get_delta,
    insert_delta,
    list_deltas,
    mark_reversed,
    primary_key_columns,
    reverse_sqlite_delta,
    upsert_row,
    validate_table_columns,
)
from grimoire.state_store.delta_log import queue_for_review as _queue_for_review
from grimoire.state_store.errors import (
    InvalidRefError,
    NotFoundError,
    StateStoreError,
)
from grimoire.state_store.indexers import (
    delete_library_index_row,
    make_library_id,
    upsert_campaign_content_index,
    upsert_library_index,
)
from grimoire.state_store.paths import (
    KIND_TO_DIR,
    campaign_id_for_path,
    campaigns_root,
    content_path,
    emergent_path,
    image_metadata_path,
    library_path,
    override_path,
    parse_library_id,
    sheet_path,
)
from grimoire.state_store.search import (
    SearchHit,
    delete_embeddings_for_ref,
    insert_embedding,
    keyword_search_facts,
    keyword_search_library,
)
from grimoire.state_store.search import vector_search as _vector_search
from grimoire.state_store.snapshots import (
    remove_snapshots_for_world,
    upgrade_snapshots,
    write_snapshots_for_world,
)
from grimoire.storage import Database
from grimoire.util import new_id, now_iso

logger = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _json_loads(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _restore_file(target: Path, before_bytes: bytes | None) -> None:
    """Undo a disk mutation when the surrounding SQL transaction rolled back.

    If the file did not exist beforehand, unlink the new write; otherwise
    restore the original bytes. Used by library and campaign file writes so
    file state and ``library_index`` / ``campaign_content_index`` never drift
    apart when the index update fails.
    """
    if before_bytes is None:
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
    else:
        target.write_bytes(before_bytes)


@dataclass(frozen=True)
class UpgradeReport:
    world_id: str
    diff: dict[str, dict]  # library_id -> {before: int, after: int}

    @property
    def changed_count(self) -> int:
        return len(self.diff)


@dataclass(frozen=True)
class SwapResult:
    """Outcome of :meth:`StateStore.swap_delta_set`."""

    rewound: list[DeltaRecord]
    applied: list[DeltaRecord]


@dataclass(frozen=True)
class FileWriteResult:
    """What :meth:`StateStore.write_library_file` returns to callers."""

    library_id: str
    path: Path
    version: int
    delta_id: str


class StateStore:
    """Authoritative persistence layer.

    The store assumes a connected :class:`Database` (call ``connect()`` and
    apply migrations before constructing). Each public coroutine takes ``source``
    explicitly so deltas carry attribution (e.g. ``"extractor"``,
    ``"mechanics:wod"``, ``"user"``).
    """

    def __init__(
        self,
        db: Database,
        data_root: Path,
        *,
        metrics: MetricsRegistryProtocol = NULL_METRICS,
        event_bus: EventBus | None = None,
    ) -> None:
        self.db = db
        self.data_root = Path(data_root)
        self._metrics: MetricsRegistryProtocol = metrics
        self._bus: EventBus | None = event_bus

    def set_metrics(self, metrics: MetricsRegistryProtocol) -> None:
        self._metrics = metrics

    async def validate_schema(self) -> list[str]:
        """Check _TABLE_COLUMNS against actual DB schema and log warnings."""
        async with self.db.acquire() as conn:
            warnings = await validate_table_columns(conn)
        for w in warnings:
            logger.warning("schema drift: %s", w)
        return warnings

    async def bulk_load_index_mtimes(self) -> dict[str, tuple[str, str]]:
        """Return {relative_path: (file_mtime, content_hash)} for all indexed rows."""
        result: dict[str, tuple[str, str]] = {}
        for row in await self.db.fetchall(
            "SELECT path, file_mtime, content_hash FROM library_index"
        ):
            result[row["path"]] = (row["file_mtime"], row["content_hash"])
        for row in await self.db.fetchall(
            "SELECT path, file_mtime, content_hash FROM campaign_content_index"
        ):
            result[row["path"]] = (row["file_mtime"], row["content_hash"])
        return result

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run a block as a single SQLite transaction on a pooled connection.

        Uses ``BEGIN IMMEDIATE`` so the writer lock is taken at BEGIN time.
        Deferred BEGIN + read + write upgrades return SQLITE_BUSY immediately
        in WAL mode (the busy handler is not invoked for snapshot upgrades),
        so deferred transactions race against background writers.
        """
        async with (
            self._metrics.measure("state_store", "write"),
            self.db.acquire() as conn,
        ):
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # Library file writes
    # ------------------------------------------------------------------

    async def write_library_file(
        self,
        *,
        library_id: str,
        frontmatter: dict,
        body: str,
        source: str,
        campaign_id: str | None = None,
        turn_id: str | None = None,
    ) -> FileWriteResult:
        """Write a library entity file and update ``library_index`` atomically.

        Returns the version after the write. Records a ``library_file_write``
        delta so the change is reversible.
        """
        ref = parse_library_id(library_id)
        target = library_path(self.data_root, library_id)
        target.parent.mkdir(parents=True, exist_ok=True)

        before_payload: dict | None
        # Snapshot the prior on-disk bytes (or absence) so we can restore on
        # SQL rollback — otherwise the file would be left mutated while the
        # index reflects the old state. See BUGS.md ("apply_delta path leaves
        # orphan files when SQL rolls back").
        if target.exists():
            before_bytes: bytes | None = target.read_bytes()
            if ref.kind in {"world", "image_preset"}:
                before_data = load_yaml(target) or {}
                before_payload = {"frontmatter": before_data, "body": ""}
            else:
                doc = read_markdown(target)
                before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}
        else:
            before_bytes = None
            before_payload = None

        # Write the file.
        if ref.kind in {"world", "image_preset"}:
            write_yaml(target, frontmatter)
            body = ""
        else:
            write_markdown(target, ParsedDocument(frontmatter=frontmatter, body=body))

        try:
            async with self._txn() as conn:
                version = await upsert_library_index(
                    conn,
                    data_root=self.data_root,
                    library_id=library_id,
                    path=target,
                    frontmatter=frontmatter,
                    body=body,
                )
                delta_id = await insert_delta(
                    conn,
                    campaign_id=campaign_id,
                    branch_id=None,
                    turn_id=turn_id,
                    source=source,
                    kind="library_file_write",
                    target_scope="library",
                    target_table=None,
                    target_path=str(target),
                    target_id=library_id,
                    before=before_payload,
                    after={"frontmatter": frontmatter, "body": body, "version": version},
                )
        except BaseException:
            _restore_file(target, before_bytes)
            raise

        if self._bus is not None:
            await self._bus.emit(
                Event(
                    type="library_entity_changed",
                    payload={"library_id": library_id, "kind": ref.kind},
                )
            )

        return FileWriteResult(
            library_id=library_id,
            path=target,
            version=version,
            delta_id=delta_id,
        )

    async def delete_library_file(
        self,
        *,
        library_id: str,
        source: str,
        campaign_id: str | None = None,
    ) -> str:
        """Delete a library entity file and remove its index row.

        Returns the delta id. The deleted content is captured in ``before`` so
        the deletion is reversible.
        """
        ref = parse_library_id(library_id)
        target = library_path(self.data_root, library_id)
        if not target.exists():
            raise NotFoundError(f"library file does not exist: {library_id}")

        # Snapshot the file bytes before unlinking so an SQL rollback can
        # put the file back (see write_library_file's matching comment).
        before_bytes = target.read_bytes()
        if ref.kind in {"world", "image_preset"}:
            before_data = load_yaml(target) or {}
            before_payload = {"frontmatter": before_data, "body": ""}
        else:
            doc = read_markdown(target)
            before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}

        target.unlink()

        try:
            async with self._txn() as conn:
                await delete_library_index_row(conn, library_id)
                delta_id = await insert_delta(
                    conn,
                    campaign_id=campaign_id,
                    branch_id=None,
                    turn_id=None,
                    source=source,
                    kind="library_file_delete",
                    target_scope="library",
                    target_table=None,
                    target_path=str(target),
                    target_id=library_id,
                    before=before_payload,
                    after=None,
                )
        except BaseException:
            _restore_file(target, before_bytes)
            raise

        if self._bus is not None:
            await self._bus.emit(
                Event(
                    type="library_entity_changed",
                    payload={"library_id": library_id, "kind": ref.kind},
                )
            )

        return delta_id

    # ------------------------------------------------------------------
    # Campaign content file writes
    # ------------------------------------------------------------------

    async def write_override(
        self,
        *,
        campaign_id: str,
        library_id: str,
        patch: dict,
        source: str,
        turn_id: str | None = None,
    ) -> Path:
        ref = parse_library_id(library_id)
        if ref.world_id is None:
            raise InvalidRefError("overrides are only valid for world-scoped library entities")
        target = override_path(self.data_root, campaign_id, ref.world_id, ref.kind, ref.asset_id)
        before_payload: dict | None = None
        if target.exists():
            before_payload = load_yaml(target) or {}

        write_yaml(target, patch)

        composite_id = f"campaigns/{campaign_id}/overrides/{library_id}"
        async with self._txn() as conn:
            await upsert_campaign_content_index(
                conn,
                data_root=self.data_root,
                campaign_id=campaign_id,
                composite_id=composite_id,
                kind="override",
                entity_subkind=ref.kind,
                asset_id=ref.asset_id,
                path=target,
                frontmatter=patch,
                body=None,
            )
            await insert_delta(
                conn,
                campaign_id=campaign_id,
                branch_id=None,
                turn_id=turn_id,
                source=source,
                kind="override_write",
                target_scope="campaign-file",
                target_table=None,
                target_path=str(target),
                target_id=composite_id,
                before=before_payload,
                after=patch,
            )
        return target

    async def delete_override(
        self,
        *,
        campaign_id: str,
        library_id: str,
        source: str,
        turn_id: str | None = None,
    ) -> bool:
        """Delete a campaign-local override file and its index row.

        Symmetrical with :meth:`write_override`. Returns ``True`` when the
        file existed and was deleted, ``False`` when nothing was on disk.
        """
        ref = parse_library_id(library_id)
        if ref.world_id is None:
            raise InvalidRefError("overrides are only valid for world-scoped library entities")
        target = override_path(self.data_root, campaign_id, ref.world_id, ref.kind, ref.asset_id)
        if not target.exists():
            return False
        before_payload = load_yaml(target) or {}
        target.unlink()

        composite_id = f"campaigns/{campaign_id}/overrides/{library_id}"
        async with self._txn() as conn:
            from grimoire.state_store.indexers import delete_campaign_content_row

            await delete_campaign_content_row(conn, composite_id)
            await insert_delta(
                conn,
                campaign_id=campaign_id,
                branch_id=None,
                turn_id=turn_id,
                source=source,
                kind="override_delete",
                target_scope="campaign-file",
                target_table=None,
                target_path=str(target),
                target_id=composite_id,
                before=before_payload,
                after=None,
            )
        return True

    async def delete_emergent(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        source: str,
        turn_id: str | None = None,
    ) -> bool:
        """Delete a campaign-local emergent file and its index row.

        Symmetrical with :meth:`write_emergent`. Returns ``True`` when the
        file existed and was deleted, ``False`` when nothing was on disk.
        The deletion is captured in ``before`` of an ``emergent_delete``
        delta so it remains reversible.
        """
        target = emergent_path(self.data_root, campaign_id, kind, entity_id)
        if not target.exists():
            return False
        doc = read_markdown(target)
        before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}
        target.unlink()

        composite_id = f"campaigns/{campaign_id}/emergent/{kind}/{entity_id}"
        async with self._txn() as conn:
            from grimoire.state_store.indexers import delete_campaign_content_row

            await delete_campaign_content_row(conn, composite_id)
            await insert_delta(
                conn,
                campaign_id=campaign_id,
                branch_id=None,
                turn_id=turn_id,
                source=source,
                kind="emergent_delete",
                target_scope="campaign-file",
                target_table=None,
                target_path=str(target),
                target_id=composite_id,
                before=before_payload,
                after=None,
            )
        return True

    async def write_emergent(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        frontmatter: dict,
        body: str,
        source: str,
        turn_id: str | None = None,
    ) -> Path:
        target = emergent_path(self.data_root, campaign_id, kind, entity_id)
        before_payload: dict | None = None
        if target.exists():
            doc = read_markdown(target)
            before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}

        write_markdown(target, ParsedDocument(frontmatter=frontmatter, body=body))

        composite_id = f"campaigns/{campaign_id}/emergent/{kind}/{entity_id}"
        async with self._txn() as conn:
            await upsert_campaign_content_index(
                conn,
                data_root=self.data_root,
                campaign_id=campaign_id,
                composite_id=composite_id,
                kind="emergent",
                entity_subkind=kind,
                asset_id=entity_id,
                path=target,
                frontmatter=frontmatter,
                body=body,
            )
            await insert_delta(
                conn,
                campaign_id=campaign_id,
                branch_id=None,
                turn_id=turn_id,
                source=source,
                kind="emergent_create" if before_payload is None else "emergent_update",
                target_scope="campaign-file",
                target_table=None,
                target_path=str(target),
                target_id=composite_id,
                before=before_payload,
                after={"frontmatter": frontmatter, "body": body},
            )
        return target

    async def write_sheet(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        mechanics_id: str,
        sheet: dict,
        source: str,
        turn_id: str | None = None,
    ) -> Path:
        target = sheet_path(self.data_root, campaign_id, kind, entity_id, mechanics_id)
        before_payload: dict | None = None
        if target.exists():
            before_payload = load_yaml(target) or {}

        write_yaml(target, sheet)

        composite_id = f"campaigns/{campaign_id}/sheets/{kind}/{entity_id}.{mechanics_id}"
        async with self._txn() as conn:
            await upsert_campaign_content_index(
                conn,
                data_root=self.data_root,
                campaign_id=campaign_id,
                composite_id=composite_id,
                kind="sheet",
                entity_subkind=kind,
                asset_id=entity_id,
                path=target,
                frontmatter=sheet,
                body=None,
            )
            await insert_delta(
                conn,
                campaign_id=campaign_id,
                branch_id=None,
                turn_id=turn_id,
                source=source,
                kind="sheet_update",
                target_scope="campaign-file",
                target_table=None,
                target_path=str(target),
                target_id=composite_id,
                before=before_payload,
                after=sheet,
            )
        return target

    async def write_content(
        self,
        *,
        campaign_id: str,
        kind: str,
        content_id: str,
        mechanics_id: str,
        payload: dict,
        source: str,
        turn_id: str | None = None,
    ) -> Path:
        """Persist a mechanics content instance to disk and index it."""
        target = content_path(self.data_root, campaign_id, kind, content_id, mechanics_id)
        before_payload: dict | None = None
        if target.exists():
            before_payload = load_yaml(target) or {}

        write_yaml(target, payload)

        composite_id = f"campaigns/{campaign_id}/content/{kind}/{content_id}.{mechanics_id}"
        async with self._txn() as conn:
            await upsert_campaign_content_index(
                conn,
                data_root=self.data_root,
                campaign_id=campaign_id,
                composite_id=composite_id,
                kind="content",
                entity_subkind=kind,
                asset_id=content_id,
                path=target,
                frontmatter=payload,
                body=None,
            )
            await insert_delta(
                conn,
                campaign_id=campaign_id,
                branch_id=None,
                turn_id=turn_id,
                source=source,
                kind="content_update",
                target_scope="campaign-file",
                target_table=None,
                target_path=str(target),
                target_id=composite_id,
                before=before_payload,
                after=payload,
            )
        return target

    async def get_content(
        self,
        campaign_id: str,
        kind: str,
        content_id: str,
        mechanics_id: str,
    ) -> dict | None:
        target = content_path(self.data_root, campaign_id, kind, content_id, mechanics_id)
        if not target.exists():
            return None
        return load_yaml(target) or {}

    async def list_content(
        self,
        campaign_id: str,
        kind: str,
        mechanics_id: str,
    ) -> list[dict]:
        """Return every content instance of ``kind`` for ``mechanics_id``.

        Reads from the filesystem (rather than the index) so callers see
        files even when the index hasn't been re-scanned.
        """
        # Reuse the validated path so all id components are checked.
        base = content_path(self.data_root, campaign_id, kind, "x", mechanics_id).parent
        if not base.is_dir():
            return []
        suffix = f".{mechanics_id}.yaml"
        out: list[dict] = []
        for entry in sorted(base.iterdir()):
            if not entry.is_file() or not entry.name.endswith(suffix):
                continue
            content_id = entry.name[: -len(suffix)]
            payload = load_yaml(entry) or {}
            out.append({"id": content_id, "payload": payload, "mechanics_id": mechanics_id})
        return out

    async def write_image_metadata(
        self,
        *,
        campaign_id: str,
        image_id: str,
        metadata: dict,
        source: str,
        turn_id: str | None = None,
    ) -> Path:
        target = image_metadata_path(self.data_root, campaign_id, image_id)
        before_payload: dict | None = None
        if target.exists():
            before_payload = load_yaml(target) or {}

        write_yaml(target, metadata)

        composite_id = f"campaigns/{campaign_id}/images/{image_id}"
        async with self._txn() as conn:
            await upsert_campaign_content_index(
                conn,
                data_root=self.data_root,
                campaign_id=campaign_id,
                composite_id=composite_id,
                kind="image",
                entity_subkind=None,
                asset_id=image_id,
                path=target,
                frontmatter=metadata,
                body=None,
            )
            await insert_delta(
                conn,
                campaign_id=campaign_id,
                branch_id=metadata.get("branch_id"),
                turn_id=turn_id,
                source=source,
                kind="image_metadata_write",
                target_scope="campaign-file",
                target_table=None,
                target_path=str(target),
                target_id=composite_id,
                before=before_payload,
                after=metadata,
            )
        return target

    # ------------------------------------------------------------------
    # Reads — library + campaign content
    # ------------------------------------------------------------------

    async def set_body_compressed(
        self,
        library_id: str,
        text: str | None,
        *,
        expected_content_hash: str | None = None,
    ) -> bool:
        """Write the auto-summary into ``library_index.body_compressed``.

        Spec 18 §Indexing + remaining-design §1. The optional
        ``expected_content_hash`` guard lets a worker pass the
        content_hash it summarized against; the update is rejected when
        the row's hash has since drifted (someone re-wrote the file in
        between, invalidating the summary).

        Returns ``True`` when the row was updated, ``False`` when the
        guard rejected the write or the row no longer exists.
        """
        async with self.db.acquire() as conn:
            if expected_content_hash is None:
                cursor = await conn.execute(
                    "UPDATE library_index SET body_compressed = ? WHERE id = ?",
                    (text, library_id),
                )
            else:
                cursor = await conn.execute(
                    """
                    UPDATE library_index SET body_compressed = ?
                    WHERE id = ? AND content_hash = ?
                    """,
                    (text, library_id, expected_content_hash),
                )
            return bool(cursor.rowcount and cursor.rowcount > 0)

    async def get_library_entity(self, library_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM library_index WHERE id = ?", (library_id,))
        if row is None:
            return None
        return _library_row_to_dict(row)

    async def list_library_in_world(self, world_id: str, kind: str | None = None) -> list[dict]:
        if kind is None:
            rows = await self.db.fetchall(
                "SELECT * FROM library_index WHERE world_id = ? ORDER BY name",
                (world_id,),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM library_index WHERE world_id = ? AND kind = ? ORDER BY name",
                (world_id, kind),
            )
        return [_library_row_to_dict(row) for row in rows]

    async def variants_of(self, asset_id: str, kind: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM library_index WHERE asset_id = ? AND kind = ? ORDER BY world_id",
            (asset_id, kind),
        )
        return [_library_row_to_dict(row) for row in rows]

    async def get_override(self, campaign_id: str, library_id: str) -> dict | None:
        ref = parse_library_id(library_id)
        if ref.world_id is None:
            return None
        target = override_path(self.data_root, campaign_id, ref.world_id, ref.kind, ref.asset_id)
        if not target.exists():
            return None
        return load_yaml(target) or {}

    async def get_emergent(self, campaign_id: str, kind: str, entity_id: str) -> dict | None:
        target = emergent_path(self.data_root, campaign_id, kind, entity_id)
        if not target.exists():
            return None
        doc = read_markdown(target)
        return {"frontmatter": doc.frontmatter, "body": doc.body}

    async def list_emergent(self, campaign_id: str, kind: str) -> list[dict]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM campaign_content_index
            WHERE campaign_id = ? AND kind = 'emergent' AND entity_subkind = ?
            ORDER BY asset_id
            """,
            (campaign_id, kind),
        )
        return [_content_row_to_dict(row) for row in rows]

    async def get_sheet(
        self,
        campaign_id: str,
        kind: str,
        entity_id: str,
        mechanics_id: str,
    ) -> dict | None:
        target = sheet_path(self.data_root, campaign_id, kind, entity_id, mechanics_id)
        if not target.exists():
            return None
        return load_yaml(target) or {}

    async def list_sheet_entity_ids(
        self,
        campaign_id: str,
        kind: str,
        mechanics_id: str,
    ) -> set[str]:
        """Return the entity_ids that already have a sheet on disk.

        Single directory scan, intended for bulk existence checks (e.g. the
        ``bulk_create_missing_sheets`` API) where calling :meth:`get_sheet`
        per entity would do N individual ``stat`` calls.
        """
        dir_name = KIND_TO_DIR.get(kind, kind)
        sheets_dir = campaigns_root(self.data_root) / campaign_id / "sheets" / dir_name
        if not sheets_dir.is_dir():
            return set()
        suffix = f".{mechanics_id}.yaml"
        return {p.name[: -len(suffix)] for p in sheets_dir.iterdir() if p.name.endswith(suffix)}

    async def list_scenes(self, campaign_id: str, branch_id: str | None = None) -> list[dict]:
        async with self._metrics.measure("state_store", "query"):
            return await self._list_scenes_inner(campaign_id, branch_id)

    async def _list_scenes_inner(
        self, campaign_id: str, branch_id: str | None = None
    ) -> list[dict]:
        if branch_id is None:
            rows = await self.db.fetchall(
                "SELECT * FROM scenes WHERE campaign_id = ? ORDER BY ordinal",
                (campaign_id,),
            )
        else:
            rows = await self.db.fetchall(
                """
                SELECT * FROM scenes WHERE campaign_id = ? AND branch_id = ?
                ORDER BY ordinal
                """,
                (campaign_id, branch_id),
            )
        return [_scene_row_to_dict(row) for row in rows]

    async def get_scene_metadata(self, scene_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM scenes WHERE id = ?", (scene_id,))
        return _scene_row_to_dict(row) if row else None

    # ------------------------------------------------------------------
    # Repository queries — common cross-service patterns
    # ------------------------------------------------------------------

    async def list_library_by_kind(self, kind: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM library_index WHERE kind = ? ORDER BY name",
            (kind,),
        )
        return [_library_row_to_dict(row) for row in rows]

    async def get_campaign_row(self, campaign_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        return dict(row) if row else None

    async def get_campaign_config(self, campaign_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT config FROM campaigns WHERE id = ?", (campaign_id,))
        if row is None:
            return None
        return _json_loads(row["config"])

    async def campaign_exists(self, campaign_id: str) -> bool:
        row = await self.db.fetchone("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,))
        return row is not None

    async def count_deltas(self, campaign_id: str) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS cnt FROM deltas WHERE campaign_id = ?",
            (campaign_id,),
        )
        return int(row["cnt"]) if row else 0

    # ------------------------------------------------------------------
    # Composition-aware resolve cascade
    # ------------------------------------------------------------------

    async def resolve_entity(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        kind: str,
        asset_id: str,
        world_id: str | None = None,
    ) -> dict | None:
        """Cascade: campaign override → snapshot (pinned) / live index → None.

        ``world_id=None`` means try campaign emergent first (the entity is
        campaign-local). Pinned vs live is determined per world ref.
        """
        if world_id is None:
            emergent = await self.get_emergent(campaign_id, kind, asset_id)
            if emergent is not None:
                return {
                    "source": "campaign-emergent",
                    "frontmatter": emergent["frontmatter"],
                    "body": emergent["body"],
                }
            return None

        library_id = make_library_id(world_id, kind, asset_id)

        override = await self.get_override(campaign_id, library_id)
        if override is not None:
            base = await self._resolve_world_base(
                campaign_id=campaign_id,
                branch_id=branch_id,
                world_id=world_id,
                library_id=library_id,
            )
            merged = dict(base or {})
            merged_fm = dict(merged.get("frontmatter") or {})
            # Narrative extras (frontmatter['extras']) need a key-by-key
            # merge so an override on one key doesn't drop unrelated
            # library keys. None entries in the override are "override-
            # null" tombstones that delete the cascaded library value.
            base_extras = dict(merged_fm.get("extras") or {})
            override_extras = override.get("extras")
            merged_fm.update(override)
            if base_extras or isinstance(override_extras, dict):
                merged_extras = dict(base_extras)
                for key, value in (override_extras or {}).items():
                    if value is None:
                        merged_extras.pop(key, None)
                    else:
                        merged_extras[key] = value
                if merged_extras:
                    merged_fm["extras"] = merged_extras
                else:
                    merged_fm.pop("extras", None)
            merged["frontmatter"] = merged_fm
            merged["source"] = "campaign-override"
            merged["override"] = override
            return merged

        return await self._resolve_world_base(
            campaign_id=campaign_id,
            branch_id=branch_id,
            world_id=world_id,
            library_id=library_id,
        )

    async def _resolve_world_base(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        world_id: str,
        library_id: str,
    ) -> dict | None:
        """Find the base data for a library entity, snapshot-first when pinned."""
        ref_row = await self.db.fetchone(
            """
            SELECT track_latest FROM campaign_world_refs
            WHERE campaign_id = ? AND world_id = ?
            """,
            (campaign_id, world_id),
        )
        prefer_snapshot = bool(ref_row and not int(ref_row["track_latest"]))

        if prefer_snapshot:
            # Walk the branch chain (child → parent → ... → main) so a forked
            # branch reads its parent's snapshot row. Without this, a fork
            # made when ``track_latest=False`` would have no snapshot rows of
            # its own and fall back to the live index — picking up library
            # upgrades that happened on main after the fork, which defeats
            # the pinning semantics. See §3 of the library remaining-design.
            chain = await self.branch_chain(branch_id)
            placeholders = ",".join("?" * len(chain))
            snap = await self.db.fetchone(
                f"""
                SELECT branch_id, version, frontmatter, body FROM library_snapshots
                WHERE campaign_id = ? AND library_id = ?
                  AND branch_id IN ({placeholders})
                ORDER BY CASE branch_id
                  {" ".join(f"WHEN ? THEN {i}" for i in range(len(chain)))}
                END ASC
                LIMIT 1
                """,
                (campaign_id, library_id, *chain, *chain),
            )
            if snap is not None:
                return {
                    "source": "library-snapshot",
                    "library_id": library_id,
                    "version": int(snap["version"]),
                    "frontmatter": _json_loads(snap["frontmatter"]) or {},
                    "body": snap["body"] or "",
                }

        row = await self.db.fetchone("SELECT * FROM library_index WHERE id = ?", (library_id,))
        if row is None:
            return None
        return {
            "source": "library-live" if not prefer_snapshot else "library-fallback",
            "library_id": library_id,
            "version": int(row["version"]),
            "frontmatter": _json_loads(row["frontmatter"]) or {},
            "body": row["body"] or "",
        }

    # ------------------------------------------------------------------
    # Composition (world refs + PCs)
    # ------------------------------------------------------------------

    async def upsert_campaign(
        self,
        *,
        campaign_id: str,
        name: str,
        description: str | None = None,
        mechanics_module: str | None = None,
        style_guide_id: str | None = None,
        image_preset_id: str | None = None,
        inline_style_guide: str | None = None,
        content_boundaries: str | None = None,
        greeting_id: str | None = None,
        tags: list[str] | None = None,
        config: dict | None = None,
    ) -> None:
        tags_json = _json_dumps(list(tags)) if tags is not None else None
        await self.db.execute(
            """
            INSERT INTO campaigns (
              id, name, description, mechanics_module, style_guide_id, image_preset_id,
              inline_style_guide, content_boundaries, greeting_id, tags, created_at,
              last_played_at, config
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              description = excluded.description,
              mechanics_module = excluded.mechanics_module,
              style_guide_id = excluded.style_guide_id,
              image_preset_id = excluded.image_preset_id,
              inline_style_guide = excluded.inline_style_guide,
              content_boundaries = excluded.content_boundaries,
              greeting_id = excluded.greeting_id,
              tags = excluded.tags,
              config = excluded.config
            """,
            (
                campaign_id,
                name,
                description,
                mechanics_module,
                style_guide_id,
                image_preset_id,
                inline_style_guide,
                content_boundaries,
                greeting_id,
                tags_json,
                now_iso(),
                _json_dumps(config) if config is not None else None,
            ),
        )
        # Ensure 'main' branch exists.
        await self.db.execute(
            """
            INSERT OR IGNORE INTO branches (id, campaign_id, parent_branch_id,
              forked_from_turn_id, label, rng_seed, created_at)
            VALUES (?, ?, NULL, NULL, 'main', ?, ?)
            """,
            (f"{campaign_id}:main", campaign_id, _seed_for("main"), now_iso()),
        )

    async def record_mechanics_switch(
        self,
        *,
        campaign_id: str,
        previous: str | None,
        current: str | None,
        source: str = "user",
    ) -> None:
        """Append a row to ``campaign_mechanics_history``.

        Both ``previous`` and ``current`` may be ``None`` (a campaign with
        ``mechanics: null``). The timestamp is generated here so callers
        don't need to coordinate clocks.
        """
        await self.db.execute(
            """
            INSERT INTO campaign_mechanics_history
              (campaign_id, mechanics_module, switched_at, switched_from, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (campaign_id, current, now_iso(), previous, source),
        )

    async def previous_mechanics_modules(self, campaign_id: str) -> list[str]:
        """Return every distinct non-null mechanics_module ever set on ``campaign_id``."""
        rows = await self.db.fetchall(
            """
            SELECT DISTINCT mechanics_module FROM campaign_mechanics_history
            WHERE campaign_id = ? AND mechanics_module IS NOT NULL
            UNION
            SELECT DISTINCT switched_from FROM campaign_mechanics_history
            WHERE campaign_id = ? AND switched_from IS NOT NULL
            """,
            (campaign_id, campaign_id),
        )
        return [row[0] for row in rows if row[0]]

    async def upsert_world_ref(
        self,
        *,
        campaign_id: str,
        world_id: str,
        priority: int,
        include: list[str] | None,
        track_latest: bool,
        bound_at_version: int | None = None,
        snapshot_on_bind: bool = True,
    ) -> None:
        if bound_at_version is None:
            row = await self.db.fetchone(
                "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
                (world_id,),
            )
            bound_at_version = int(row["v"] or 0) if row else 0

        async with self._txn() as conn:
            await conn.execute(
                """
                INSERT INTO campaign_world_refs (
                  campaign_id, world_id, priority, include, bound_at_version,
                  track_latest, bound_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, world_id) DO UPDATE SET
                  priority = excluded.priority,
                  include = excluded.include,
                  bound_at_version = excluded.bound_at_version,
                  track_latest = excluded.track_latest,
                  bound_at = excluded.bound_at
                """,
                (
                    campaign_id,
                    world_id,
                    priority,
                    json.dumps(list(include), sort_keys=True) if include is not None else None,
                    bound_at_version,
                    1 if track_latest else 0,
                    now_iso(),
                ),
            )
            if not track_latest and snapshot_on_bind:
                await write_snapshots_for_world(
                    conn,
                    campaign_id=campaign_id,
                    branch_id=f"{campaign_id}:main",
                    world_id=world_id,
                    include=list(include) if include else None,
                )
            else:
                await remove_snapshots_for_world(
                    conn,
                    campaign_id=campaign_id,
                    branch_id=f"{campaign_id}:main",
                    world_id=world_id,
                )

    async def upgrade_world_ref(
        self,
        *,
        campaign_id: str,
        world_id: str,
    ) -> UpgradeReport:
        ref_row = await self.db.fetchone(
            """
            SELECT include, track_latest FROM campaign_world_refs
            WHERE campaign_id = ? AND world_id = ?
            """,
            (campaign_id, world_id),
        )
        if ref_row is None:
            raise NotFoundError(f"campaign {campaign_id!r} does not bind world {world_id!r}")
        if int(ref_row["track_latest"]):
            return UpgradeReport(world_id=world_id, diff={})
        include = _json_loads(ref_row["include"]) or None

        max_row = await self.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        new_max = int(max_row["v"] or 0) if max_row else 0
        async with self._txn() as conn:
            diff = await upgrade_snapshots(
                conn,
                campaign_id=campaign_id,
                branch_id=f"{campaign_id}:main",
                world_id=world_id,
                include=include,
            )
            await conn.execute(
                """
                UPDATE campaign_world_refs
                SET bound_at_version = ?, bound_at = ?
                WHERE campaign_id = ? AND world_id = ?
                """,
                (new_max, now_iso(), campaign_id, world_id),
            )
        return UpgradeReport(world_id=world_id, diff=diff)

    async def add_pc(
        self,
        *,
        campaign_id: str,
        character_ref: str,
        display_name: str,
        owner: str = "local",
    ) -> None:
        # First PC in the campaign becomes active; subsequent PCs are added
        # inactive so at most one row per campaign has active=1. set_active_pc
        # is the only place that flips the bit afterwards.
        row = await self.db.fetchone(
            "SELECT 1 FROM campaign_pcs WHERE campaign_id = ? LIMIT 1",
            (campaign_id,),
        )
        active_default = 0 if row else 1
        await self.db.execute(
            """
            INSERT INTO campaign_pcs (
              campaign_id, character_ref, display_name, owner, active, added_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, character_ref) DO UPDATE SET
              display_name = excluded.display_name,
              owner = excluded.owner
            """,
            (campaign_id, character_ref, display_name, owner, active_default, now_iso()),
        )

    async def remove_pc(self, *, campaign_id: str, character_ref: str) -> None:
        await self.db.execute(
            "DELETE FROM campaign_pcs WHERE campaign_id = ? AND character_ref = ?",
            (campaign_id, character_ref),
        )

    async def set_active_pc(self, *, campaign_id: str, character_ref: str) -> None:
        """Atomically mark exactly one PC in the campaign as active."""
        async with self._txn() as conn:
            await conn.execute(
                "UPDATE campaign_pcs SET active = 0 WHERE campaign_id = ?",
                (campaign_id,),
            )
            await conn.execute(
                "UPDATE campaign_pcs SET active = 1 WHERE campaign_id = ? AND character_ref = ?",
                (campaign_id, character_ref),
            )

    async def list_pcs(self, campaign_id: str) -> list[dict]:
        async with self._metrics.measure("state_store", "query"):
            return await self._list_pcs_inner(campaign_id)

    async def _list_pcs_inner(self, campaign_id: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM campaign_pcs WHERE campaign_id = ? ORDER BY added_at",
            (campaign_id,),
        )
        return [dict(row) for row in rows]

    async def mark_pc_played(self, *, campaign_id: str, character_ref: str) -> None:
        """Stamp ``last_played_at`` on the PC row.

        Called by the orchestrator whenever a turn runs for a PC so the
        rich PC switcher can show "last played 12m ago" rows. A no-op if
        the PC is not registered for the campaign.
        """
        await self.db.execute(
            "UPDATE campaign_pcs SET last_played_at = ? "
            "WHERE campaign_id = ? AND character_ref = ?",
            (now_iso(), campaign_id, character_ref),
        )

    async def list_world_refs(self, campaign_id: str) -> list[dict]:
        async with self._metrics.measure("state_store", "query"):
            return await self._list_world_refs_inner(campaign_id)

    async def _list_world_refs_inner(self, campaign_id: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM campaign_world_refs WHERE campaign_id = ? ORDER BY priority",
            (campaign_id,),
        )
        return [
            {
                "campaign_id": row["campaign_id"],
                "world_id": row["world_id"],
                "priority": int(row["priority"]),
                # Preserve None ("missing => include all kinds") vs [] ("include
                # nothing"). The library service distinguishes these now.
                "include": _json_loads(row["include"]) if row["include"] is not None else None,
                "bound_at_version": int(row["bound_at_version"]),
                "track_latest": bool(int(row["track_latest"])),
                "bound_at": row["bound_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Branches and fork
    # ------------------------------------------------------------------

    async def fork_branch(
        self,
        *,
        campaign_id: str,
        parent_branch_id: str,
        new_label: str,
        at_turn_id: str | None = None,
    ) -> str:
        """Create a new branch row that points at ``parent_branch_id``.

        Reads against the new branch fall back to the parent for rows the
        new branch hasn't yet written. Snapshot rows are shared.
        """
        new_id = f"{campaign_id}:{new_label}"
        async with self._txn() as conn:
            cur = await conn.execute("SELECT id FROM branches WHERE id = ?", (parent_branch_id,))
            if (await cur.fetchone()) is None:
                await cur.close()
                raise NotFoundError(f"parent branch {parent_branch_id!r} not found")
            await cur.close()
            await conn.execute(
                """
                INSERT INTO branches (
                  id, campaign_id, parent_branch_id, forked_from_turn_id, label,
                  rng_seed, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    campaign_id,
                    parent_branch_id,
                    at_turn_id,
                    new_label,
                    _seed_for(new_label),
                    now_iso(),
                ),
            )
        return new_id

    async def branch_chain(self, branch_id: str) -> list[str]:
        """Return ``[branch_id, parent, grandparent, ...]`` for CoW reads."""
        chain: list[str] = []
        current: str | None = branch_id
        while current is not None:
            chain.append(current)
            row = await self.db.fetchone(
                "SELECT parent_branch_id FROM branches WHERE id = ?", (current,)
            )
            if row is None:
                break
            current = row["parent_branch_id"]
        return chain

    async def resolve_character_state(
        self,
        *,
        character_ref: str,
        branch_id: str,
    ) -> dict | None:
        """CoW read: walk parents until a matching row is found."""
        for bid in await self.branch_chain(branch_id):
            row = await self.db.fetchone(
                """
                SELECT * FROM character_state
                WHERE character_ref = ? AND branch_id = ?
                """,
                (character_ref, bid),
            )
            if row is not None:
                return _character_state_row_to_dict(row)
        return None

    async def list_tier_pins(
        self,
        *,
        campaign_id: str,
        branch_id: str,
    ) -> dict[str, str]:
        """Return ``{character_ref: tier_pin}`` across the branch chain.

        CoW semantics: the topmost branch's value wins. Only characters with
        a non-null ``tier_pin`` appear in the result. Intended for callers
        that need pins for many characters at once (e.g. the Characters
        service's tier computation) so they avoid one query per character.

        .. warning::

           The ``WHERE tier_pin IS NOT NULL`` filter assumes no caller ever
           writes an explicit ``NULL`` ``tier_pin`` on a child branch while
           an ancestor branch has one set. Today this holds because there
           is no "unpin" API and ``_load_state`` always carries the parent
           value forward when CoW'ing a row. If an unpin path is ever
           added, this batch reader will silently disagree with the
           single-row :meth:`resolve_character_state` reader, which honors
           the topmost row regardless of NULL. Adjust the query (e.g. fetch
           all rows and let ``NULL`` evict an ancestor's value) before
           introducing such an API.
        """
        seen: dict[str, str] = {}
        for bid in await self.branch_chain(branch_id):
            rows = await self.db.fetchall(
                """
                SELECT character_ref, tier_pin FROM character_state
                WHERE campaign_id = ? AND branch_id = ? AND tier_pin IS NOT NULL
                """,
                (campaign_id, bid),
            )
            for row in rows:
                ref = row["character_ref"]
                if ref not in seen:
                    seen[ref] = row["tier_pin"]
        return seen

    # ------------------------------------------------------------------
    # Delta log
    # ------------------------------------------------------------------

    async def apply_delta(
        self,
        *,
        delta: dict | Any,
        source: str | None = None,
        turn_id: str | None = None,
        branch_id: str | None = None,
        campaign_id: str | None = None,
        delta_set_id: str | None = None,
    ) -> str:
        """Apply a delta to the SQLite layer and record it in ``deltas``.

        Accepts either a ``StateDelta`` pydantic model (from
        ``grimoire.types.state``) or a plain dict with the same keys. The
        store reads ``target_table``, ``target_id``, ``after`` and uses
        ``before`` (or auto-captures it from the current row) so reversal can
        restore the previous state.
        """
        async with self._txn() as conn:
            return await self._apply_delta_on_conn(
                conn,
                delta=delta,
                source=source,
                turn_id=turn_id,
                branch_id=branch_id,
                campaign_id=campaign_id,
                delta_set_id=delta_set_id,
            )

    async def _apply_delta_on_conn(
        self,
        conn: aiosqlite.Connection,
        *,
        delta: dict | Any,
        source: str | None = None,
        turn_id: str | None = None,
        branch_id: str | None = None,
        campaign_id: str | None = None,
        delta_set_id: str | None = None,
    ) -> str:
        """Apply one delta inside an already-open transaction. Returns delta id."""
        payload = _delta_to_dict(delta)
        target_scope = payload.get("target_scope")
        target_table = payload.get("target_table")
        kind = payload.get("kind", "other")
        after = payload.get("after") or {}
        provided_before = payload.get("before")

        captured_before: Any | None = None
        if target_scope == "campaign-sqlite":
            if not target_table:
                raise StateStoreError("campaign-sqlite delta missing target_table")
            captured_before = await _capture_current_row(conn, target_table, after)
            await upsert_row(conn, table=target_table, values=after)
        elif target_scope in ("library", "campaign-file", "campaign-local"):
            pass
        else:
            raise StateStoreError(f"unknown target_scope {target_scope!r}; use file APIs for files")

        before_for_log = provided_before if provided_before is not None else captured_before
        delta_id = await insert_delta(
            conn,
            campaign_id=campaign_id or payload.get("campaign_id"),
            branch_id=branch_id or payload.get("branch_id"),
            turn_id=turn_id or payload.get("turn_id"),
            source=source or payload.get("source") or "unknown",
            kind=kind,
            target_scope=target_scope,
            target_table=target_table,
            target_path=payload.get("target_path"),
            target_id=payload.get("target_id"),
            before=before_for_log,
            after=after,
            confidence=payload.get("confidence"),
            notes=payload.get("notes"),
            delta_set_id=delta_set_id or payload.get("delta_set_id"),
        )
        return delta_id

    async def reverse_delta(self, delta_id: str) -> None:
        async with self._txn() as conn:
            await self._reverse_delta_on_conn(conn, delta_id)

    async def _reverse_delta_on_conn(
        self,
        conn: aiosqlite.Connection,
        delta_id: str,
    ) -> None:
        delta = await get_delta(conn, delta_id)
        if delta.reversed_at is not None:
            raise StateStoreError(f"delta {delta_id} already reversed at {delta.reversed_at}")
        if delta.target_scope == "campaign-sqlite":
            await reverse_sqlite_delta(conn, delta)
        elif delta.target_scope in ("library", "campaign-file"):
            await self._reverse_file_delta(conn, delta)
        else:
            raise StateStoreError(f"cannot reverse delta with scope {delta.target_scope!r}")
        await mark_reversed(conn, delta_id)

    async def _reverse_file_delta(
        self,
        conn: aiosqlite.Connection,
        delta: DeltaRecord,
    ) -> None:
        if delta.target_path is None:
            raise StateStoreError(f"file delta {delta.id} has no target_path")
        target = Path(delta.target_path)
        if delta.before is None:
            # The file did not exist before — delete it now.
            if target.exists():
                target.unlink()
            if delta.target_scope == "library" and delta.target_id:
                await conn.execute("DELETE FROM library_index WHERE id = ?", (delta.target_id,))
            if delta.target_scope == "campaign-file" and delta.target_id:
                await conn.execute(
                    "DELETE FROM campaign_content_index WHERE id = ?",
                    (delta.target_id,),
                )
            return

        before = delta.before
        fm = before.get("frontmatter") if isinstance(before, dict) else None
        body = (before.get("body") if isinstance(before, dict) else None) or ""

        if target.suffix == ".yaml":
            write_yaml(target, fm if fm is not None else before)
        else:
            write_markdown(target, ParsedDocument(frontmatter=fm or {}, body=body))

        if delta.target_scope == "library" and delta.target_id:
            await upsert_library_index(
                conn,
                data_root=self.data_root,
                library_id=delta.target_id,
                path=target,
                frontmatter=fm or {},
                body=body,
            )
        elif delta.target_scope == "campaign-file" and delta.target_id:
            cid = campaign_id_for_path(self.data_root, target) or ""
            kind = _content_kind_from_id(delta.target_id)
            await upsert_campaign_content_index(
                conn,
                data_root=self.data_root,
                campaign_id=cid,
                composite_id=delta.target_id,
                kind=kind,
                entity_subkind=None,
                asset_id=None,
                path=target,
                frontmatter=fm,
                body=body,
            )

    # ------------------------------------------------------------------
    # Delta sets (swipes / alternates)
    # ------------------------------------------------------------------

    async def apply_delta_set(
        self,
        *,
        deltas: list[Any],
        delta_set_id: str,
        campaign_id: str | None,
        branch_id: str | None,
        turn_id: str | None,
        source: str,
    ) -> list[DeltaRecord]:
        """Apply every delta atomically, tagging each with ``delta_set_id``.

        On any failure, the entire batch rolls back. Returns the materialized
        :class:`DeltaRecord`s in insertion order.
        """
        records: list[DeltaRecord] = []
        async with self._txn() as conn:
            for d in deltas:
                delta_id = await self._apply_delta_on_conn(
                    conn,
                    delta=d,
                    source=source,
                    turn_id=turn_id,
                    branch_id=branch_id,
                    campaign_id=campaign_id,
                    delta_set_id=delta_set_id,
                )
                rec = await get_delta(conn, delta_id)
                records.append(rec)
        return records

    async def rewind_delta_set(
        self,
        delta_set_id: str,
        *,
        campaign_id: str,
        branch_id: str,
    ) -> list[DeltaRecord]:
        """LIFO-reverse every non-reversed delta tagged with ``delta_set_id``.

        Atomic — if any reversal fails, the whole batch rolls back.
        """
        reversed_records: list[DeltaRecord] = []
        async with self._txn() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM deltas
                WHERE campaign_id = ? AND branch_id = ?
                  AND delta_set_id = ? AND reversed_at IS NULL
                ORDER BY applied_at DESC, rowid DESC
                """,
                (campaign_id, branch_id, delta_set_id),
            )
            rows = await cur.fetchall()
            await cur.close()
            for row in rows:
                delta_id = row["id"]
                await self._reverse_delta_on_conn(conn, delta_id)
                reversed_records.append(await get_delta(conn, delta_id))
        return reversed_records

    async def re_activate_delta_set(
        self,
        *,
        delta_set_id: str,
        campaign_id: str,
        branch_id: str,
    ) -> int:
        """Re-apply every previously-reversed delta in a set (oldest first).

        Walks the rows where ``reversed_at IS NOT NULL`` for the set and
        re-upserts the ``after`` payload into the target table, then clears
        ``reversed_at``. Used by :meth:`swap_delta_set` when the apply side is
        a delta set already present in the DB (just rewound).

        Returns the number of rows reactivated.
        """
        count = 0
        async with self._txn() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM deltas
                WHERE campaign_id = ? AND branch_id = ?
                  AND delta_set_id = ? AND reversed_at IS NOT NULL
                ORDER BY applied_at ASC, rowid ASC
                """,
                (campaign_id, branch_id, delta_set_id),
            )
            rows = await cur.fetchall()
            await cur.close()
            for row in rows:
                rec = DeltaRecord.from_row(row)
                if rec.target_scope == "campaign-sqlite" and rec.target_table:
                    after = rec.after or {}
                    if after:
                        await upsert_row(conn, table=rec.target_table, values=after)
                # file deltas: their target file should already reflect the
                # after-state if it was never overwritten in the interim; we
                # only flip the reversed_at flag for now.
                await conn.execute(
                    "UPDATE deltas SET reversed_at = NULL WHERE id = ?",
                    (rec.id,),
                )
                count += 1
        return count

    async def swap_delta_set(
        self,
        *,
        rewind_set_id: str,
        apply_deltas: list[Any] | None,
        apply_set_id: str,
        campaign_id: str,
        branch_id: str,
        turn_id: str | None,
        source: str,
    ) -> SwapResult:
        """Atomic rewind of one set followed by application of another.

        Two modes:

        * **Fresh apply:** ``apply_deltas`` is a non-empty list — every delta
          is inserted with ``apply_set_id``. This is what ``regenerate_post``
          uses (the apply set is new).
        * **Re-activate:** ``apply_deltas`` is ``None`` or empty — the apply
          set is assumed to already exist in the ``deltas`` table in a
          reversed state, and reversal flags are cleared. This is what
          ``switch_primary_alternate`` uses for swap-between-existing.

        Both phases happen in a single transaction. On apply failure the
        rewind is rolled back too, so the prior primary remains intact.
        """
        rewound: list[DeltaRecord] = []
        applied: list[DeltaRecord] = []
        async with self._txn() as conn:
            # Phase 1: rewind the current set (LIFO).
            cur = await conn.execute(
                """
                SELECT id FROM deltas
                WHERE campaign_id = ? AND branch_id = ?
                  AND delta_set_id = ? AND reversed_at IS NULL
                ORDER BY applied_at DESC, rowid DESC
                """,
                (campaign_id, branch_id, rewind_set_id),
            )
            rewind_rows = await cur.fetchall()
            await cur.close()
            for row in rewind_rows:
                await self._reverse_delta_on_conn(conn, row["id"])
                rewound.append(await get_delta(conn, row["id"]))

            # Phase 2: apply.
            if apply_deltas:
                for d in apply_deltas:
                    delta_id = await self._apply_delta_on_conn(
                        conn,
                        delta=d,
                        source=source,
                        turn_id=turn_id,
                        branch_id=branch_id,
                        campaign_id=campaign_id,
                        delta_set_id=apply_set_id,
                    )
                    applied.append(await get_delta(conn, delta_id))
            else:
                # Re-activate an existing set: re-upsert after-payloads and
                # clear reversed_at.
                cur = await conn.execute(
                    """
                    SELECT * FROM deltas
                    WHERE campaign_id = ? AND branch_id = ?
                      AND delta_set_id = ? AND reversed_at IS NOT NULL
                    ORDER BY applied_at ASC, rowid ASC
                    """,
                    (campaign_id, branch_id, apply_set_id),
                )
                rows = await cur.fetchall()
                await cur.close()
                for row in rows:
                    rec = DeltaRecord.from_row(row)
                    if rec.target_scope == "campaign-sqlite" and rec.target_table:
                        after = rec.after or {}
                        if after:
                            await upsert_row(conn, table=rec.target_table, values=after)
                    await conn.execute(
                        "UPDATE deltas SET reversed_at = NULL WHERE id = ?",
                        (rec.id,),
                    )
                    applied.append(await get_delta(conn, rec.id))
        return SwapResult(rewound=rewound, applied=applied)

    async def set_current_alternate_delta_set(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        post_id: str,
        delta_set_id: str,
    ) -> None:
        """Record which delta set is the current primary for ``post_id``."""
        async with self._txn() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO current_alternate_delta_sets
                  (campaign_id, branch_id, post_id, delta_set_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (campaign_id, branch_id, post_id, delta_set_id, now_iso()),
            )

    async def clear_current_alternate_delta_set(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        post_id: str,
    ) -> None:
        async with self._txn() as conn:
            await conn.execute(
                """
                DELETE FROM current_alternate_delta_sets
                WHERE campaign_id = ? AND branch_id = ? AND post_id = ?
                """,
                (campaign_id, branch_id, post_id),
            )

    async def current_delta_set_for(
        self,
        *,
        post_id: str | None,
        campaign_id: str,
        branch_id: str,
        set_id: str | None = None,
    ) -> str | None:
        """Look up the primary delta set for ``post_id`` (or echo back
        ``set_id`` if it's the recorded primary for any post on the branch).
        """
        async with self.db.acquire() as conn:
            if post_id is not None:
                cur = await conn.execute(
                    """
                    SELECT delta_set_id FROM current_alternate_delta_sets
                    WHERE campaign_id = ? AND branch_id = ? AND post_id = ?
                    """,
                    (campaign_id, branch_id, post_id),
                )
                row = await cur.fetchone()
                await cur.close()
                return row["delta_set_id"] if row else None
            if set_id is None:
                return None
            cur = await conn.execute(
                """
                SELECT delta_set_id FROM current_alternate_delta_sets
                WHERE campaign_id = ? AND branch_id = ? AND delta_set_id = ?
                LIMIT 1
                """,
                (campaign_id, branch_id, set_id),
            )
            row = await cur.fetchone()
            await cur.close()
            return row["delta_set_id"] if row else None

    async def queue_for_review(
        self,
        *,
        delta: dict | Any,
        source: str | None = None,
        campaign_id: str | None = None,
    ) -> str:
        """Persist a low-confidence delta for human review without applying it."""
        payload = _delta_to_dict(delta)
        async with self._txn() as conn:
            delta_id = await insert_delta(
                conn,
                campaign_id=campaign_id or payload.get("campaign_id"),
                branch_id=payload.get("branch_id"),
                turn_id=payload.get("turn_id"),
                source=source or payload.get("source") or "unknown",
                kind=payload.get("kind") or "other",
                target_scope=payload.get("target_scope") or "campaign-sqlite",
                target_table=payload.get("target_table"),
                target_path=payload.get("target_path"),
                target_id=payload.get("target_id"),
                before=payload.get("before"),
                after=payload.get("after"),
                confidence=payload.get("confidence"),
                notes="queued for review",
            )
            # The delta is logged but not applied — caller approves later by
            # calling apply via approve_review_item().
            review_id = await _queue_for_review(conn, delta_id=delta_id, campaign_id=campaign_id)
        return review_id

    async def approve_review_item(self, review_id: str) -> str:
        """Apply a previously-queued delta. Returns the delta id."""
        async with self._txn() as conn:
            cur = await conn.execute(
                "SELECT delta_id, status FROM review_queue WHERE id = ?",
                (review_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                raise NotFoundError(f"review item {review_id} not found")
            if row["status"] != "pending":
                raise StateStoreError(f"review item {review_id} already {row['status']}")
            delta = await get_delta(conn, row["delta_id"])
            if delta.target_scope == "campaign-sqlite" and delta.target_table:
                await upsert_row(conn, table=delta.target_table, values=delta.after or {})
            await conn.execute(
                """
                UPDATE review_queue
                SET status = 'approved', reviewed_at = ?
                WHERE id = ?
                """,
                (now_iso(), review_id),
            )
        return delta.id

    async def reject_review_item(self, review_id: str, *, notes: str = "") -> None:
        async with self._txn() as conn:
            cur = await conn.execute(
                "SELECT delta_id FROM review_queue WHERE id = ?",
                (review_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                raise NotFoundError(f"review item {review_id} not found")
            await conn.execute(
                """
                UPDATE review_queue
                SET status = 'rejected', reviewed_at = ?, reviewer_notes = ?
                WHERE id = ?
                """,
                (now_iso(), notes, review_id),
            )
            # Mark the unapplied delta as reversed so it doesn't appear in
            # active-deltas queries.
            await mark_reversed(conn, row["delta_id"])

    async def get_delta_log(
        self,
        *,
        campaign_id: str | None = None,
        since: datetime | str | None = None,
        turn_id: str | None = None,
        include_reversed: bool = True,
        limit: int | None = None,
    ) -> list[DeltaRecord]:
        since_str = since.isoformat() if isinstance(since, datetime) else since
        async with self.db.acquire() as conn:
            return await list_deltas(
                conn,
                campaign_id=campaign_id,
                since=since_str,
                turn_id=turn_id,
                include_reversed=include_reversed,
                limit=limit,
            )

    # ------------------------------------------------------------------
    # Embeddings + search
    # ------------------------------------------------------------------

    async def add_embedding(
        self,
        *,
        ref: str,
        scope: str,
        source_kind: str,
        text: str,
        vector: list[float],
        model: str,
        campaign_id: str | None = None,
    ) -> str:
        embedding_id = new_id("emb", length=16)
        async with self.db.acquire() as conn:
            await insert_embedding(
                conn,
                embedding_id=embedding_id,
                scope=scope,
                ref=ref,
                source_kind=source_kind,
                text=text,
                vector=vector,
                model=model,
                embedded_at=now_iso(),
                campaign_id=campaign_id,
            )
        return embedding_id

    async def delete_embeddings(self, ref: str) -> int:
        async with self.db.acquire() as conn:
            return await delete_embeddings_for_ref(conn, ref)

    async def vector_search(
        self,
        *,
        query_vector: list[float],
        campaign_id: str,
        source_kinds: list[str] | None = None,
        include_library: bool = True,
        top_k: int = 8,
    ) -> list[SearchHit]:
        async with self.db.acquire() as conn:
            return await _vector_search(
                conn,
                query_vector=query_vector,
                campaign_id=campaign_id,
                source_kinds=source_kinds,
                include_library=include_library,
                top_k=top_k,
            )

    async def keyword_search(
        self,
        *,
        query: str,
        campaign_id: str | None = None,
        branch_id: str | None = None,
        kinds: Iterable[str] = ("fact",),
        top_k: int = 5,
        include_retired: bool = False,
    ) -> list[SearchHit]:
        kinds_set = set(kinds)
        hits: list[SearchHit] = []
        async with self.db.acquire() as conn:
            if "fact" in kinds_set:
                hits.extend(
                    await keyword_search_facts(
                        conn,
                        query=query,
                        campaign_id=campaign_id,
                        branch_id=branch_id,
                        include_retired=include_retired,
                        top_k=top_k,
                    )
                )
            if kinds_set & {"character", "item", "location", "lore", "faction"}:
                hits.extend(
                    await keyword_search_library(
                        conn,
                        query=query,
                        kinds=list(
                            kinds_set
                            & {
                                "character",
                                "item",
                                "location",
                                "lore",
                                "faction",
                            }
                        ),
                        top_k=top_k,
                    )
                )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    # ------------------------------------------------------------------
    # Context inspector pins / excludes
    # ------------------------------------------------------------------

    async def write_context_pin(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        kind: str,
        target_source_id: str | None = None,
        target_entity_kind: str | None = None,
        target_entity_id: str | None = None,
        created_at_turn_id: str | None = None,
        ttl_turns: int | None = None,
        created_by: str = "user",
        pin_id: str | None = None,
    ) -> str:
        """Insert a context pin/exclude row.

        TTL is stored as an integer turn count alongside
        ``created_at_turn_id``. Expiry is resolved at read time by counting
        turns elapsed between ``created_at_turn_id`` and the current turn id
        using ``turn_audits.created_at`` ordering — turn ids themselves are
        random hex and not lexicographically comparable.
        """
        if kind not in ("pin", "exclude"):
            raise ValueError(f"context pin kind must be 'pin' or 'exclude', got {kind!r}")
        target_kind = "source" if target_source_id else "entity"
        if target_kind == "entity" and not (target_entity_kind and target_entity_id):
            raise ValueError("entity-targeted pin requires both entity_kind and entity_id")
        if ttl_turns is not None and ttl_turns <= 0:
            raise ValueError(f"ttl_turns must be positive when set, got {ttl_turns}")
        pid = pin_id or new_id("ctx_pin", length=16)
        await self.db.execute(
            """
            INSERT INTO context_pins (
                id, campaign_id, branch_id, kind, target_kind,
                target_source_id, target_entity_kind, target_entity_id,
                created_at, created_by, created_at_turn_id, ttl_turns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                campaign_id,
                branch_id,
                kind,
                target_kind,
                target_source_id,
                target_entity_kind,
                target_entity_id,
                now_iso(),
                created_by,
                created_at_turn_id,
                ttl_turns,
            ),
        )
        return pid

    async def list_active_context_pins(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        current_turn_id: str | None = None,
    ) -> list[dict]:
        """Return every active pin/exclude for the (campaign, branch) tuple.

        Active = ``cleared_at IS NULL`` AND TTL not exhausted. TTL is
        exhausted when the number of canonical turns recorded in
        ``turn_audits`` between ``created_at_turn_id`` (exclusive) and
        ``current_turn_id`` (inclusive) is >= ``ttl_turns``. Rows with
        ``ttl_turns IS NULL`` never expire.
        """
        rows = await self.db.fetchall(
            """
            SELECT * FROM context_pins
            WHERE campaign_id = ? AND branch_id = ? AND cleared_at IS NULL
            ORDER BY created_at
            """,
            (campaign_id, branch_id),
        )
        out: list[dict] = []
        for row in rows:
            ttl = row["ttl_turns"]
            if ttl is None or current_turn_id is None or row["created_at_turn_id"] is None:
                out.append(dict(row))
                continue
            elapsed = await self._turns_elapsed(
                campaign_id=campaign_id,
                branch_id=branch_id,
                from_turn_id=row["created_at_turn_id"],
                to_turn_id=current_turn_id,
            )
            if elapsed is None or elapsed < int(ttl):
                out.append(dict(row))
        return out

    async def _turns_elapsed(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        from_turn_id: str,
        to_turn_id: str,
    ) -> int | None:
        """Count canonical turns between two turn ids on the same branch.

        Uses ``turn_audits.created_at`` ordering. Returns ``None`` when
        either turn id is unknown to the audit table (caller treats this
        as "TTL not yet exhausted" so a missing audit can't silently
        evict pins).
        """
        rows = await self.db.fetchall(
            """
            SELECT turn_id, created_at FROM turn_audits
            WHERE campaign_id = ? AND branch_id = ?
              AND turn_id IN (?, ?)
            """,
            (campaign_id, branch_id, from_turn_id, to_turn_id),
        )
        by_id = {row["turn_id"]: row["created_at"] for row in rows}
        if from_turn_id not in by_id or to_turn_id not in by_id:
            return None
        start = by_id[from_turn_id]
        end = by_id[to_turn_id]
        if end < start:
            return 0
        count_row = await self.db.fetchone(
            """
            SELECT COUNT(*) AS n FROM turn_audits
            WHERE campaign_id = ? AND branch_id = ?
              AND created_at > ? AND created_at <= ?
            """,
            (campaign_id, branch_id, start, end),
        )
        return int(count_row["n"]) if count_row else 0

    async def mark_context_pin_cleared(
        self,
        *,
        pin_id: str,
        cleared_by: str = "user",
    ) -> None:
        await self.db.execute(
            "UPDATE context_pins SET cleared_at = ?, cleared_by = ? WHERE id = ?",
            (now_iso(), cleared_by, pin_id),
        )


# ---------------------------------------------------------------------------
# Row → dict helpers
# ---------------------------------------------------------------------------


def _library_row_to_dict(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "world_id": row["world_id"],
        "kind": row["kind"],
        "asset_id": row["asset_id"],
        "name": row["name"],
        "path": row["path"],
        "frontmatter": _json_loads(row["frontmatter"]) or {},
        "body": row["body"] or "",
        "body_compressed": row["body_compressed"],
        "tags": _json_loads(row["tags"]) or [],
        "keywords": _json_loads(row["keywords"]) or [],
        "file_mtime": row["file_mtime"],
        "content_hash": row["content_hash"],
        "indexed_at": row["indexed_at"],
        "version": int(row["version"]),
    }


def _content_row_to_dict(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "campaign_id": row["campaign_id"],
        "kind": row["kind"],
        "entity_subkind": row["entity_subkind"],
        "asset_id": row["asset_id"],
        "path": row["path"],
        "frontmatter": _json_loads(row["frontmatter"]) or {},
        "body": row["body"] or "",
        "content_hash": row["content_hash"],
        "indexed_at": row["indexed_at"],
    }


def _scene_row_to_dict(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "campaign_id": row["campaign_id"],
        "branch_id": row["branch_id"],
        "ordinal": int(row["ordinal"]),
        "slug": row["slug"],
        "file_path": row["file_path"],
        "title": row["title"],
        "location_ref": row["location_ref"],
        "in_game_start": row["in_game_start"],
        "in_game_end": row["in_game_end"],
        "pov_character_ref": row["pov_character_ref"],
        "present_character_refs": _json_loads(row["present_character_refs"]) or [],
        "present_pc_refs": _json_loads(row["present_pc_refs"]) or [],
        "post_count": int(row["post_count"]),
        "closed": bool(int(row["closed"])),
        "running_summary": row["running_summary"],
        "summary": row["summary"],
        "key_beats": _json_loads(row["key_beats"]) or [],
        "tags": _json_loads(row["tags"]) or [],
    }


def _character_state_row_to_dict(row: aiosqlite.Row) -> dict:
    return {
        "character_ref": row["character_ref"],
        "campaign_id": row["campaign_id"],
        "branch_id": row["branch_id"],
        "location_ref": row["location_ref"],
        "emotional_state": row["emotional_state"],
        "physical_state": row["physical_state"],
        "immediate_intent": row["immediate_intent"],
        "knowledge_state": _json_loads(row["knowledge_state"]),
        "last_action": row["last_action"],
        "last_screen_time_turn": row["last_screen_time_turn"],
        "visible_to_pc": bool(int(row["visible_to_pc"])),
        "drift_score": float(row["drift_score"]),
        "tier_pin": row["tier_pin"],
        "current_scene_id": row["current_scene_id"],
        "updated_at_turn": row["updated_at_turn"],
        "appearances_since_last_drift_check": int(row["appearances_since_last_drift_check"] or 0),
    }


def _delta_to_dict(value: Any) -> dict:
    if value is None:
        raise StateStoreError("delta is None")
    if isinstance(value, dict):
        return value
    # Try to interoperate with pydantic models (StateDelta).
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    raise StateStoreError(f"cannot convert delta of type {type(value).__name__}")


async def _capture_current_row(
    conn: aiosqlite.Connection,
    table: str,
    after: dict,
) -> dict | None:
    """Look up the current row for the PK in ``after``; ``None`` if absent.

    ``table`` is interpolated into the SELECT — safe today because
    ``primary_key_columns`` returns ``None`` for any table that isn't in
    the hard-coded ``_PRIMARY_KEYS`` allowlist, so the gate below already
    rejects untrusted names. The explicit membership check makes that
    invariant local to this function rather than relying on a transitive
    property of a helper in another module.
    """
    pk = primary_key_columns(table)
    if pk is None:
        return None
    if table not in _CAPTURE_SAFE_TABLES:
        # Belt-and-braces: should be unreachable because primary_key_columns
        # returns None for anything outside this set. If a future edit adds
        # a table to _PRIMARY_KEYS but forgets to extend _CAPTURE_SAFE_TABLES,
        # we'd rather fail closed here than silently interpolate the new
        # name. Mismatch is a programming error, not a runtime contingency.
        raise StateStoreError(f"refusing to capture from non-allowlisted table {table!r}")
    if not all(col in after for col in pk):
        return None
    where = " AND ".join(f"{c} = ?" for c in pk)
    params = tuple(after[c] for c in pk)
    async with conn.execute(
        f"SELECT * FROM {table} WHERE {where}",
        params,
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


# Tables _capture_current_row is allowed to SELECT from. Must be a
# superset of the keys in delta_log._PRIMARY_KEYS for the check above
# to pass; kept as a separate constant so a future edit to either has
# to consciously mirror it.
_CAPTURE_SAFE_TABLES: frozenset[str] = frozenset(
    {
        "character_state",
        "location_state",
        "faction_state",
        "facts",
        "commitments",
        "relationships",
        "knowledge_state",
        "calendar",
        "images",
        "scenes",
        "posts",
    }
)


def _content_kind_from_id(composite_id: str) -> str:
    """Infer ``kind`` for ``campaign_content_index`` from a composite id."""
    parts = composite_id.split("/")
    # campaigns/<id>/<kind>/...
    if len(parts) >= 3 and parts[0] == "campaigns":
        return parts[2].rstrip("s") if parts[2].endswith("s") else parts[2]
    return "unknown"


def _seed_for(label: str) -> int:
    """Stable deterministic seed from a label; sqlite stores INTEGER.

    Python's builtin ``hash()`` is salted per process (PYTHONHASHSEED), so the
    same label produces a different value on each restart — breaking the
    "deterministic replay across restarts" guarantee for branch RNG seeding.
    Use a fixed-output hash (SHA-256, truncated to 31 bits) instead.
    """
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
