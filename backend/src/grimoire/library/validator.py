"""Reclassification and validation collaborator for LibraryService."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire import events
from grimoire.library.classify import suggest_kind
from grimoire.library.errors import ReclassificationError
from grimoire.library.reclassify import (
    ReclassificationResult,
    append_audit,
    apply_mapping,
    iter_audit,
    required_overrides_for,
)
from grimoire.state_store.indexers import make_library_id
from grimoire.types.common import EntityKind

if TYPE_CHECKING:
    from grimoire.event_bus import EventBus
    from grimoire.state_store import StateStore


class LibraryValidator:
    """Reclassification logic for lore entries."""

    def __init__(
        self,
        *,
        store: StateStore,
        config: Any,
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

    async def preview_reclassification(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: EntityKind | str,
    ) -> dict[str, Any]:
        from grimoire.library.service import _lore_from_entity

        target = self._coerce_reclassify_target(target_kind)
        source_entity = await self._service.get_entity(world_id, "lore", source_id)
        lore = _lore_from_entity(source_entity)
        fm, body, kept, dropped, into_notes, warnings = apply_mapping(lore, target, None)
        suggestion = suggest_kind(
            lore, threshold=self._config.reclassification.suggestion_threshold
        )
        return {
            "source_id": source_id,
            "target_kind": target.value,
            "frontmatter": fm,
            "body": body,
            "kept": kept,
            "dropped": dropped,
            "into_notes": into_notes,
            "warnings": warnings,
            "required_overrides": required_overrides_for(target),
            "suggestion": {
                "kind": suggestion.kind.value,
                "confidence": suggestion.confidence,
                "reason": suggestion.reason,
            },
        }

    async def reclassify_entity(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: EntityKind | str,
        overrides: dict[str, Any] | None = None,
        actor: str = "user",
    ) -> ReclassificationResult:
        from grimoire.library.service import _lore_from_entity, _slugify

        target = self._coerce_reclassify_target(target_kind)
        overrides = dict(overrides or {})

        missing = [
            key
            for key in required_overrides_for(target)
            if key not in overrides or overrides[key] in (None, "")
        ]
        if missing:
            raise ReclassificationError(
                f"missing required override(s) for {target.value}: {missing!r}"
            )

        source_entity = await self._service.get_entity(world_id, "lore", source_id)
        lore = _lore_from_entity(source_entity)
        fm, body, kept, dropped, into_notes, warnings = apply_mapping(lore, target, overrides)

        derived = _slugify(fm.get("name") or source_id)
        target_id = await self._collision_suffix(world_id, target, derived)
        fm["id"] = target_id

        try:
            await self._service.create_entity(
                world_id,
                target,
                target_id,
                fm,
                body,
                source=f"{actor}:reclassify",
            )
        except Exception as exc:
            raise ReclassificationError(
                f"failed to create {target.value}/{target_id}: {exc}"
            ) from exc

        try:
            await self._service.delete_entity(
                world_id, "lore", source_id, source=f"{actor}:reclassify"
            )
        except Exception as exc:
            warnings.append(f"source not deleted: {exc}")

        try:
            append_audit(
                self._store.data_root,
                world_id=world_id,
                source_id=source_id,
                source_snapshot={
                    "frontmatter": dict(source_entity.frontmatter or {}),
                    "body": source_entity.body or "",
                },
                target_id=target_id,
                target_kind=target,
                overrides=overrides,
                actor=actor,
            )
        except Exception as exc:
            warnings.append(f"audit log write failed: {exc}")

        await self._emit(
            events.LIBRARY_RECLASSIFY,
            {
                "world_id": world_id,
                "source_id": source_id,
                "target_id": target_id,
                "target_kind": target.value,
                "actor": actor,
                "warnings": warnings,
            },
        )

        return ReclassificationResult(
            source_id=source_id,
            target_id=target_id,
            target_kind=target,
            fields_kept=kept,
            fields_dropped=dropped,
            fields_into_notes=into_notes,
            warnings=warnings,
        )

    async def _collision_suffix(
        self,
        world_id: str,
        kind: EntityKind,
        base_id: str,
    ) -> str:
        candidate = base_id
        for n in range(1, 100):
            library_id = make_library_id(world_id, kind.value, candidate)
            existing = await self._store.get_library_entity(library_id)
            if existing is None:
                return candidate
            candidate = f"{base_id}-{n + 1}"
        raise ReclassificationError(
            f"too many id collisions for {kind.value}/{base_id} (>99); refusing to write"
        )

    async def list_reclassifications(self, world_id: str) -> list[dict[str, Any]]:
        return list(iter_audit(self._store.data_root, world_id=world_id))

    async def undo_reclassification(
        self,
        world_id: str,
        timestamp: str,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        from grimoire.library.errors import LibraryNotFoundError

        target_record: dict[str, Any] | None = None
        for record in iter_audit(self._store.data_root, world_id=world_id):
            if record.get("ts") == timestamp:
                target_record = record
                break
        if target_record is None:
            raise ReclassificationError(
                f"no audit record for world {world_id!r} at ts {timestamp!r}"
            )

        original_source_id = target_record["source_id"]
        snapshot = target_record["source_snapshot"]
        target_id = target_record["target_id"]
        target_kind = EntityKind(target_record["target_kind"])

        warnings: list[str] = []

        restored_id = await self._collision_suffix(world_id, EntityKind.LORE, original_source_id)
        snapshot_fm = dict(snapshot.get("frontmatter") or {})
        snapshot_fm["id"] = restored_id
        snapshot_body = snapshot.get("body") or ""
        await self._service.create_entity(
            world_id,
            "lore",
            restored_id,
            snapshot_fm,
            snapshot_body,
            source=f"{actor}:reclassify-undo",
        )

        try:
            await self._service.delete_entity(
                world_id,
                target_kind.value,
                target_id,
                source=f"{actor}:reclassify-undo",
            )
        except LibraryNotFoundError:
            warnings.append(f"target {target_kind.value}/{target_id} already deleted")
        except Exception as exc:
            warnings.append(f"target not deleted: {exc}")

        try:
            deps = await self._service._composition.dependents(
                world_id, target_kind.value, target_id
            )
            if deps:
                warnings.append(
                    f"target was referenced by {len(deps)} campaign(s); those refs are now dangling"
                )
        except Exception:
            pass

        try:
            append_audit(
                self._store.data_root,
                world_id=world_id,
                source_id=target_id,
                source_snapshot={},
                target_id=restored_id,
                target_kind=EntityKind.LORE,
                overrides={"_undo_of": timestamp},
                actor=actor,
            )
        except Exception as exc:
            warnings.append(f"audit log write failed: {exc}")

        await self._emit(
            events.LIBRARY_RECLASSIFY_UNDO,
            {
                "world_id": world_id,
                "restored_source_id": restored_id,
                "deleted_target_id": target_id,
                "undo_of": timestamp,
                "warnings": warnings,
            },
        )

        return {
            "restored_source_id": restored_id,
            "deleted_target_id": target_id,
            "undo_of": timestamp,
            "warnings": warnings,
        }

    def _coerce_reclassify_target(self, target_kind: EntityKind | str) -> EntityKind:
        from grimoire.library.service import _normalize_kind

        if isinstance(target_kind, EntityKind):
            value = target_kind
        else:
            try:
                value = EntityKind(_normalize_kind(target_kind))
            except ValueError as exc:
                raise ReclassificationError(f"unknown target_kind {target_kind!r}") from exc
        allowed = {
            EntityKind.CHARACTER,
            EntityKind.LOCATION,
            EntityKind.FACTION,
            EntityKind.ITEM,
            EntityKind.MONSTER,
        }
        if value not in allowed:
            raise ReclassificationError(
                "reclassify target must be character/location/faction/item/monster, "
                f"got {value.value!r}"
            )
        return value
