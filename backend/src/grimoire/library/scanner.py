"""Library scanning and version diffing collaborator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.library.errors import LibraryNotFoundError
from grimoire.types.composition import (
    ResolvedEntity,
    UpgradeEntityChange,
    UpgradePreview,
    UpgradeReport,
)

if TYPE_CHECKING:
    from grimoire.state_store import StateStore


class LibraryScanner:
    """World diff, upgrade preview, and entity resolution."""

    def __init__(self, *, store: StateStore, service: Any = None) -> None:
        self._store = store
        self._service = service

    async def world_diff(
        self,
        world_id: str,
        from_version: int,
        to_version: int | None = None,
    ) -> dict[str, Any]:
        from grimoire.library.service import _normalize_row

        await self._service.get_world(world_id)
        max_row = await self._store.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        current_max = int((max_row["v"] if max_row else 0) or 0)
        if to_version is None:
            to_version = current_max
        effective_to = min(to_version, current_max)

        rows = await self._store.db.fetchall(
            "SELECT id, kind, asset_id, name, frontmatter, body, version, world_id "
            "FROM library_index WHERE world_id = ? AND kind != 'world' "
            "ORDER BY kind, asset_id",
            (world_id,),
        )

        changed: list[dict[str, Any]] = []
        for raw in rows:
            row = _normalize_row(raw)
            version = int(row.get("version") or 0)
            if version <= from_version:
                continue
            path = f"{row.get('kind')}/{row.get('asset_id')}"
            after = {
                "name": row.get("name") or row.get("asset_id"),
                "frontmatter": row.get("frontmatter") or {},
                "body": row.get("body") or "",
                "version": version,
            }
            changed.append({"path": path, "before": None, "after": after})

        return {
            "world_id": world_id,
            "from_version": int(from_version),
            "to_version": int(effective_to),
            "added": [],
            "removed": [],
            "changed": changed,
        }

    async def preview_upgrade_world_ref(
        self,
        campaign_id: str,
        world_id: str,
    ) -> UpgradePreview:
        from grimoire.util import maybe_json

        camp_row = await self._store.db.fetchone(
            """
            SELECT bound_at_version, track_latest FROM campaign_world_refs
            WHERE campaign_id = ? AND world_id = ?
            """,
            (campaign_id, world_id),
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} does not bind world {world_id!r}")
        from_version = int(camp_row["bound_at_version"] or 0)
        live_rows = await self._store.list_library_in_world(world_id)
        live_by_id = {row["id"]: row for row in live_rows}

        snap_rows = await self._store.db.fetchall(
            """
            SELECT s.library_id AS library_id, s.version AS version,
                   s.frontmatter AS frontmatter, s.body AS body
            FROM library_snapshots s
            JOIN library_index i ON i.id = s.library_id
            WHERE s.campaign_id = ? AND i.world_id = ?
            """,
            (campaign_id, world_id),
        )
        snap_by_id = {row["library_id"]: row for row in snap_rows}

        max_row = await self._store.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        to_version = int((max_row["v"] if max_row else 0) or 0)

        entries: list[UpgradeEntityChange] = []
        changed: list[str] = []
        added: list[str] = []
        removed: list[str] = []
        for lib_id in sorted(set(live_by_id) | set(snap_by_id)):
            live = live_by_id.get(lib_id)
            snap = snap_by_id.get(lib_id)
            before_version = int(snap["version"]) if snap else None
            after_version = int(live["version"]) if live else None
            if snap is None and live is not None:
                added.append(lib_id)
            elif live is None and snap is not None:
                removed.append(lib_id)
            elif snap is not None and live is not None and before_version != after_version:
                changed.append(lib_id)
            else:
                continue
            entries.append(
                UpgradeEntityChange(
                    library_id=lib_id,
                    before_version=before_version,
                    after_version=after_version,
                    before_frontmatter=maybe_json(snap["frontmatter"]) if snap else None,
                    after_frontmatter=(live.get("frontmatter") if live else None),
                    before_body=(snap["body"] if snap and snap["body"] else None),
                    after_body=(live.get("body") if live else None),
                )
            )

        return UpgradePreview(
            campaign_id=campaign_id,
            world_id=world_id,
            from_version=from_version,
            to_version=to_version,
            changed_entities=changed,
            added_entities=added,
            removed_entities=removed,
            entries=entries,
        )

    async def upgrade_world_ref(self, campaign_id: str, world_id: str) -> UpgradeReport:
        before_max = await self._store.db.fetchone(
            "SELECT bound_at_version FROM campaign_world_refs "
            "WHERE campaign_id = ? AND world_id = ?",
            (campaign_id, world_id),
        )
        if before_max is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} does not bind world {world_id!r}")
        from_version = int(before_max["bound_at_version"] or 0)

        report = await self._store.upgrade_world_ref(campaign_id=campaign_id, world_id=world_id)
        max_row = await self._store.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        to_version = int((max_row["v"] if max_row else 0) or 0)
        return UpgradeReport(
            campaign_id=campaign_id,
            world_id=world_id,
            from_version=from_version,
            to_version=to_version,
            changed_entities=sorted(report.diff.keys()),
            diff=report.diff,
        )

    async def resolve(self, entity_id: str, campaign_id: str) -> ResolvedEntity:
        from grimoire.library.service import (
            _build_resolved,
            _parse_resolve_ref,
        )

        target_kind, world_id, asset_id, is_emergent_only = _parse_resolve_ref(entity_id)

        if is_emergent_only:
            data = await self._store.resolve_entity(
                campaign_id=campaign_id,
                kind=target_kind,
                asset_id=asset_id,
                world_id=None,
            )
            if data is None:
                raise LibraryNotFoundError(
                    f"emergent {target_kind}/{asset_id} not found in campaign {campaign_id!r}"
                )
            return _build_resolved(target_kind, world_id, asset_id, data)

        emergent = await self._store.get_emergent(campaign_id, target_kind, asset_id)
        if emergent is not None:
            data = {
                "source": "campaign-emergent",
                "frontmatter": emergent.get("frontmatter") or {},
                "body": emergent.get("body") or "",
            }
            return _build_resolved(target_kind, world_id, asset_id, data)

        data = await self._store.resolve_entity(
            campaign_id=campaign_id,
            kind=target_kind,
            asset_id=asset_id,
            world_id=world_id,
        )
        if data is None:
            raise LibraryNotFoundError(f"cannot resolve {entity_id!r} for campaign {campaign_id!r}")
        return _build_resolved(target_kind, world_id, asset_id, data)
