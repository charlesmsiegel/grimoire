"""The :class:`StateStore` — write-side coordinator.

Every domain write goes through this class. The constructor wires a
:class:`grimoire.storage.Database` connection pool together with a data
root path; the methods below mediate file writes and SQLite writes so the
two halves stay coherent.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from grimoire.files import (
    ParsedDocument,
    load_yaml,
    read_markdown,
    write_markdown,
    write_yaml,
)
from grimoire.state_store.delta_log import (
    DeltaRecord,
    get_delta,
    insert_delta,
    list_deltas,
    mark_reversed,
    primary_key_columns,
    reverse_sqlite_delta,
    upsert_row,
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
    campaign_id_for_path,
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
    remove_snapshots_for_setting,
    upgrade_snapshots,
    write_snapshots_for_setting,
)
from grimoire.storage import Database


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class UpgradeReport:
    setting_id: str
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

    def __init__(self, db: Database, data_root: Path) -> None:
        self.db = db
        self.data_root = Path(data_root)

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run a block as a single SQLite transaction on a pooled connection."""
        async with self.db.acquire() as conn:
            await conn.execute("BEGIN")
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
            if ref.kind in {"setting", "image_preset"}:
                before_data = load_yaml(target) or {}
                before_payload = {"frontmatter": before_data, "body": ""}
            else:
                doc = read_markdown(target)
                before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}
        else:
            before_payload = None

        # Write the file.
        if ref.kind in {"setting", "image_preset"}:
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

        if ref.kind in {"setting", "image_preset"}:
            before_data = load_yaml(target) or {}
            before_payload = {"frontmatter": before_data, "body": ""}
        else:
            doc = read_markdown(target)
            before_payload = {"frontmatter": doc.frontmatter, "body": doc.body}

        target.unlink()

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
        if ref.setting_id is None:
            raise InvalidRefError("overrides are only valid for setting-scoped library entities")
        target = override_path(self.data_root, campaign_id, ref.setting_id, ref.kind, ref.asset_id)
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

    async def get_library_entity(self, library_id: str) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM library_index WHERE id = ?", (library_id,))
        if row is None:
            return None
        return _library_row_to_dict(row)

    async def list_library_in_setting(self, setting_id: str, kind: str | None = None) -> list[dict]:
        if kind is None:
            rows = await self.db.fetchall(
                "SELECT * FROM library_index WHERE setting_id = ? ORDER BY name",
                (setting_id,),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM library_index WHERE setting_id = ? AND kind = ? ORDER BY name",
                (setting_id, kind),
            )
        return [_library_row_to_dict(row) for row in rows]

    async def variants_of(self, asset_id: str, kind: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM library_index WHERE asset_id = ? AND kind = ? ORDER BY setting_id",
            (asset_id, kind),
        )
        return [_library_row_to_dict(row) for row in rows]

    async def get_override(self, campaign_id: str, library_id: str) -> dict | None:
        ref = parse_library_id(library_id)
        if ref.setting_id is None:
            return None
        target = override_path(self.data_root, campaign_id, ref.setting_id, ref.kind, ref.asset_id)
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

    async def list_scenes(self, campaign_id: str, branch_id: str | None = None) -> list[dict]:
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
    # Composition-aware resolve cascade
    # ------------------------------------------------------------------

    async def resolve_entity(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        kind: str,
        asset_id: str,
        setting_id: str | None = None,
    ) -> dict | None:
        """Cascade: campaign override → snapshot (pinned) / live index → None.

        ``setting_id=None`` means try campaign emergent first (the entity is
        campaign-local). Pinned vs live is determined per setting ref.
        """
        if setting_id is None:
            emergent = await self.get_emergent(campaign_id, kind, asset_id)
            if emergent is not None:
                return {
                    "source": "campaign-emergent",
                    "frontmatter": emergent["frontmatter"],
                    "body": emergent["body"],
                }
            return None

        library_id = make_library_id(setting_id, kind, asset_id)

        override = await self.get_override(campaign_id, library_id)
        if override is not None:
            base = await self._resolve_setting_base(
                campaign_id=campaign_id,
                branch_id=branch_id,
                setting_id=setting_id,
                library_id=library_id,
            )
            merged = dict(base or {})
            merged_fm = dict(merged.get("frontmatter") or {})
            merged_fm.update(override)
            merged["frontmatter"] = merged_fm
            merged["source"] = "campaign-override"
            merged["override"] = override
            return merged

        return await self._resolve_setting_base(
            campaign_id=campaign_id,
            branch_id=branch_id,
            setting_id=setting_id,
            library_id=library_id,
        )

    async def _resolve_setting_base(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        setting_id: str,
        library_id: str,
    ) -> dict | None:
        """Find the base data for a library entity, snapshot-first when pinned."""
        ref_row = await self.db.fetchone(
            """
            SELECT track_latest FROM campaign_setting_refs
            WHERE campaign_id = ? AND setting_id = ?
            """,
            (campaign_id, setting_id),
        )
        prefer_snapshot = bool(ref_row and not int(ref_row["track_latest"]))

        if prefer_snapshot:
            snap = await self.db.fetchone(
                """
                SELECT version, frontmatter, body FROM library_snapshots
                WHERE campaign_id = ? AND branch_id = ? AND library_id = ?
                """,
                (campaign_id, branch_id, library_id),
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
    # Composition (setting refs + PCs)
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
                _now_iso(),
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
            (f"{campaign_id}:main", campaign_id, _seed_for("main"), _now_iso()),
        )

    async def upsert_setting_ref(
        self,
        *,
        campaign_id: str,
        setting_id: str,
        priority: int,
        include: list[str] | None,
        track_latest: bool,
        bound_at_version: int | None = None,
    ) -> None:
        if bound_at_version is None:
            row = await self.db.fetchone(
                "SELECT MAX(version) AS v FROM library_index WHERE setting_id = ?",
                (setting_id,),
            )
            bound_at_version = int(row["v"] or 0) if row else 0

        async with self._txn() as conn:
            await conn.execute(
                """
                INSERT INTO campaign_setting_refs (
                  campaign_id, setting_id, priority, include, bound_at_version,
                  track_latest, bound_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, setting_id) DO UPDATE SET
                  priority = excluded.priority,
                  include = excluded.include,
                  bound_at_version = excluded.bound_at_version,
                  track_latest = excluded.track_latest,
                  bound_at = excluded.bound_at
                """,
                (
                    campaign_id,
                    setting_id,
                    priority,
                    json.dumps(list(include), sort_keys=True) if include is not None else None,
                    bound_at_version,
                    1 if track_latest else 0,
                    _now_iso(),
                ),
            )
            if not track_latest:
                await write_snapshots_for_setting(
                    conn,
                    campaign_id=campaign_id,
                    branch_id=f"{campaign_id}:main",
                    setting_id=setting_id,
                    include=list(include) if include else None,
                )
            else:
                await remove_snapshots_for_setting(
                    conn,
                    campaign_id=campaign_id,
                    branch_id=f"{campaign_id}:main",
                    setting_id=setting_id,
                )

    async def upgrade_setting_ref(
        self,
        *,
        campaign_id: str,
        setting_id: str,
    ) -> UpgradeReport:
        ref_row = await self.db.fetchone(
            """
            SELECT include, track_latest FROM campaign_setting_refs
            WHERE campaign_id = ? AND setting_id = ?
            """,
            (campaign_id, setting_id),
        )
        if ref_row is None:
            raise NotFoundError(f"campaign {campaign_id!r} does not bind setting {setting_id!r}")
        if int(ref_row["track_latest"]):
            return UpgradeReport(setting_id=setting_id, diff={})
        include = _json_loads(ref_row["include"]) or None

        max_row = await self.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE setting_id = ?",
            (setting_id,),
        )
        new_max = int(max_row["v"] or 0) if max_row else 0
        async with self._txn() as conn:
            diff = await upgrade_snapshots(
                conn,
                campaign_id=campaign_id,
                branch_id=f"{campaign_id}:main",
                setting_id=setting_id,
                include=include,
            )
            await conn.execute(
                """
                UPDATE campaign_setting_refs
                SET bound_at_version = ?, bound_at = ?
                WHERE campaign_id = ? AND setting_id = ?
                """,
                (new_max, _now_iso(), campaign_id, setting_id),
            )
        return UpgradeReport(setting_id=setting_id, diff=diff)

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
            (campaign_id, character_ref, display_name, owner, active_default, _now_iso()),
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
        rows = await self.db.fetchall(
            "SELECT * FROM campaign_pcs WHERE campaign_id = ? ORDER BY added_at",
            (campaign_id,),
        )
        return [dict(row) for row in rows]

    async def list_setting_refs(self, campaign_id: str) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM campaign_setting_refs WHERE campaign_id = ? ORDER BY priority",
            (campaign_id,),
        )
        return [
            {
                "campaign_id": row["campaign_id"],
                "setting_id": row["setting_id"],
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
                    _now_iso(),
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
    ) -> str:
        """Apply a delta to the SQLite layer and record it in ``deltas``.

        Accepts either a ``StateDelta`` pydantic model (from
        ``grimoire.types.state``) or a plain dict with the same keys. The
        store reads ``target_table``, ``target_id``, ``after`` and uses
        ``before`` (or auto-captures it from the current row) so reversal can
        restore the previous state.
        """
        payload = _delta_to_dict(delta)
        target_scope = payload.get("target_scope")
        target_table = payload.get("target_table")
        kind = payload.get("kind", "other")
        after = payload.get("after") or {}
        provided_before = payload.get("before")

        async with self._txn() as conn:
            captured_before: Any | None = None
            if target_scope == "campaign-sqlite":
                if not target_table:
                    raise StateStoreError("campaign-sqlite delta missing target_table")
                captured_before = await _capture_current_row(conn, target_table, after)
                # Apply the delta.
                await upsert_row(conn, table=target_table, values=after)
            elif target_scope in ("library", "campaign-file"):
                # Caller should have used a file write API; we just log.
                pass
            else:
                raise StateStoreError(
                    f"unknown target_scope {target_scope!r}; use file APIs for files"
                )

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
            )
        return delta_id

    async def reverse_delta(self, delta_id: str) -> None:
        async with self._txn() as conn:
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
                (_now_iso(), review_id),
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
                (_now_iso(), notes, review_id),
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
        embedding_id = _new_id("emb")
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
                embedded_at=_now_iso(),
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


# ---------------------------------------------------------------------------
# Row → dict helpers
# ---------------------------------------------------------------------------


def _library_row_to_dict(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "setting_id": row["setting_id"],
        "kind": row["kind"],
        "asset_id": row["asset_id"],
        "name": row["name"],
        "path": row["path"],
        "frontmatter": _json_loads(row["frontmatter"]) or {},
        "body": row["body"] or "",
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
    """Look up the current row for the PK in ``after``; ``None`` if absent."""
    pk = primary_key_columns(table)
    if pk is None:
        return None
    if not all(col in after for col in pk):
        return None
    where = " AND ".join(f"{c} = ?" for c in pk)
    params = tuple(after[c] for c in pk)
    cur = await conn.execute(f"SELECT * FROM {table} WHERE {where}", params)
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    return dict(row)


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
