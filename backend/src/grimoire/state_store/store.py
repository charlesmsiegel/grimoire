"""The :class:`StateStore` — write-side coordinator.

Every domain write goes through this class. The constructor wires a
:class:`grimoire.storage.Database` connection pool together with a data
root path; the methods below mediate file writes and SQLite writes so the
two halves stay coherent.
"""

from __future__ import annotations

import contextlib
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
from grimoire.state_store.context_pins import ContextPinStore
from grimoire.state_store.delta_log import (
    DeltaRecord,
    insert_delta,
    validate_table_columns,
)
from grimoire.state_store.delta_ops import DeltaOps, SwapResult
from grimoire.state_store.errors import (
    InvalidRefError,
    NotFoundError,
    StateStoreError,
)
from grimoire.state_store.file_snapshots import snapshot_file_before
from grimoire.state_store.indexers import (
    delete_library_index_row,
    make_library_id,
    upsert_campaign_content_index,
    upsert_library_index,
)
from grimoire.state_store.inventory_store import InventoryStore
from grimoire.state_store.paths import (
    KIND_TO_DIR,
    campaigns_root,
    character_variant_path,
    character_variants_dir,
    content_path,
    emergent_path,
    image_metadata_path,
    library_path,
    override_path,
    parse_library_id,
    sheet_path,
    validate_path_component,
)
from grimoire.state_store.search import SearchHit
from grimoire.state_store.search_store import SearchStore
from grimoire.state_store.snapshots import (
    remove_snapshots_for_world,
    upgrade_snapshots,
    write_snapshots_for_world,
)
from grimoire.storage import Database
from grimoire.util import now_iso

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


# Variant-file frontmatter keys that describe the variant itself and must not
# leak into the resolved character: ``label`` is the variant's display name,
# ``id`` would clobber the character's identity.
_VARIANT_RESERVED_KEYS: frozenset[str] = frozenset({"id", "label"})


def _merge_overlay(base_frontmatter: dict, patch: dict) -> dict:
    """Shallow-merge ``patch`` onto entity frontmatter with extras tombstones.

    Top-level keys replace wholesale, except ``extras`` which merges
    key-by-key; a ``None`` value in ``patch['extras']`` deletes the cascaded
    key. Shared by campaign overrides and character variant overlays so both
    layers behave identically.
    """
    merged = dict(base_frontmatter)
    base_extras = dict(merged.get("extras") or {})
    patch_extras = patch.get("extras")
    merged.update(patch)
    if base_extras or isinstance(patch_extras, dict):
        merged_extras = dict(base_extras)
        for key, value in (patch_extras or {}).items():
            if value is None:
                merged_extras.pop(key, None)
            else:
                merged_extras[key] = value
        if merged_extras:
            merged["extras"] = merged_extras
        else:
            merged.pop("extras", None)
    return merged


