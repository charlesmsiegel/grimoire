"""Composition management collaborator for LibraryService."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire import events
from grimoire.library.errors import LibraryError, LibraryNotFoundError, PromotionError
from grimoire.state_store.indexers import make_library_id
from grimoire.state_store.paths import parse_library_id
from grimoire.types.common import EntityKind
from grimoire.types.composition import CampaignRef, Composition, LibraryEntity, WorldRef
from grimoire.util import json_equal, maybe_json

if TYPE_CHECKING:
    from grimoire.event_bus import EventBus
    from grimoire.library.config import LibraryConfig
    from grimoire.state_store import StateStore


def _normalize_kind(kind: EntityKind | str) -> str:
    from grimoire.library.service import _normalize_kind as _nk

    return _nk(kind)


def _include_to_kinds(include: list[str] | None) -> set[str] | None:
    from grimoire.library.service import _include_to_kinds as _itk

    return _itk(include)


_World_ENTITY_KINDS: frozenset[str] = frozenset(
    {"character", "item", "location", "lore", "faction", "greeting", "monster"}
)


class CompositionManager:
    """Composition, promotion, demotion, and save-back-to-library."""

    def __init__(
        self,
        *,
        store: StateStore,
        config: LibraryConfig,
        event_bus: EventBus | None = None,
        service: Any = None,
    ) -> None:
        self._store = store
        self._config = config
        self._event_bus = event_bus
        self._service = service

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        from grimoire.event_bus import Event

        await self._event_bus.emit(Event(type=event_type, payload=payload))

    async def get_composition(self, campaign_id: str) -> Composition:
        camp_row = await self._store.db.fetchone(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} not found")

        refs_raw = await self._store.list_world_refs(campaign_id)
        refs = [
            WorldRef(
                world_id=r["world_id"],
                priority=int(r["priority"]),
                include=list(r.get("include") or []),
                bound_at_version=int(r.get("bound_at_version") or 0),
                track_latest=bool(r.get("track_latest")),
            )
            for r in refs_raw
        ]
        config = maybe_json(camp_row["config"]) or {}
        return Composition(
            worlds=refs,
            mechanics=camp_row["mechanics_module"],
            style_guide_id=camp_row["style_guide_id"],
            image_preset_id=camp_row["image_preset_id"],
            inline_style_guide=camp_row["inline_style_guide"],
            content_boundaries=camp_row["content_boundaries"],
            calendar_ids=list(config.get("calendar_ids") or []),
            holiday_set_ids=list(config.get("holiday_set_ids") or []),
            display_calendar_id=config.get("display_calendar_id"),
        )

    async def set_composition(self, campaign_id: str, composition: Composition) -> None:
        camp_row = await self._store.db.fetchone(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} not found")

        config = maybe_json(camp_row["config"]) or {}
        if composition.calendar_ids or "calendar_ids" in config:
            config["calendar_ids"] = list(composition.calendar_ids)
        if composition.holiday_set_ids or "holiday_set_ids" in config:
            config["holiday_set_ids"] = list(composition.holiday_set_ids)
        if composition.display_calendar_id is not None or "display_calendar_id" in config:
            config["display_calendar_id"] = composition.display_calendar_id

        await self._store.upsert_campaign(
            campaign_id=campaign_id,
            name=camp_row["name"],
            description=camp_row["description"],
            mechanics_module=composition.mechanics,
            style_guide_id=composition.style_guide_id,
            image_preset_id=composition.image_preset_id,
            inline_style_guide=composition.inline_style_guide,
            content_boundaries=composition.content_boundaries,
            greeting_id=camp_row["greeting_id"],
            config=config if config else None,
        )

        existing = {r["world_id"] for r in await self._store.list_world_refs(campaign_id)}
        desired_ids: set[str] = set()
        for ref in composition.worlds:
            desired_ids.add(ref.world_id)
            await self._store.upsert_world_ref(
                campaign_id=campaign_id,
                world_id=ref.world_id,
                priority=ref.priority,
                include=list(ref.include or []),
                track_latest=ref.track_latest,
                bound_at_version=(ref.bound_at_version if ref.bound_at_version else None),
                snapshot_on_bind=self._config.version_pinning.snapshot_on_bind,
            )

        for old in existing - desired_ids:
            await self._store.db.execute(
                "DELETE FROM campaign_world_refs WHERE campaign_id = ? AND world_id = ?",
                (campaign_id, old),
            )
            await self._store.db.execute(
                """
                DELETE FROM library_snapshots
                WHERE campaign_id = ? AND library_id IN (
                  SELECT id FROM library_index WHERE world_id = ?
                )
                """,
                (campaign_id, old),
            )

    async def promote_to_library(
        self,
        campaign_id: str,
        entity_kind: EntityKind | str,
        campaign_entity_id: str,
        target_world_id: str,
        *,
        source: str = "user",
    ) -> str:
        normalized = _normalize_kind(entity_kind)
        if normalized not in _World_ENTITY_KINDS:
            raise PromotionError(
                f"cannot promote kind {normalized!r}; only world-scoped kinds supported"
            )
        emergent = await self._store.get_emergent(campaign_id, normalized, campaign_entity_id)
        if emergent is None:
            raise PromotionError(
                f"no emergent {normalized}/{campaign_entity_id} in campaign {campaign_id!r}"
            )
        frontmatter = dict(emergent.get("frontmatter") or {})
        frontmatter.setdefault("id", campaign_entity_id)
        body = emergent.get("body") or ""
        library_id = make_library_id(target_world_id, normalized, campaign_entity_id)
        result = await self._store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body=body,
            source=f"{source}:promotion",
            campaign_id=campaign_id,
        )

        cleanup_action, body_diverged = await self._cleanup_promoted_emergent(
            campaign_id=campaign_id,
            kind=normalized,
            entity_id=campaign_entity_id,
            target_world_id=target_world_id,
            promoted_frontmatter=frontmatter,
            promoted_body=body,
            source=source,
        )
        await self._rekey_embeddings_after_promotion(
            campaign_id=campaign_id,
            kind=normalized,
            entity_id=campaign_entity_id,
            library_id=library_id,
        )
        await self._emit(
            events.LIBRARY_ENTITY_PROMOTED,
            {
                "campaign_id": campaign_id,
                "kind": normalized,
                "entity_id": campaign_entity_id,
                "target_world_id": target_world_id,
                "library_id": library_id,
                "library_path": str(result.path),
                "cleanup": cleanup_action,
                "body_diverged": body_diverged,
            },
        )
        return str(result.path)

    async def _cleanup_promoted_emergent(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        target_world_id: str,
        promoted_frontmatter: dict,
        promoted_body: str,
        source: str,
    ) -> tuple[str, bool]:
        current = await self._store.get_emergent(campaign_id, kind, entity_id)
        if current is None:
            return ("missing", False)

        current_fm = current.get("frontmatter") or {}
        current_body = current.get("body") or ""
        fm_match = json_equal(current_fm, promoted_frontmatter)
        body_match = current_body == promoted_body

        if fm_match and body_match:
            await self._store.delete_emergent(
                campaign_id=campaign_id,
                kind=kind,
                entity_id=entity_id,
                source=f"{source}:promotion-cleanup",
            )
            return ("deleted", False)

        if not fm_match:
            diff = _frontmatter_diff(current_fm, promoted_frontmatter)
            if diff:
                library_id = make_library_id(target_world_id, kind, entity_id)
                await self._store.write_override(
                    campaign_id=campaign_id,
                    library_id=library_id,
                    patch=diff,
                    source=f"{source}:promotion-override",
                )
        await self._store.delete_emergent(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            source=f"{source}:promotion-cleanup",
        )
        return ("override+deleted", not body_match)

    async def _rekey_embeddings_after_promotion(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        library_id: str,
    ) -> None:
        emergent_ref = f"campaigns/{campaign_id}/emergent/{kind}/{entity_id}"
        try:
            await self._store.delete_embeddings(emergent_ref)
        except Exception:
            return

    async def demote(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        *,
        copy_down_to: list[str] | None = None,
        source: str = "user",
    ) -> list[CampaignRef]:
        normalized = _normalize_kind(kind)
        if normalized not in _World_ENTITY_KINDS:
            raise LibraryError(
                f"cannot demote kind {normalized!r}; only world-scoped kinds supported"
            )
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self._store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(
                f"cannot demote missing entity {kind}/{entity_id} in {world_id!r}"
            )

        dependents = await self.dependents(world_id, normalized, entity_id)
        copy_down_to = copy_down_to or []
        copied: list[str] = []
        for campaign_id in copy_down_to:
            await self._store.write_emergent(
                campaign_id=campaign_id,
                kind=normalized,
                entity_id=entity_id,
                frontmatter=row.get("frontmatter") or {},
                body=row.get("body") or "",
                source=f"{source}:demote-copy-down",
            )
            copied.append(campaign_id)

        await self._store.delete_library_file(library_id=library_id, source=source)

        await self._emit(
            events.LIBRARY_ENTITY_DEMOTED,
            {
                "world_id": world_id,
                "kind": normalized,
                "entity_id": entity_id,
                "library_id": library_id,
                "dependents": [d.id for d in dependents],
                "copied_down_to": copied,
            },
        )
        return dependents

    async def preview_save_override_to_library(
        self,
        campaign_id: str,
        library_id: str,
    ) -> dict[str, Any]:
        ref = parse_library_id(library_id)
        if ref.world_id is None:
            raise LibraryError(f"library_id {library_id!r} is not world-scoped")
        before_row = await self._store.get_library_entity(library_id)
        if before_row is None:
            raise LibraryNotFoundError(f"library entity {library_id!r} does not exist")
        override = await self._store.get_override(campaign_id, library_id)
        if not override:
            raise LibraryError(f"no override on {library_id!r} for campaign {campaign_id!r}")
        merged_fm = dict(before_row.get("frontmatter") or {})
        merged_fm.update(override)
        return {
            "library_id": library_id,
            "before": {
                "frontmatter": before_row.get("frontmatter") or {},
                "body": before_row.get("body") or "",
                "version": int(before_row.get("version") or 0),
            },
            "after": {
                "frontmatter": merged_fm,
                "body": before_row.get("body") or "",
            },
        }

    async def save_override_to_library(
        self,
        campaign_id: str,
        library_id: str,
        *,
        source: str = "user",
    ) -> dict[str, Any]:
        preview = await self.preview_save_override_to_library(campaign_id, library_id)
        result = await self._store.write_library_file(
            library_id=library_id,
            frontmatter=preview["after"]["frontmatter"],
            body=preview["after"]["body"],
            source=f"{source}:save-back-from-override",
            campaign_id=campaign_id,
        )
        await self._store.delete_override(
            campaign_id=campaign_id,
            library_id=library_id,
            source=f"{source}:save-back-cleanup",
        )
        await self._emit(
            events.LIBRARY_ENTITY_SAVE_BACK,
            {
                "campaign_id": campaign_id,
                "library_id": library_id,
                "from_version": preview["before"]["version"],
                "to_version": result.version,
            },
        )
        return {
            "library_id": library_id,
            "before": preview["before"],
            "after": {**preview["after"], "version": result.version},
        }

    async def dependents(
        self, world_id: str, kind: EntityKind | str, entity_id: str
    ) -> list[CampaignRef]:
        rows = await self._store.db.fetchall(
            """
            SELECT c.id AS id, c.name AS name
            FROM campaigns c
            JOIN campaign_world_refs r ON r.campaign_id = c.id
            WHERE r.world_id = ?
            ORDER BY c.name
            """,
            (world_id,),
        )
        return [CampaignRef(id=row["id"], name=row["name"]) for row in rows]

    async def list_for_composition(
        self,
        campaign_id: str,
        kind: EntityKind | str,
    ) -> list[LibraryEntity]:
        from grimoire.library.service import _entity_from_row

        normalized = _normalize_kind(kind)
        refs = await self._store.list_world_refs(campaign_id)
        refs.sort(key=lambda r: int(r.get("priority") or 0))

        seen: dict[str, LibraryEntity] = {}
        for ref in refs:
            include_kinds = _include_to_kinds(ref.get("include"))
            if include_kinds is not None and normalized not in include_kinds:
                continue
            rows = await self._rows_for_ref(campaign_id, ref, normalized)
            for row in rows:
                asset = row["asset_id"]
                if asset in seen:
                    continue
                seen[asset] = _entity_from_row(row)
        return list(seen.values())

    async def _rows_for_ref(self, campaign_id: str, ref: dict, kind: str) -> list[dict]:
        """Index-shaped rows for one world ref, honouring version pinning.

        A pinned ref (``track_latest=false``) enumerates its bind-time
        snapshot, so entities added to the live world after binding don't
        leak in and snapshotted entities survive live deletion — matching
        the per-entity resolve path, which prefers snapshots for pinned
        refs. A pinned ref bound with ``snapshot_on_bind`` disabled has no
        snapshot rows and falls back to the live index, mirroring the
        resolve path's ``library-fallback``.
        """
        world_id = ref["world_id"]
        if not ref.get("track_latest"):
            snapshot_rows = await self._store.list_snapshot_rows(campaign_id, world_id)
            if snapshot_rows:
                return [row for row in snapshot_rows if row["kind"] == kind]
        return await self._store.list_library_in_world(world_id, kind)


def _frontmatter_diff(current: dict, baseline: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in current.items():
        if not json_equal(value, baseline.get(key)):
            out[key] = value
    for key in baseline:
        if key not in current:
            out[key] = None
    return out