@dataclass(frozen=True)
class UpgradeReport:
    world_id: str
    diff: dict[str, dict]  # library_id -> {before: int, after: int}

    @property
    def changed_count(self) -> int:
        return len(self.diff)


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
        # Coordinators (#521): cohesive clusters extracted out of this class.
        # Each is handed the store's transaction factory explicitly so its
        # multi-step writes share BEGIN IMMEDIATE + metrics behaviour.
        self._delta_ops = DeltaOps(db=db, data_root=self.data_root, txn=self._txn)
        self._inventory = InventoryStore(
            db=db,
            data_root=self.data_root,
            txn=self._txn,
            write_emergent=self.write_emergent,
        )
        self._search = SearchStore(db=db)
        self._context_pins = ContextPinStore(db=db)

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
        if target.exists():
            if ref.kind in {"world", "image_preset"}:
                before_data = load_yaml(target) or {}
                before_payload = {"frontmatter": before_data, "body": ""}
            else:
                doc = read_markdown(target)
                before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}
        else:
            before_payload = None

        with snapshot_file_before(target):
            # Write the file.
            if ref.kind in {"world", "image_preset"}:
                write_yaml(target, frontmatter)
                body = ""
            else:
                write_markdown(target, ParsedDocument(frontmatter=frontmatter, body=body))

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
        the deletion is reversible. Deleting a character cascades to its
        variant overlay files (each captured in its own reversible delta) so a
        later character with the same id doesn't resurrect stale variants.
        """
        ref = parse_library_id(library_id)
        target = library_path(self.data_root, library_id)
        if not target.exists():
            raise NotFoundError(f"library file does not exist: {library_id}")

        if ref.kind in {"world", "image_preset"}:
            before_data = load_yaml(target) or {}
            before_payload = {"frontmatter": before_data, "body": ""}
        else:
            doc = read_markdown(target)
            before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}

        # Cascade: a character's variant overlays die with the base. Each
        # file gets its own snapshot context so a failed transaction restores
        # all of them, base included.
        variant_snapshots: list[tuple[Path, dict, str]] = []
        if ref.kind == "character" and ref.world_id is not None:
            variants_dir = character_variants_dir(self.data_root, ref.world_id, ref.asset_id)
            if variants_dir.is_dir():
                for vpath in sorted(variants_dir.glob("*.md")):
                    vdoc = read_markdown(vpath)
                    variant_snapshots.append(
                        (
                            vpath,
                            {"frontmatter": vdoc.frontmatter, "body": vdoc.body},
                            f"{library_id}/variants/{vpath.stem}",
                        )
                    )

        with contextlib.ExitStack() as snapshots:
            snapshots.enter_context(snapshot_file_before(target))
            for vpath, _vpayload, _vid in variant_snapshots:
                snapshots.enter_context(snapshot_file_before(vpath))
            target.unlink()
            for vpath, _vpayload, _vid in variant_snapshots:
                vpath.unlink()

            async with self._txn() as conn:
                await delete_library_index_row(conn, library_id)
                delta_id = await insert_delta(
                    conn,
                    campaign_id=campaign_id,
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
                for vpath, vpayload, vid in variant_snapshots:
                    await insert_delta(
                        conn,
                        campaign_id=campaign_id,
                        turn_id=None,
                        source=source,
                        kind="library_file_delete",
                        target_scope="library",
                        target_table=None,
                        target_path=str(vpath),
                        target_id=vid,
                        before=vpayload,
                        after=None,
                    )

        if variant_snapshots:
            # Tidy the now-empty variants/ dir (and the character dir when the
            # overlay dir was its only content, i.e. flat-form characters).
            variants_dir = variant_snapshots[0][0].parent
            with contextlib.suppress(OSError):
                variants_dir.rmdir()
            with contextlib.suppress(OSError):
                variants_dir.parent.rmdir()

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

        composite_id = f"campaigns/{campaign_id}/overrides/{library_id}"
        with snapshot_file_before(target):
            write_yaml(target, patch)
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

    async def merge_override(
        self,
        *,
        campaign_id: str,
        world_id: str,
        kind: str,
        asset_id: str,
        patch: dict,
        source: str,
        turn_id: str | None = None,
    ) -> Path:
        """Shallow-merge ``patch`` into a library entity's campaign override.

        ``write_override`` is a full-file overwrite; this reads the existing
        override first and merges top-level keys so unrelated overrides (name,
        role, extras, …) are preserved. ``make_library_id`` stays inside the
        storage package.
        """
        library_id = make_library_id(world_id, kind, asset_id)
        existing = await self.get_override(campaign_id, library_id) or {}
        merged = {**existing, **patch}
        return await self.write_override(
            campaign_id=campaign_id,
            library_id=library_id,
            patch=merged,
            source=source,
            turn_id=turn_id,
        )

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

        composite_id = f"campaigns/{campaign_id}/overrides/{library_id}"
        with snapshot_file_before(target):
            target.unlink()
            async with self._txn() as conn:
                from grimoire.state_store.indexers import delete_campaign_content_row

                await delete_campaign_content_row(conn, composite_id)
                await insert_delta(
                    conn,
                    campaign_id=campaign_id,
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

        composite_id = f"campaigns/{campaign_id}/emergent/{kind}/{entity_id}"
        with snapshot_file_before(target):
            target.unlink()
            async with self._txn() as conn:
                from grimoire.state_store.indexers import delete_campaign_content_row

                await delete_campaign_content_row(conn, composite_id)
                await insert_delta(
                    conn,
                    campaign_id=campaign_id,
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

        composite_id = f"campaigns/{campaign_id}/emergent/{kind}/{entity_id}"
        with snapshot_file_before(target):
            write_markdown(target, ParsedDocument(frontmatter=frontmatter, body=body))
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

        composite_id = f"campaigns/{campaign_id}/sheets/{kind}/{entity_id}.{mechanics_id}"
        with snapshot_file_before(target):
            write_yaml(target, sheet)
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

        composite_id = f"campaigns/{campaign_id}/content/{kind}/{content_id}.{mechanics_id}"
        with snapshot_file_before(target):
            write_yaml(target, payload)
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

        composite_id = f"campaigns/{campaign_id}/images/{image_id}"
        with snapshot_file_before(target):
            write_yaml(target, metadata)
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

    async def list_snapshot_rows(
        self, campaign_id: str, world_id: str, kind: str | None = None
    ) -> list[dict]:
        """Pinned-bind snapshot rows for one campaign + world, index-row shaped.

        Snapshot membership is what a pinned (``track_latest=false``) world ref
        looked like at bind time; rows are shaped like ``library_index`` dicts
        so listing code can treat both sources uniformly. Tags/keywords derive
        from the snapshotted frontmatter; file metadata columns are absent.
        """
        rows = await self.db.fetchall(
            """
            SELECT library_id, version, frontmatter, body FROM library_snapshots
            WHERE campaign_id = ? AND library_id LIKE ?
            ORDER BY library_id
            """,
            (campaign_id, f"worlds/{world_id}/%"),
        )
        out: list[dict] = []
        for row in rows:
            try:
                ref = parse_library_id(row["library_id"])
            except InvalidRefError:
                continue
            if kind is not None and ref.kind != kind:
                continue
            fm = _json_loads(row["frontmatter"]) or {}
            out.append(
                {
                    "id": row["library_id"],
                    "world_id": ref.world_id,
                    "kind": ref.kind,
                    "asset_id": ref.asset_id,
                    "name": fm.get("name") or fm.get("title") or ref.asset_id,
                    "path": "",
                    "frontmatter": fm,
                    "body": row["body"] or "",
                    "tags": list(fm.get("tags") or []),
                    "keywords": list(fm.get("keywords") or []),
                    "version": int(row["version"] or 0),
                }
            )
        return out

    # ------------------------------------------------------------------
    # Character variants (in-world diff overlays; files only, not indexed)
    # ------------------------------------------------------------------

    def _variant_dict(self, world_id: str, base_id: str, path: Path) -> dict:
        doc = read_markdown(path)
        variant_id = path.stem
        frontmatter = dict(doc.frontmatter or {})
        return {
            "id": variant_id,
            "world_id": world_id,
            "character_id": base_id,
            "label": str(frontmatter.get("label") or variant_id),
            "frontmatter": frontmatter,
            "body": doc.body or "",
            "path": str(path),
        }

    async def list_character_variants(self, world_id: str, base_id: str) -> list[dict]:
        """All variant overlays of a character, sorted by variant id.

        Variants are leaf files of the base character — they are read straight
        from disk and never enter ``library_index``. An unparseable file is
        returned as an error-marker entry (empty diff + ``error`` message)
        rather than dropped, so callers can tell "no variants" from "broken
        variant file" and the UI can surface the failure.
        """
        directory = character_variants_dir(self.data_root, world_id, base_id)
        if not directory.is_dir():
            return []
        out: list[dict] = []
        for path in sorted(directory.glob("*.md")):
            try:
                out.append(self._variant_dict(world_id, base_id, path))
            except Exception as exc:
                logger.warning("unparseable character variant %s: %s", path, exc)
                out.append(
                    {
                        "id": path.stem,
                        "world_id": world_id,
                        "character_id": base_id,
                        "label": path.stem,
                        "frontmatter": {},
                        "body": "",
                        "path": str(path),
                        "error": str(exc),
                    }
                )
        return out

    async def get_character_variant(
        self, world_id: str, base_id: str, variant_id: str
    ) -> dict | None:
        target = character_variant_path(self.data_root, world_id, base_id, variant_id)
        if not target.exists():
            return None
        return self._variant_dict(world_id, base_id, target)

    async def write_character_variant(
        self,
        *,
        world_id: str,
        base_id: str,
        variant_id: str,
        frontmatter: dict,
        body: str,
        source: str,
    ) -> dict:
        """Write a variant overlay file and record a reversible delta.

        Mirrors :meth:`write_library_file`'s compensation: the prior bytes are
        restored if the delta insert fails so disk and delta log stay coherent.
        """
        target = character_variant_path(self.data_root, world_id, base_id, variant_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        composite_id = f"worlds/{world_id}/characters/{base_id}/variants/{variant_id}"

        if target.exists():
            doc = read_markdown(target)
            before_payload: dict | None = {"frontmatter": doc.frontmatter, "body": doc.body}
        else:
            before_payload = None

        with snapshot_file_before(target):
            write_markdown(target, ParsedDocument(frontmatter=frontmatter, body=body))

            async with self._txn() as conn:
                await insert_delta(
                    conn,
                    campaign_id=None,
                    turn_id=None,
                    source=source,
                    kind="library_file_write",
                    target_scope="library",
                    target_table=None,
                    target_path=str(target),
                    target_id=composite_id,
                    before=before_payload,
                    after={"frontmatter": frontmatter, "body": body},
                )

        if self._bus is not None:
            await self._bus.emit(
                Event(
                    type="library_entity_changed",
                    payload={
                        "library_id": composite_id,
                        "kind": "character_variant",
                        "variant_of": make_library_id(world_id, "character", base_id),
                    },
                )
            )
        return self._variant_dict(world_id, base_id, target)

    async def delete_character_variant(
        self,
        *,
        world_id: str,
        base_id: str,
        variant_id: str,
        source: str,
    ) -> str:
        target = character_variant_path(self.data_root, world_id, base_id, variant_id)
        if not target.exists():
            raise NotFoundError(
                f"character variant does not exist: {base_id}/{variant_id} in {world_id}"
            )
        composite_id = f"worlds/{world_id}/characters/{base_id}/variants/{variant_id}"
        doc = read_markdown(target)
        before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}

        with snapshot_file_before(target):
            target.unlink()
            async with self._txn() as conn:
                delta_id = await insert_delta(
                    conn,
                    campaign_id=None,
                    turn_id=None,
                    source=source,
                    kind="library_file_delete",
                    target_scope="library",
                    target_table=None,
                    target_path=str(target),
                    target_id=composite_id,
                    before=before_payload,
                    after=None,
                )

        if self._bus is not None:
            await self._bus.emit(
                Event(
                    type="library_entity_changed",
                    payload={
                        "library_id": composite_id,
                        "kind": "character_variant",
                        "variant_of": make_library_id(world_id, "character", base_id),
                    },
                )
            )
        return delta_id

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

    async def list_scenes(self, campaign_id: str) -> list[dict]:
        async with self._metrics.measure("state_store", "query"):
            return await self._list_scenes_inner(campaign_id)

    async def _list_scenes_inner(self, campaign_id: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM scenes WHERE campaign_id = ? ORDER BY ordinal",
            (campaign_id,),
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

    async def set_campaign_config(self, campaign_id: str, config: dict | None) -> None:
        await self.db.execute(
            "UPDATE campaigns SET config = ? WHERE id = ?",
            (_json_dumps(config) if config is not None else None, campaign_id),
        )

    async def delete_campaign(self, campaign_id: str) -> None:
        """Delete a campaign and every derived row it owns, in one transaction.

        Campaign-scoped tables are discovered dynamically (any table with a
        ``campaign_id`` column) so the cascade covers new tables automatically
        and can't silently drift — which is how deleted campaigns previously
        left orphaned scenes/posts/deltas/etc. behind.
        """
        rows = await self.db.fetchall(
            "SELECT m.name AS name FROM sqlite_master m "
            "WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite_%' "
            "AND EXISTS (SELECT 1 FROM pragma_table_info(m.name) p WHERE p.name = 'campaign_id')"
        )
        # Table names come from sqlite_master (trusted schema), not user input.
        tables = [r["name"] for r in rows]
        async with self._txn() as conn:
            for table in tables:
                await conn.execute(f'DELETE FROM "{table}" WHERE campaign_id = ?', (campaign_id,))
            await conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))

    # ── Inventory derived state (#444) ──────────────────────────────

    async def upsert_inventory_holding(
        self,
        *,
        campaign_id: str,
        holder_kind: str,
        holder_id: str,
        item_ref: str,
        item_name: str,
        quantity: int,
        fungible: bool,
        equipped: bool,
        provenance: str | None,
        notes: str | None,
    ) -> None:
        await self._inventory.upsert_holding(
            campaign_id=campaign_id,
            holder_kind=holder_kind,
            holder_id=holder_id,
            item_ref=item_ref,
            item_name=item_name,
            quantity=quantity,
            fungible=fungible,
            equipped=equipped,
            provenance=provenance,
            notes=notes,
        )

    async def delete_inventory_holding(
        self, campaign_id: str, holder_kind: str, holder_id: str, item_ref: str
    ) -> None:
        await self._inventory.delete_holding(campaign_id, holder_kind, holder_id, item_ref)

    async def clear_holder_inventory(
        self, campaign_id: str, holder_kind: str, holder_id: str
    ) -> None:
        await self._inventory.clear_holder(campaign_id, holder_kind, holder_id)

    async def rebuild_inventory_holdings_from_files(self) -> int:
        """Rebuild the derived ``inventory_holdings`` table from overlay files.

        Returns the number of holder files skipped because they failed to
        parse (see :meth:`InventoryStore.rebuild_holdings_from_files`).
        """
        return await self._inventory.rebuild_holdings_from_files()

    async def list_inventory_holdings(
        self,
        campaign_id: str,
        *,
        holder_kind: str | None = None,
        holder_id: str | None = None,
        item_ref: str | None = None,
    ) -> list[dict]:
        return await self._inventory.list_holdings(
            campaign_id, holder_kind=holder_kind, holder_id=holder_id, item_ref=item_ref
        )

    async def record_inventory_flag(
        self,
        *,
        campaign_id: str,
        turn_id: str | None,
        op_json: str,
        flag_reason: str,
        created_at: str,
    ) -> str:
        return await self._inventory.record_flag(
            campaign_id=campaign_id,
            turn_id=turn_id,
            op_json=op_json,
            flag_reason=flag_reason,
            created_at=created_at,
        )

    async def list_inventory_flags(self, campaign_id: str, *, resolved: bool) -> list[dict]:
        return await self._inventory.list_flags(campaign_id, resolved=resolved)

    async def resolve_inventory_flag(self, campaign_id: str, flag_id: str) -> None:
        await self._inventory.resolve_flag(campaign_id, flag_id)

    async def delete_inventory_flag(self, campaign_id: str, flag_id: str) -> None:
        """Remove a flag outright — used when the apply that recorded it rolls
        back (#584), so no review row survives for an unapplied change."""
        await self._inventory.delete_flag(campaign_id, flag_id)

    async def find_item_by_name(self, campaign_id: str, name: str) -> dict | None:
        """Resolve an item name to a campaign-visible item via the content index."""
        return await self._inventory.find_item_by_name(campaign_id, name)

    async def create_emergent_item(
        self, campaign_id: str, name: str, *, source: str, turn_id: str | None = None
    ) -> str:
        return await self._inventory.create_emergent_item(
            campaign_id, name, source=source, turn_id=turn_id
        )

    async def campaign_exists(self, campaign_id: str) -> bool:
        row = await self.db.fetchone("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,))
        return row is not None

    async def count_deltas(self, campaign_id: str) -> int:
        return await self._delta_ops.count_deltas(campaign_id)

    # ------------------------------------------------------------------
    # Composition-aware resolve cascade
    # ------------------------------------------------------------------

    async def resolve_entity(
        self,
        *,
        campaign_id: str,
        kind: str,
        asset_id: str,
        world_id: str | None = None,
    ) -> dict | None:
        """Cascade: base (snapshot/live) → variant overlay → campaign override.

        ``world_id=None`` means try campaign emergent first (the entity is
        campaign-local). Pinned vs live is determined per world ref. For
        characters, the campaign's selected variant diff (``variants:`` in
        ``campaign.yaml``) is applied on the base before any override.
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

        base = await self._resolve_world_base(
            campaign_id=campaign_id,
            world_id=world_id,
            library_id=library_id,
        )
        if base is not None and kind == "character":
            base = await self._apply_variant_overlay(
                campaign_id=campaign_id,
                world_id=world_id,
                asset_id=asset_id,
                library_id=library_id,
                base=base,
            )

        override = await self.get_override(campaign_id, library_id)
        if override is not None:
            merged = dict(base or {})
            merged["frontmatter"] = _merge_overlay(dict(merged.get("frontmatter") or {}), override)
            merged["source"] = "campaign-override"
            merged["override"] = override
            return merged

        return base

    def _campaign_yaml_path(self, campaign_id: str) -> Path:
        validate_path_component(campaign_id, name="campaign_id")
        return campaigns_root(self.data_root) / campaign_id / "campaign.yaml"

    async def get_campaign_variant_selections(self, campaign_id: str) -> dict[str, str]:
        """The campaign's variant selection map from ``campaign.yaml``.

        Keys are character library ids (``worlds/<world>/characters/<id>``),
        values are variant ids. Missing file or block → empty map.
        """
        yaml_path = self._campaign_yaml_path(campaign_id)
        if not yaml_path.is_file():
            return {}
        try:
            raw = load_yaml(yaml_path) or {}
        except Exception as exc:
            logger.warning("failed to read %s for variant selections: %s", yaml_path, exc)
            return {}
        block = raw.get("variants") if isinstance(raw, dict) else None
        if not isinstance(block, dict):
            return {}
        return {str(k): str(v) for k, v in block.items() if v}

    async def set_campaign_variant_selections(
        self, campaign_id: str, selections: dict[str, str]
    ) -> dict[str, str]:
        """Replace the ``variants:`` map in ``campaign.yaml`` (file is SSOT).

        An empty map removes the block. A missing ``campaign.yaml`` is created
        (mirroring how the LLM gateway persists tier routes) so campaigns
        whose file hasn't materialized yet can still pin variants. Emits a
        ``library_entity_changed`` event (kind ``character_variant``) when the
        selection actually changes so cached character views re-render with
        the newly selected portrayal.
        """
        yaml_path = self._campaign_yaml_path(campaign_id)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        raw = (load_yaml(yaml_path) or {}) if yaml_path.is_file() else {"id": campaign_id}
        if not isinstance(raw, dict):
            raise StateStoreError(f"{yaml_path}: top-level YAML must be a mapping")
        previous_block = raw.get("variants")
        previous = (
            {str(k): str(v) for k, v in previous_block.items() if v}
            if isinstance(previous_block, dict)
            else {}
        )
        cleaned = {str(k): str(v) for k, v in selections.items() if v}
        if cleaned:
            raw["variants"] = cleaned
        else:
            raw.pop("variants", None)
        write_yaml(yaml_path, raw)

        changed = sorted(
            k for k in previous.keys() | cleaned.keys() if previous.get(k) != cleaned.get(k)
        )
        if changed and self._bus is not None:
            await self._bus.emit(
                Event(
                    type="library_entity_changed",
                    payload={
                        "kind": "character_variant",
                        "campaign_id": campaign_id,
                        "library_ids": changed,
                    },
                )
            )
        return cleaned

    def _selected_variant_id(
        self, campaign_id: str, library_id: str
    ) -> tuple[str | None, str | None]:
        """Read the campaign's variant selection for one character.

        ``campaign.yaml`` is the source of truth::

            variants:
              worlds/<world>/characters/<id>: <variant-id>

        Returns ``(variant_id, error)``. ``error`` is set when the file exists
        but can't be read — the caller can't know whether a selection was
        present, so it must surface the breakage rather than treat it as
        "no selection".
        """
        yaml_path = self._campaign_yaml_path(campaign_id)
        if not yaml_path.is_file():
            return None, None
        try:
            raw = load_yaml(yaml_path) or {}
        except Exception as exc:
            logger.warning("failed to read %s for variant selection: %s", yaml_path, exc)
            return None, f"unreadable campaign.yaml: {exc}"
        block = raw.get("variants") if isinstance(raw, dict) else None
        if not isinstance(block, dict):
            return None, None
        value = block.get(library_id)
        return (str(value) if value else None), None

    async def _apply_variant_overlay(
        self,
        *,
        campaign_id: str,
        world_id: str,
        asset_id: str,
        library_id: str,
        base: dict,
    ) -> dict:
        """Apply the campaign's selected variant diff on top of a character base.

        Degraded reads (unreadable campaign.yaml, dangling selection,
        unparseable overlay) log a warning and fall back to the base so a bad
        overlay never hides the character — but they mark the result with
        ``variant_error`` so resolution surfaces "broken" distinctly from
        "no selection" (it shows up in ``ResolvedEntity.overrides_applied``).
        """
        variant_id, selection_error = self._selected_variant_id(campaign_id, library_id)
        if selection_error is not None:
            return {**base, "variant_error": selection_error}
        if not variant_id:
            return base
        try:
            variant = await self.get_character_variant(world_id, asset_id, variant_id)
        except Exception as exc:
            logger.warning(
                "campaign %s: failed to read variant %r of %s: %s",
                campaign_id,
                variant_id,
                library_id,
                exc,
            )
            return {**base, "variant_error": f"unreadable variant {variant_id!r}: {exc}"}
        if variant is None:
            logger.warning(
                "campaign %s selects missing variant %r of %s",
                campaign_id,
                variant_id,
                library_id,
            )
            return {**base, "variant_error": f"missing variant {variant_id!r}"}
        overlay = {
            k: v
            for k, v in (variant.get("frontmatter") or {}).items()
            if k not in _VARIANT_RESERVED_KEYS
        }
        merged = dict(base)
        merged["frontmatter"] = _merge_overlay(dict(merged.get("frontmatter") or {}), overlay)
        if (variant.get("body") or "").strip():
            merged["body"] = variant["body"]
        merged["variant"] = variant_id
        return merged

    async def _resolve_world_base(
        self,
        *,
        campaign_id: str,
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
            snap = await self.db.fetchone(
                """
                SELECT version, frontmatter, body FROM library_snapshots
                WHERE campaign_id = ? AND library_id = ?
                LIMIT 1
                """,
                (campaign_id, library_id),
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
                    world_id=world_id,
                    include=list(include) if include else None,
                )
            else:
                await remove_snapshots_for_world(
                    conn,
                    campaign_id=campaign_id,
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
        role_tags: list[str] | None = None,
    ) -> None:
        import json as _json

        # First PC in the campaign becomes active; subsequent PCs are added
        # inactive so at most one row per campaign has active=1. set_active_pc
        # is the only place that flips the bit afterwards.
        row = await self.db.fetchone(
            "SELECT 1 FROM campaign_pcs WHERE campaign_id = ? LIMIT 1",
            (campaign_id,),
        )
        active_default = 0 if row else 1
        tags_json = _json.dumps(role_tags) if role_tags is not None else "[]"
        # When role_tags was explicitly provided, update it on conflict;
        # otherwise preserve the existing value so callers that only refresh
        # display_name/owner don't silently erase campaign-scoped tags.
        if role_tags is not None:
            conflict_clause = """
              display_name = excluded.display_name,
              owner = excluded.owner,
              role_tags = excluded.role_tags
            """
        else:
            conflict_clause = """
              display_name = excluded.display_name,
              owner = excluded.owner
            """
        await self.db.execute(
            f"""
            INSERT INTO campaign_pcs (
              campaign_id, character_ref, display_name, owner, active, added_at, role_tags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, character_ref) DO UPDATE SET
            {conflict_clause}
            """,
            (
                campaign_id,
                character_ref,
                display_name,
                owner,
                active_default,
                now_iso(),
                tags_json,
            ),
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

    async def resolve_character_state(
        self,
        *,
        character_ref: str,
        campaign_id: str,
    ) -> dict | None:
        """Look up a single character's state row."""
        row = await self.db.fetchone(
            "SELECT * FROM character_state WHERE character_ref = ? AND campaign_id = ?",
            (character_ref, campaign_id),
        )
        return _character_state_row_to_dict(row) if row is not None else None

    async def list_tier_pins(
        self,
        *,
        campaign_id: str,
    ) -> dict[str, str]:
        """Return ``{character_ref: tier_pin}`` for the campaign."""
        rows = await self.db.fetchall(
            """
            SELECT character_ref, tier_pin FROM character_state
            WHERE campaign_id = ? AND tier_pin IS NOT NULL
            """,
            (campaign_id,),
        )
        return {row["character_ref"]: row["tier_pin"] for row in rows}

    # ------------------------------------------------------------------
    # Delta log / delta sets / review queue (delegated to DeltaOps)
    # ------------------------------------------------------------------

    async def apply_delta(
        self,
        *,
        delta: dict | Any,
        source: str | None = None,
        turn_id: str | None = None,
        campaign_id: str | None = None,
        delta_set_id: str | None = None,
    ) -> str:
        """Apply a delta to the SQLite layer and record it in ``deltas``."""
        return await self._delta_ops.apply_delta(
            delta=delta,
            source=source,
            turn_id=turn_id,
            campaign_id=campaign_id,
            delta_set_id=delta_set_id,
        )

    async def reverse_delta(self, delta_id: str) -> None:
        await self._delta_ops.reverse_delta(delta_id)

    async def apply_delta_set(
        self,
        *,
        deltas: list[Any],
        delta_set_id: str,
        campaign_id: str | None,
        turn_id: str | None,
        source: str,
    ) -> list[DeltaRecord]:
        """Apply every delta atomically, tagging each with ``delta_set_id``."""
        return await self._delta_ops.apply_delta_set(
            deltas=deltas,
            delta_set_id=delta_set_id,
            campaign_id=campaign_id,
            turn_id=turn_id,
            source=source,
        )

    async def rewind_delta_set(
        self,
        delta_set_id: str,
        *,
        campaign_id: str,
    ) -> list[DeltaRecord]:
        """LIFO-reverse every non-reversed delta tagged with ``delta_set_id``."""
        return await self._delta_ops.rewind_delta_set(delta_set_id, campaign_id=campaign_id)

    async def re_activate_delta_set(
        self,
        *,
        delta_set_id: str,
        campaign_id: str,
    ) -> int:
        """Re-apply every previously-reversed delta in a set (oldest first)."""
        return await self._delta_ops.re_activate_delta_set(
            delta_set_id=delta_set_id, campaign_id=campaign_id
        )

    async def swap_delta_set(
        self,
        *,
        rewind_set_id: str,
        apply_deltas: list[Any] | None,
        apply_set_id: str,
        campaign_id: str,
        turn_id: str | None,
        source: str,
    ) -> SwapResult:
        """Atomic rewind of one set followed by application of another."""
        return await self._delta_ops.swap_delta_set(
            rewind_set_id=rewind_set_id,
            apply_deltas=apply_deltas,
            apply_set_id=apply_set_id,
            campaign_id=campaign_id,
            turn_id=turn_id,
            source=source,
        )

    async def swap_turn_deltas(
        self,
        *,
        campaign_id: str,
        turn_id: str | None,
        deltas: list[Any],
        source: str,
        review_deltas: list[Any] | None = None,
    ) -> SwapResult:
        """Atomically replace a turn's recorded effects with re-extracted ones.

        See :meth:`DeltaOps.swap_turn_deltas` for the transaction semantics
        (#583).
        """
        return await self._delta_ops.swap_turn_deltas(
            campaign_id=campaign_id,
            turn_id=turn_id,
            deltas=deltas,
            source=source,
            review_deltas=review_deltas,
        )

    async def set_current_alternate_delta_set(
        self,
        *,
        campaign_id: str,
        post_id: str,
        delta_set_id: str,
    ) -> None:
        """Record which delta set is the current primary for ``post_id``."""
        await self._delta_ops.set_current_alternate_delta_set(
            campaign_id=campaign_id, post_id=post_id, delta_set_id=delta_set_id
        )

    async def clear_current_alternate_delta_set(
        self,
        *,
        campaign_id: str,
        post_id: str,
    ) -> None:
        await self._delta_ops.clear_current_alternate_delta_set(
            campaign_id=campaign_id, post_id=post_id
        )

    async def current_delta_set_for(
        self,
        *,
        post_id: str | None,
        campaign_id: str,
        set_id: str | None = None,
    ) -> str | None:
        """Look up the primary delta set for ``post_id``."""
        return await self._delta_ops.current_delta_set_for(
            post_id=post_id, campaign_id=campaign_id, set_id=set_id
        )

    async def queue_for_review(
        self,
        *,
        delta: dict | Any,
        source: str | None = None,
        campaign_id: str | None = None,
    ) -> str:
        """Persist a low-confidence delta for human review without applying it."""
        return await self._delta_ops.queue_for_review(
            delta=delta, source=source, campaign_id=campaign_id
        )

    async def approve_review_item(self, review_id: str) -> str:
        """Apply a previously-queued delta. Returns the delta id."""
        return await self._delta_ops.approve_review_item(review_id)

    async def reject_review_item(self, review_id: str, *, notes: str = "") -> None:
        await self._delta_ops.reject_review_item(review_id, notes=notes)

    async def pending_review_delta_ids(self, campaign_id: str) -> set[str]:
        """Delta ids that are queued for review but not yet applied."""
        return await self._delta_ops.pending_review_delta_ids(campaign_id)

    async def pending_review_items(self, campaign_id: str) -> list[tuple[str, str | None]]:
        """``(review_id, turn_id)`` for each pending review item in the campaign."""
        return await self._delta_ops.pending_review_items(campaign_id)

    async def get_delta_log(
        self,
        *,
        campaign_id: str | None = None,
        since: datetime | str | None = None,
        turn_id: str | None = None,
        include_reversed: bool = True,
        limit: int | None = None,
    ) -> list[DeltaRecord]:
        return await self._delta_ops.get_delta_log(
            campaign_id=campaign_id,
            since=since,
            turn_id=turn_id,
            include_reversed=include_reversed,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Embeddings + search (delegated to SearchStore)
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
        return await self._search.add_embedding(
            ref=ref,
            scope=scope,
            source_kind=source_kind,
            text=text,
            vector=vector,
            model=model,
            campaign_id=campaign_id,
        )

    async def delete_embeddings(self, ref: str) -> int:
        return await self._search.delete_embeddings(ref)

    async def vector_search(
        self,
        *,
        query_vector: list[float],
        campaign_id: str,
        source_kinds: list[str] | None = None,
        include_library: bool = True,
        top_k: int = 8,
    ) -> list[SearchHit]:
        return await self._search.vector_search(
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
        kinds: Iterable[str] = ("fact",),
        top_k: int = 5,
        include_retired: bool = False,
    ) -> list[SearchHit]:
        return await self._search.keyword_search(
            query=query,
            campaign_id=campaign_id,
            kinds=kinds,
            top_k=top_k,
            include_retired=include_retired,
        )

    # ------------------------------------------------------------------
    # Context inspector pins / excludes (delegated to ContextPinStore)
    # ------------------------------------------------------------------

    async def write_context_pin(
        self,
        *,
        campaign_id: str,
        kind: str,
        target_source_id: str | None = None,
        target_entity_kind: str | None = None,
        target_entity_id: str | None = None,
        created_at_turn_id: str | None = None,
        ttl_turns: int | None = None,
        created_by: str = "user",
        pin_id: str | None = None,
    ) -> str:
        """Insert a context pin/exclude row (see :meth:`ContextPinStore.write_pin`)."""
        return await self._context_pins.write_pin(
            campaign_id=campaign_id,
            kind=kind,
            target_source_id=target_source_id,
            target_entity_kind=target_entity_kind,
            target_entity_id=target_entity_id,
            created_at_turn_id=created_at_turn_id,
            ttl_turns=ttl_turns,
            created_by=created_by,
            pin_id=pin_id,
        )

    async def list_active_context_pins(
        self,
        *,
        campaign_id: str,
        current_turn_id: str | None = None,
    ) -> list[dict]:
        """Return every active (uncleared, TTL-unexpired) pin/exclude."""
        return await self._context_pins.list_active(
            campaign_id=campaign_id, current_turn_id=current_turn_id
        )

    async def mark_context_pin_cleared(
        self,
        *,
        pin_id: str,
        cleared_by: str = "user",
    ) -> None:
        await self._context_pins.mark_cleared(pin_id=pin_id, cleared_by=cleared_by)


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
